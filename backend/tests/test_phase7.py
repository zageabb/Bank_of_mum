from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.phase7 as phase7
from app.database import Base
from app.ledger import append_transaction
from app.models import Account, AuditEvent, InterestRatePeriod, LedgerTransaction, Person
from app.phase7 import AccountUpdate, account_statement, annual_interest_summary, maintenance_verification, update_account


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def make_account(db, rate=Decimal("12.00")):
    person = Person(name="Phase Seven Family")
    db.add(person)
    db.flush()
    account = Account(
        person_id=person.id,
        name="Car",
        opening_principal=1000,
        annual_interest_rate=float(rate),
        regular_payment=100,
        start_date=date(2026, 1, 1),
        status="active",
    )
    db.add(account)
    db.flush()
    append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 1, 1),
        transaction_type="opening_balance",
        direction="debit",
        amount=Decimal("1000.00"),
        source="pytest",
        created_by="pytest",
    )
    db.add(InterestRatePeriod(
        account_id=account.id,
        effective_from=date(2026, 1, 1),
        annual_rate=rate,
        day_count_convention="actual_365",
        reason="pytest",
        created_by="pytest",
    ))
    db.commit()
    return person, account


def test_statement_uses_deterministic_interest_and_payment_allocation():
    engine, db = make_session()
    _, account = make_account(db)
    append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 2, 1),
        transaction_type="payment",
        direction="credit",
        amount=Decimal("100.00"),
        source="pytest",
        created_by="pytest",
    )
    db.commit()

    report = account_statement(db, account, date(2026, 1, 1), date(2026, 2, 1))
    payment = [row for row in report["transactions"] if row["type"] == "payment"][0]

    assert report["opening"]["balance"] == 0.0
    assert report["interest_accrued"] == 10.19
    assert report["interest_paid"] == 10.19
    assert report["credits"] == 100.0
    assert payment["allocated_to_interest"] == 10.19
    assert payment["allocated_to_principal"] == 89.81
    assert report["closing"]["principal"] == 910.19
    engine.dispose()


def test_annual_interest_summary_reconciles_account_totals():
    engine, db = make_session()
    _, account = make_account(db, Decimal("6.00"))
    append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 7, 1),
        transaction_type="payment",
        direction="credit",
        amount=Decimal("250.00"),
        source="pytest",
        created_by="pytest",
    )
    db.commit()

    report = annual_interest_summary(db, 2026)
    assert len(report["accounts"]) == 1
    row = report["accounts"][0]
    assert row["interest_accrued"] > 0
    assert row["interest_paid"] > 0
    assert report["totals"]["interest_accrued"] == row["interest_accrued"]
    assert report["totals"]["closing_balance"] == row["closing_balance"]
    engine.dispose()


def test_archiving_account_preserves_immutable_ledger_history():
    engine, db = make_session()
    _, account = make_account(db, Decimal("0"))
    ledger_before = db.scalar(select(func.count(LedgerTransaction.id)))

    result = update_account(
        account.id,
        AccountUpdate(name="Car", account_type="loan", regular_payment=Decimal("100.00"), status="archived", reason="pytest archive"),
        db,
    )

    assert result["account"]["status"] == "archived"
    assert db.scalar(select(func.count(LedgerTransaction.id))) == ledger_before
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "account_updated"))
    assert audit is not None
    engine.dispose()


def test_backup_snapshot_has_checksum_and_sqlite_integrity(tmp_path):
    database = tmp_path / "bank-of-mum.db"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    with create_engine(f"sqlite:///{database}").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.exec_driver_sql("INSERT INTO sample(value) VALUES ('ok')")

    old_db = phase7.DATABASE_PATH
    old_dir = phase7.BACKUP_DIR
    phase7.DATABASE_PATH = database
    phase7.BACKUP_DIR = backup_dir
    try:
        path = phase7.create_backup("pytest")
        details = phase7.validate_backup(path)
        assert details["valid"] is True
        assert details["integrity"] == "ok"
        assert len(details["sha256"]) == 64
        assert Path(path).exists()
    finally:
        phase7.DATABASE_PATH = old_db
        phase7.BACKUP_DIR = old_dir


def test_phase7_verification_checks_database_and_ledger_hashes():
    engine, db = make_session()
    make_account(db, Decimal("0"))
    result = maintenance_verification(db)
    assert result["database_integrity"] == "ok"
    assert result["counts"]["accounts"] == 1
    assert result["counts"]["transactions"] == 1
    assert result["ok"] is True
    engine.dispose()

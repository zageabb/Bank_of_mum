from datetime import date

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.ledger import account_balance, append_transaction, correct_transaction, reverse_transaction, verify_account_chain
from app.migrations import install_immutability_guards
from app.models import Account, AuditEvent, Person


def make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    install_immutability_guards(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def make_account(db):
    person = Person(name="Test Borrower")
    db.add(person)
    db.flush()
    account = Account(
        person_id=person.id,
        name="Car",
        opening_principal=1000,
        annual_interest_rate=0,
        regular_payment=200,
        start_date=date(2026, 1, 1),
        legacy_id="test-car",
    )
    db.add(account)
    db.flush()
    append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 1, 1),
        transaction_type="opening_balance",
        direction="debit",
        amount=1000,
        source="test",
        created_by="pytest",
    )
    db.commit()
    return account


def test_reversal_restores_balance_without_editing_original():
    engine, db = make_session()
    account = make_account(db)
    payment = append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 2, 1),
        transaction_type="payment",
        direction="credit",
        amount=200,
        source="test",
        created_by="pytest",
    )
    db.commit()
    original_id = payment.id
    assert account_balance(db, account.id) == 800.0

    reversal = reverse_transaction(db, payment, reason="Payment entered twice", actor="pytest")
    db.commit()
    assert reversal.reverses_transaction_id == original_id
    assert account_balance(db, account.id) == 1000.0
    assert verify_account_chain(db, account.id)["ok"] is True

    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE ledger_transactions SET amount = 1 WHERE id = :id"), {"id": original_id})
        db.commit()
    db.rollback()
    engine.dispose()


def test_negative_payment_is_recorded_as_a_debit():
    engine, db = make_session()
    account = make_account(db)

    charge = append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 1, 15),
        transaction_type="payment",
        direction="credit",
        amount=-70,
        note="Additional borrowing",
        source="test",
        created_by="pytest",
    )
    db.commit()

    assert charge.amount == 70
    assert charge.direction == "debit"
    assert account_balance(db, account.id) == 1070.0
    assert verify_account_chain(db, account.id)["ok"] is True
    engine.dispose()


def test_correction_posts_reversal_and_replacement_with_audit():
    engine, db = make_session()
    account = make_account(db)
    payment = append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 2, 1),
        transaction_type="payment",
        direction="credit",
        amount=200,
        note="Original payment",
        source="test",
        created_by="pytest",
    )
    db.commit()

    reversal, replacement = correct_transaction(
        db,
        payment,
        effective_date=date(2026, 2, 2),
        transaction_type="payment",
        direction="credit",
        amount=250,
        note="Corrected payment",
        reason="Bank statement showed £250",
        actor="pytest",
    )
    db.commit()

    assert reversal.reverses_transaction_id == payment.id
    assert replacement.correction_group == reversal.correction_group
    assert account_balance(db, account.id) == 750.0
    assert verify_account_chain(db, account.id)["ok"] is True
    assert db.scalar(select(func.count(AuditEvent.id))) >= 6
    engine.dispose()

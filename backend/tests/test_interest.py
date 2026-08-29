from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.interest import calculate_account, day_fraction
from app.ledger import append_transaction, correct_transaction
from app.migrations import install_immutability_guards
from app.models import Account, InterestRatePeriod, Person


def make_session(rate=Decimal("12.0")):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    person = Person(name="Interest Test")
    db.add(person); db.flush()
    account = Account(
        person_id=person.id,
        name="Loan",
        opening_principal=1000,
        annual_interest_rate=float(rate),
        regular_payment=200,
        start_date=date(2026, 1, 1),
        legacy_id="interest-test",
        interest_method="daily_simple",
        day_count_convention="actual_365",
        payment_allocation="fees_interest_principal",
    )
    db.add(account); db.flush()
    db.add(InterestRatePeriod(
        account_id=account.id,
        effective_from=date(2026, 1, 1),
        annual_rate=rate,
        day_count_convention="actual_365",
        reason="Test rate",
        created_by="pytest",
    ))
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
    install_immutability_guards(engine)
    return engine, db, account


def test_payment_pays_accrued_interest_before_principal():
    engine, db, account = make_session()
    append_transaction(
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
    result = calculate_account(db, account.id, date(2026, 2, 1))
    payment = result["timeline"][-1]
    assert payment["allocated_to_interest"] == pytest.approx(10.19, abs=0.01)
    assert payment["allocated_to_principal"] == pytest.approx(189.81, abs=0.01)
    assert result["principal"] == pytest.approx(810.19, abs=0.01)
    assert result["accrued_interest"] == 0.0
    assert result["total_balance"] == pytest.approx(810.19, abs=0.01)
    engine.dispose()


def test_backdated_payment_recalculates_later_interest():
    engine, db, account = make_session()
    without_payment = calculate_account(db, account.id, date(2026, 3, 1))["total_balance"]
    append_transaction(
        db,
        account_id=account.id,
        effective_date=date(2026, 1, 15),
        transaction_type="payment",
        direction="credit",
        amount=200,
        source="test",
        created_by="pytest",
    )
    db.commit()
    with_payment = calculate_account(db, account.id, date(2026, 3, 1))["total_balance"]
    assert without_payment == pytest.approx(1019.40, abs=0.02)
    assert with_payment < 820
    assert with_payment < without_payment - 190
    engine.dispose()


def test_rate_period_splits_interest_at_effective_date():
    engine, db, account = make_session()
    db.add(InterestRatePeriod(
        account_id=account.id,
        effective_from=date(2026, 1, 16),
        annual_rate=Decimal("6.0"),
        day_count_convention="actual_365",
        reason="Rate reduced",
        created_by="pytest",
    ))
    db.commit()
    result = calculate_account(db, account.id, date(2026, 2, 1))
    expected = 1000 * 0.12 * 15 / 365 + 1000 * 0.06 * 16 / 365
    assert result["accrued_interest"] == pytest.approx(expected, abs=0.02)
    assert len(result["final_interest_segments"]) == 2
    engine.dispose()


def test_correcting_payment_date_replays_interest_history():
    engine, db, account = make_session()
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
    original = calculate_account(db, account.id, date(2026, 3, 1))["total_balance"]
    correct_transaction(
        db,
        payment,
        effective_date=date(2026, 1, 15),
        transaction_type="payment",
        direction="credit",
        amount=200,
        note="Payment actually arrived earlier",
        reason="Correct bank date",
        actor="pytest",
    )
    db.commit()
    corrected = calculate_account(db, account.id, date(2026, 3, 1))["total_balance"]
    assert corrected < original
    engine.dispose()


def test_rate_periods_are_database_immutable():
    engine, db, account = make_session()
    rate_id = account.interest_rates[0].id
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE interest_rate_periods SET annual_rate = 99 WHERE id = :id"), {"id": rate_id})
        db.commit()
    db.rollback()
    engine.dispose()


def test_supported_day_count_conventions():
    assert day_fraction(date(2026, 1, 1), date(2026, 2, 1), "actual_365") == Decimal(31) / Decimal(365)
    assert day_fraction(date(2026, 1, 1), date(2026, 2, 1), "actual_366") == Decimal(31) / Decimal(366)
    assert day_fraction(date(2024, 1, 1), date(2024, 2, 1), "actual_actual") == Decimal(31) / Decimal(366)
    assert day_fraction(date(2026, 1, 1), date(2026, 2, 1), "30_360") == Decimal(30) / Decimal(360)

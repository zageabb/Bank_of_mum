from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.ledger import append_transaction
from app.models import Account, LedgerTransaction, PaymentPlan, PaymentPlanAccount, Person
from app.planning import forecast_payment_plan, validate_members


def make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def make_account(db, person, name, principal, payment, rate=0.0):
    account = Account(
        person_id=person.id,
        name=name,
        opening_principal=principal,
        annual_interest_rate=rate,
        regular_payment=payment,
        start_date=date(2026, 9, 1) if rate == 0 else date(2026, 1, 1),
        legacy_id=f"test-{name.lower().replace(' ', '-')}-{principal}",
    )
    db.add(account)
    db.flush()
    append_transaction(
        db,
        account_id=account.id,
        effective_date=account.start_date,
        transaction_type="opening_balance",
        direction="debit",
        amount=principal,
        source="test",
        created_by="pytest",
    )
    db.flush()
    return account


def make_plan(db, members, first_payment_date, budget=350, name="Family snowball"):
    plan = PaymentPlan(
        name=name,
        first_payment_date=first_payment_date,
        monthly_budget=Decimal(str(budget)),
        strategy="priority_rollover",
        status="active",
        created_by="pytest",
    )
    db.add(plan)
    for priority, account, base_payment in members:
        plan.members.append(PaymentPlanAccount(
            account=account,
            priority=priority,
            base_payment=Decimal(str(base_payment)),
            enabled=True,
        ))
    db.commit()
    return plan


def test_car_payment_rolls_to_fat_bike_after_month_five():
    engine, db = make_session()
    person = Person(name="Test Family")
    db.add(person)
    db.flush()
    car = make_account(db, person, "Car", 1000, 200)
    bike = make_account(db, person, "Fat Bike", 2500, 150)
    plan = make_plan(db, [(1, car, 200), (2, bike, 150)], date(2026, 9, 1), 350)

    result = forecast_payment_plan(db, plan, 24)["forecast"]

    assert result["months_generated"] == 10
    assert result["payoff_date"] == "2027-06-01"
    assert result["total_forecast_payments"] == 3500.0
    assert result["remaining_balance"] == 0.0

    month_five = result["schedule"][4]
    assert {item["account"]: item["amount"] for item in month_five["payments"]} == {"Car": 200.0, "Fat Bike": 150.0}

    month_six = result["schedule"][5]
    assert len(month_six["payments"]) == 1
    assert month_six["payments"][0]["account"] == "Fat Bike"
    assert month_six["payments"][0]["amount"] == 350.0
    assert month_six["payments"][0]["rollover_component"] == 200.0
    engine.dispose()


def test_unused_final_payment_rolls_over_in_same_month():
    engine, db = make_session()
    person = Person(name="Spillover Family")
    db.add(person)
    db.flush()
    car = make_account(db, person, "Car", 900, 200)
    bike = make_account(db, person, "Fat Bike", 2500, 150)
    plan = make_plan(db, [(1, car, 200), (2, bike, 150)], date(2026, 9, 1), 350, "Spillover plan")

    result = forecast_payment_plan(db, plan, 24)["forecast"]
    month_five = result["schedule"][4]
    by_name = {item["account"]: item for item in month_five["payments"]}

    assert by_name["Car"]["amount"] == 100.0
    assert by_name["Fat Bike"]["base_component"] == 150.0
    assert by_name["Fat Bike"]["rollover_component"] == 100.0
    assert by_name["Fat Bike"]["amount"] == 250.0
    assert month_five["used"] == 350.0
    assert month_five["unused"] == 0.0
    engine.dispose()


def test_interest_aware_forecast_does_not_write_ledger_rows():
    engine, db = make_session()
    person = Person(name="Interest Family")
    db.add(person)
    db.flush()
    loan = make_account(db, person, "Loan", 1000, 200, rate=12.0)
    plan = make_plan(db, [(1, loan, 200)], date(2026, 2, 1), 200, "Interest plan")
    before_count = db.scalar(select(func.count(LedgerTransaction.id)))

    result = forecast_payment_plan(db, plan, 24)["forecast"]
    after_count = db.scalar(select(func.count(LedgerTransaction.id)))

    assert before_count == after_count == 1
    assert result["projected_interest"] > 0
    assert result["total_forecast_payments"] > 1000
    assert result["payoff_date"] is not None
    engine.dispose()


def test_budget_cannot_be_lower_than_enabled_base_payments():
    engine, db = make_session()
    person = Person(name="Budget Family")
    db.add(person)
    db.flush()
    car = make_account(db, person, "Car", 1000, 200)
    bike = make_account(db, person, "Fat Bike", 2500, 150)

    with pytest.raises(ValueError, match="below enabled base payments"):
        validate_members(
            db,
            [
                {"account_id": car.id, "priority": 1, "base_payment": 200, "enabled": True},
                {"account_id": bike.id, "priority": 2, "base_payment": 150, "enabled": True},
            ],
            300,
        )
    engine.dispose()

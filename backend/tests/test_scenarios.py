from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.ledger import append_transaction
from app.models import Account, InterestRatePeriod, LedgerTransaction, PaymentPlan, PaymentPlanAccount, Person, Scenario
from app.planning import forecast_payment_plan
from app.scenarios import compare_scenario, forecast_scenario, replace_changes, validate_changes


def make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def make_account(db, person, name, principal, payment, rate=0.0, start=date(2026, 9, 1)):
    account = Account(
        person_id=person.id,
        name=name,
        opening_principal=principal,
        annual_interest_rate=rate,
        regular_payment=payment,
        start_date=start,
        legacy_id=f"scenario-{name.lower().replace(' ', '-')}-{principal}-{rate}",
    )
    db.add(account)
    db.flush()
    append_transaction(
        db,
        account_id=account.id,
        effective_date=start,
        transaction_type="opening_balance",
        direction="debit",
        amount=principal,
        source="test",
        created_by="pytest",
    )
    db.flush()
    return account


def make_plan(db, members, budget, first=date(2026, 9, 1), name="Baseline"):
    plan = PaymentPlan(
        name=name,
        first_payment_date=first,
        monthly_budget=Decimal(str(budget)),
        strategy="priority_rollover",
        status="active",
        created_by="pytest",
    )
    db.add(plan)
    for priority, account, base in members:
        plan.members.append(PaymentPlanAccount(account=account, priority=priority, base_payment=Decimal(str(base)), enabled=True))
    db.commit()
    return plan


def make_scenario(db, plan, name, changes):
    validated = validate_changes(db, plan, changes)
    scenario = Scenario(plan=plan, name=name, description="pytest scenario", status="active", created_by="pytest")
    db.add(scenario)
    replace_changes(scenario, validated)
    db.commit()
    return scenario


def test_extra_monthly_budget_shortens_original_car_and_bike_plan():
    engine, db = make_session()
    person = Person(name="Scenario Family")
    db.add(person)
    db.flush()
    car = make_account(db, person, "Car", 1000, 200)
    bike = make_account(db, person, "Fat Bike", 2500, 150)
    plan = make_plan(db, [(1, car, 200), (2, bike, 150)], 350)
    scenario = make_scenario(db, plan, "Add £100", [{
        "change_type": "budget_delta",
        "effective_from": date(2026, 9, 1),
        "value": 100,
        "note": "Add £100 every month",
    }])

    baseline = forecast_payment_plan(db, plan, 24)["forecast"]
    changed = forecast_scenario(db, scenario, 24)["forecast"]
    comparison = compare_scenario(db, scenario, 24)["comparison"]

    assert baseline["months_generated"] == 10
    assert changed["months_generated"] == 8
    assert changed["payoff_date"] == "2027-04-01"
    assert changed["schedule"][0]["budget"] == 450.0
    assert comparison["months_saved"] == 2
    engine.dispose()


def test_lump_sum_uses_exact_date_and_never_writes_ledger():
    engine, db = make_session()
    person = Person(name="Lump Family")
    db.add(person)
    db.flush()
    loan = make_account(db, person, "Loan", 2000, 200)
    plan = make_plan(db, [(1, loan, 200)], 200, name="Lump baseline")
    scenario = make_scenario(db, plan, "Christmas lump", [{
        "change_type": "lump_sum",
        "account_id": loan.id,
        "effective_from": date(2026, 12, 15),
        "value": 500,
        "note": "Christmas bonus",
    }])
    before = db.scalar(select(func.count(LedgerTransaction.id)))

    result = forecast_scenario(db, scenario, 24)["forecast"]
    after = db.scalar(select(func.count(LedgerTransaction.id)))

    assert before == after == 1
    assert result["scenario_events"][0]["date"] == "2026-12-15"
    assert result["scenario_events"][0]["amount"] == 500.0
    assert result["payoff_date"] < "2027-06-01"
    engine.dispose()


def test_payment_holiday_delays_payoff_without_changing_baseline_plan():
    engine, db = make_session()
    person = Person(name="Holiday Family")
    db.add(person)
    db.flush()
    loan = make_account(db, person, "Loan", 1000, 200)
    plan = make_plan(db, [(1, loan, 200)], 200, name="Holiday baseline")
    scenario = make_scenario(db, plan, "December holiday", [{
        "change_type": "payment_holiday",
        "effective_from": date(2026, 12, 1),
        "effective_to": date(2026, 12, 31),
        "note": "Skip December payment",
    }])

    baseline = forecast_payment_plan(db, plan, 12)["forecast"]
    changed = forecast_scenario(db, scenario, 12)["forecast"]

    assert baseline["payoff_date"] == "2027-01-01"
    assert changed["payoff_date"] == "2027-02-01"
    december = next(row for row in changed["schedule"] if row["date"] == "2026-12-01")
    assert december["budget"] == 0.0
    assert december["used"] == 0.0
    assert plan.monthly_budget == Decimal("200")
    engine.dispose()


def test_future_rate_assumption_changes_interest_without_contractual_rate_row():
    engine, db = make_session()
    person = Person(name="Rate Scenario Family")
    db.add(person)
    db.flush()
    loan = make_account(db, person, "Loan", 5000, 250, rate=5.0)
    plan = make_plan(db, [(1, loan, 250)], 250, name="Rate baseline")
    scenario = make_scenario(db, plan, "Rate rises", [{
        "change_type": "interest_rate",
        "account_id": loan.id,
        "effective_from": date(2027, 1, 1),
        "value": 12,
        "day_count_convention": "actual_365",
        "note": "Assume rate rises to 12%",
    }])
    before_rates = db.scalar(select(func.count(InterestRatePeriod.id)))

    comparison = compare_scenario(db, scenario, 48)
    after_rates = db.scalar(select(func.count(InterestRatePeriod.id)))

    assert before_rates == after_rates == 0
    assert comparison["candidate"]["projected_interest"] > comparison["baseline"]["projected_interest"]
    assert comparison["comparison"]["interest_saved"] < 0
    engine.dispose()


def test_scenario_validation_rejects_accounts_outside_baseline_plan_and_past_changes():
    engine, db = make_session()
    person = Person(name="Validation Family")
    db.add(person)
    db.flush()
    included = make_account(db, person, "Included", 1000, 100)
    other = make_account(db, person, "Other", 500, 50)
    plan = make_plan(db, [(1, included, 100)], 100, name="Validation baseline")

    with pytest.raises(ValueError, match="not part of payment plan"):
        validate_changes(db, plan, [{
            "change_type": "lump_sum",
            "account_id": other.id,
            "effective_from": date(2026, 10, 1),
            "value": 100,
        }])
    with pytest.raises(ValueError, match="cannot start before"):
        validate_changes(db, plan, [{
            "change_type": "budget_delta",
            "effective_from": date(2026, 8, 1),
            "value": 100,
        }])
    engine.dispose()

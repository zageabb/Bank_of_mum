from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai import AI_TOOLS, ai_settings_dict, execute_ai_tool, update_ai_settings
from app.database import Base
from app.ledger import append_transaction
from app.models import (
    Account,
    ApplicationSetting,
    AuditEvent,
    InterestRatePeriod,
    LedgerTransaction,
    PaymentPlan,
    PaymentPlanAccount,
    Person,
    Scenario,
)


def make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, Session()


def make_account(db, person, name="Family Loan", principal=1000, payment=200, rate=0):
    account = Account(
        person_id=person.id,
        name=name,
        opening_principal=principal,
        annual_interest_rate=rate,
        regular_payment=payment,
        start_date=date(2026, 9, 1),
        legacy_id=f"ai-test-{name.lower().replace(' ', '-')}",
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


def make_plan(db, account):
    plan = PaymentPlan(
        name="AI baseline plan",
        first_payment_date=date(2026, 9, 1),
        monthly_budget=Decimal("200.00"),
        strategy="priority_rollover",
        status="active",
        created_by="pytest",
    )
    db.add(plan)
    plan.members.append(
        PaymentPlanAccount(
            account=account,
            priority=1,
            base_payment=Decimal("200.00"),
            enabled=True,
        )
    )
    db.commit()
    return plan


def tool_names():
    return {item["function"]["name"] for item in AI_TOOLS}


def test_ai_tool_surface_has_no_ledger_mutations():
    names = tool_names()
    assert "get_account_ledger" in names
    assert "propose_scenario" in names
    forbidden = {
        "create_transaction",
        "post_payment",
        "reverse_transaction",
        "correct_transaction",
        "create_interest_rate",
        "update_payment_plan",
    }
    assert not names.intersection(forbidden)


def test_ai_settings_are_persisted_and_audited():
    engine, db = make_session()
    before = ai_settings_dict(db)
    assert before["provider"] == "ollama"

    updated = update_ai_settings(
        db,
        ollama_url="http://192.168.1.249:11434/",
        ollama_model="qwen3:14b",
        max_tool_calls=8,
        timeout_seconds=240,
        reason="pytest settings change",
        actor="pytest",
    )
    db.commit()

    assert updated["ollama_url"] == "http://192.168.1.249:11434"
    assert updated["ollama_model"] == "qwen3:14b"
    assert updated["max_tool_calls"] == 8
    assert updated["timeout_seconds"] == 240
    assert db.scalar(select(func.count(ApplicationSetting.id))) == 4
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_settings_updated"))
    assert audit is not None
    assert audit.actor == "pytest"
    engine.dispose()


def test_read_only_ai_balance_tool_uses_deterministic_calculation():
    engine, db = make_session()
    person = Person(name="AI Family")
    db.add(person)
    db.flush()
    account = make_account(db, person)
    db.commit()
    before_count = db.scalar(select(func.count(LedgerTransaction.id)))

    result, event = execute_ai_tool(
        db,
        "get_account_balance",
        {"account_id": account.id, "as_of": "2026-09-01"},
        allow_scenario_proposals=True,
    )

    assert result["calculation"]["principal"] == 1000.0
    assert result["calculation"]["total_balance"] == 1000.0
    assert "Calculated" in event["summary"]
    assert db.scalar(select(func.count(LedgerTransaction.id))) == before_count
    engine.dispose()


def test_ai_can_only_prepare_draft_scenario_without_accounting_mutation():
    engine, db = make_session()
    person = Person(name="Scenario AI Family")
    db.add(person)
    db.flush()
    account = make_account(db, person)
    plan = make_plan(db, account)
    ledger_before = db.scalar(select(func.count(LedgerTransaction.id)))
    rates_before = db.scalar(select(func.count(InterestRatePeriod.id)))

    result, event = execute_ai_tool(
        db,
        "propose_scenario",
        {
            "plan_id": plan.id,
            "name": "AI plus 100",
            "description": "Test an extra £100 per month",
            "changes": [
                {
                    "change_type": "budget_delta",
                    "effective_from": "2026-09-01",
                    "value": 100,
                    "note": "AI proposal",
                }
            ],
        },
        allow_scenario_proposals=True,
    )

    scenario = db.get(Scenario, result["scenario"]["id"])
    assert scenario is not None
    assert scenario.status == "draft"
    assert scenario.created_by == "ai"
    assert result["comparison"]["non_destructive"] is True
    assert db.scalar(select(func.count(LedgerTransaction.id))) == ledger_before
    assert db.scalar(select(func.count(InterestRatePeriod.id))) == rates_before
    assert "draft scenario" in event["summary"].lower()
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "ai_scenario_proposed"))
    assert audit is not None
    engine.dispose()


def test_scenario_proposal_can_be_disabled_per_chat_request():
    engine, db = make_session()
    person = Person(name="Disabled Proposal Family")
    db.add(person)
    db.flush()
    account = make_account(db, person)
    plan = make_plan(db, account)

    try:
        execute_ai_tool(
            db,
            "propose_scenario",
            {
                "plan_id": plan.id,
                "name": "Should not save",
                "changes": [{"change_type": "budget_delta", "effective_from": "2026-09-01", "value": 50}],
            },
            allow_scenario_proposals=False,
        )
        assert False, "Expected proposals-disabled error"
    except ValueError as exc:
        assert "disabled" in str(exc).lower()

    assert db.scalar(select(func.count(Scenario.id))) == 0
    engine.dispose()

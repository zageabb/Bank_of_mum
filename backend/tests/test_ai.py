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


def test_list_accounts_filters_and_empty_database():
    import pytest
    engine, db = make_session()
    result, _ = execute_ai_tool(db, "list_accounts", {}, allow_scenario_proposals=False)
    assert result["count"] == 0 and result["database_empty"] is True
    person = Person(name="Family")
    db.add(person)
    db.flush()
    account = make_account(db, person)
    db.commit()
    for status in (None, "all", "active", "open", " Active "):
        result, _ = execute_ai_tool(db, "list_accounts", {"status": status, "as_of": "2026-09-01"}, allow_scenario_proposals=False)
        assert result["count"] == 1 and result["database_empty"] is False
        assert result["accounts"][0]["account_id"] == account.id
        assert result["accounts"][0]["calculation"]["total_balance"] == 1000
    result, _ = execute_ai_tool(db, "list_accounts", {"status": "settled"}, allow_scenario_proposals=False)
    assert result["count"] == 0 and result["database_empty"] is False
    assert result["total_accounts"] == 1
    with pytest.raises(ValueError, match="Unsupported account status"):
        execute_ai_tool(db, "list_accounts", {"status": "closed"}, allow_scenario_proposals=False)
    engine.dispose()


def test_ollama_errors_preserve_body():
    import httpx
    import pytest
    from app.ai import check_ollama_response
    for code, body, expected in [
        (404, {"error": "model 'missing' not found"}, "not installed"),
        (400, {"error": "model does not support tools"}, "tool calling"),
        (500, {"error": "runner failed"}, "runner failed"),
    ]:
        response = httpx.Response(code, json=body, request=httpx.Request("POST", "http://ollama/api/chat"))
        with pytest.raises(httpx.HTTPError, match=expected) as error:
            check_ollama_response(response, "missing")
        assert body["error"] in str(error.value)
    with pytest.raises(httpx.HTTPError, match="endpoint /api/chat is unavailable"):
        check_ollama_response(httpx.Response(404, text="Not Found", request=httpx.Request("POST", "http://ollama/api/chat")))


def test_chat_deduplicates_events_and_keeps_tool_responses(monkeypatch):
    import asyncio
    import httpx
    import app.ai as ai
    engine, db = make_session()
    requests = []
    def handle(request):
        import json
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            call = {"function": {"name": "list_accounts", "arguments": {}}}
            return httpx.Response(200, json={"message": {"role": "assistant", "tool_calls": [call, call]}})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "No account records."}})
    original = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(ai.chat_with_tools(db, [{"role": "user", "content": "list accounts"}]))
    assert len(result["tool_events"]) == 1
    assert len([m for m in requests[1]["messages"] if m["role"] == "tool"]) == 2
    engine.dispose()


def test_chat_connection_and_timeout_errors(monkeypatch):
    import asyncio
    import httpx
    import pytest
    import app.ai as ai
    engine, db = make_session()
    original = httpx.AsyncClient
    for error_type, expected in [(httpx.ConnectError, "server unavailable"), (httpx.ReadTimeout, "timed out")]:
        def handle(request):
            raise error_type("connection failed", request=request)
        monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
        with pytest.raises(httpx.HTTPError, match=expected):
            asyncio.run(ai.chat_with_tools(db, [{"role": "user", "content": "list accounts"}]))
    engine.dispose()

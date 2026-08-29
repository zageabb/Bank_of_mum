from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .interest import calculate_account
from .ledger import account_ledger, log_audit
from .models import (
    Account,
    ApplicationSetting,
    AuditEvent,
    LedgerTransaction,
    PaymentPlan,
    Scenario,
)
from .planning import forecast_payment_plan, plan_dict
from .scenarios import compare_many, compare_scenario, replace_changes, scenario_dict, validate_changes

SYSTEM_PROMPT = """You are Bank of Mum AI, a careful family-lending accounting assistant.
The immutable ledger and deterministic calculation engines are the source of truth.
Always use tools for balances, interest, forecasts, payment plans and scenario comparisons; never invent financial figures.
Explain calculations in clear UK English and distinguish actual ledger data from future forecasts.
You are read-only with respect to accounting: never post, edit, reverse or correct ledger transactions and never create contractual interest-rate records.
You may prepare a DRAFT what-if scenario only when the user asks to model a change. Draft scenarios require human review and do not alter the ledger, contractual rates or baseline payment plan.
If a tool result is incomplete or an account/plan/scenario is ambiguous, say so rather than guessing.
Use concise Markdown. Include dates when discussing balances or forecasts."""

SETTING_DEFAULTS = {
    "ai.ollama_url": settings.ollama_url,
    "ai.ollama_model": settings.ollama_model,
    "ai.max_tool_calls": "6",
    "ai.timeout_seconds": "180",
}


def _setting(db: Session, key: str) -> str:
    row = db.scalar(select(ApplicationSetting).where(ApplicationSetting.key == key).limit(1))
    return row.value if row else SETTING_DEFAULTS[key]


def ai_settings_dict(db: Session) -> dict:
    return {
        "provider": "ollama",
        "ollama_url": _setting(db, "ai.ollama_url"),
        "ollama_model": _setting(db, "ai.ollama_model"),
        "max_tool_calls": int(_setting(db, "ai.max_tool_calls")),
        "timeout_seconds": int(_setting(db, "ai.timeout_seconds")),
        "accounting_mode": "read_only",
        "scenario_proposals": "draft_only",
    }


def update_ai_settings(
    db: Session,
    *,
    ollama_url: str,
    ollama_model: str,
    max_tool_calls: int,
    timeout_seconds: int,
    reason: str,
    actor: str = "local",
) -> dict:
    before = ai_settings_dict(db)
    values = {
        "ai.ollama_url": ollama_url.rstrip("/"),
        "ai.ollama_model": ollama_model.strip(),
        "ai.max_tool_calls": str(max_tool_calls),
        "ai.timeout_seconds": str(timeout_seconds),
    }
    for key, value in values.items():
        row = db.scalar(select(ApplicationSetting).where(ApplicationSetting.key == key).limit(1))
        if row:
            row.value = value
        else:
            db.add(ApplicationSetting(key=key, value=value))
    db.flush()
    after = ai_settings_dict(db)
    log_audit(
        db,
        entity_type="ai_settings",
        entity_id=0,
        action="ai_settings_updated",
        summary=f"AI settings updated to {after['ollama_model']} at {after['ollama_url']}",
        before=before,
        after=after,
        reason=reason,
        actor=actor,
    )
    return after


async def list_ollama_models(base_url: str, timeout_seconds: int = 15) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(base_url.rstrip("/") + "/api/tags")
        response.raise_for_status()
        return [
            {
                "name": item.get("name", ""),
                "size": int(item.get("size") or 0),
                "modified_at": item.get("modified_at", ""),
                "family": (item.get("details") or {}).get("family", ""),
                "parameter_size": (item.get("details") or {}).get("parameter_size", ""),
                "quantization_level": (item.get("details") or {}).get("quantization_level", ""),
            }
            for item in response.json().get("models", [])
            if item.get("name")
        ]


AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": "Get current Bank of Mum totals derived from the dated accounting engine.",
            "parameters": {"type": "object", "properties": {"as_of": {"type": "string", "description": "Optional YYYY-MM-DD date."}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_accounts",
            "description": "List family lending accounts with current or dated calculated balances.",
            "parameters": {"type": "object", "properties": {"as_of": {"type": "string"}, "status": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_balance",
            "description": "Calculate one account balance, principal, accrued interest and payment allocation as at a date.",
            "parameters": {"type": "object", "required": ["account_id"], "properties": {"account_id": {"type": "integer"}, "as_of": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_ledger",
            "description": "Read immutable ledger entries for one account.",
            "parameters": {"type": "object", "required": ["account_id"], "properties": {"account_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_payment_plans",
            "description": "List saved baseline repayment plans and their priorities/base payments.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forecast_payment_plan",
            "description": "Run the deterministic baseline repayment forecast for a saved payment plan.",
            "parameters": {"type": "object", "required": ["plan_id"], "properties": {"plan_id": {"type": "integer"}, "horizon_months": {"type": "integer", "minimum": 1, "maximum": 600}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scenarios",
            "description": "List saved what-if scenarios, optionally for one payment plan.",
            "parameters": {"type": "object", "properties": {"plan_id": {"type": "integer"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_scenario",
            "description": "Compare one saved scenario with its unchanged baseline payment plan.",
            "parameters": {"type": "object", "required": ["scenario_id"], "properties": {"scenario_id": {"type": "integer"}, "horizon_months": {"type": "integer", "minimum": 1, "maximum": 600}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_scenarios",
            "description": "Compare multiple saved scenarios that share the same baseline plan.",
            "parameters": {"type": "object", "required": ["scenario_ids"], "properties": {"scenario_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 12}, "horizon_months": {"type": "integer", "minimum": 1, "maximum": 600}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_audit_history",
            "description": "Read recent immutable audit events, optionally filtered to an account's transactions/rates.",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_scenario",
            "description": "Prepare a DRAFT what-if scenario for human review. This never changes ledger entries, contractual rates or the baseline plan.",
            "parameters": {
                "type": "object",
                "required": ["plan_id", "name", "changes"],
                "properties": {
                    "plan_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["change_type", "effective_from"],
                            "properties": {
                                "change_type": {"type": "string", "enum": ["budget_delta", "budget_override", "lump_sum", "payment_holiday", "base_payment_override", "priority_override", "interest_rate"]},
                                "account_id": {"type": "integer"},
                                "effective_from": {"type": "string"},
                                "effective_to": {"type": "string"},
                                "value": {"type": "number"},
                                "day_count_convention": {"type": "string", "enum": ["actual_365", "actual_366", "actual_actual", "30_360"]},
                                "note": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
]


def _parse_date(value: Any, default: date | None = None) -> date | None:
    if value in (None, ""):
        return default
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _horizon(arguments: dict[str, Any]) -> int:
    return max(1, min(int(arguments.get("horizon_months") or 240), 600))


def execute_ai_tool(
    db: Session,
    name: str,
    arguments: dict[str, Any],
    *,
    allow_scenario_proposals: bool,
) -> tuple[dict, dict]:
    if name == "get_portfolio_summary":
        target = _parse_date(arguments.get("as_of"), date.today())
        accounts = list(db.scalars(select(Account).order_by(Account.id)).all())
        calculations = [calculate_account(db, item.id, target) for item in accounts]
        result = {
            "as_of": target.isoformat(),
            "people": len({item.person_id for item in accounts}),
            "accounts": len(accounts),
            "total_balance": round(sum(item["total_balance"] for item in calculations), 2),
            "principal": round(sum(item["principal"] for item in calculations), 2),
            "accrued_interest": round(sum(item["accrued_interest"] for item in calculations), 2),
            "fees": round(sum(item["fees"] for item in calculations), 2),
            "total_interest_paid": round(sum(item["total_interest_paid"] for item in calculations), 2),
        }
    elif name == "list_accounts":
        target = _parse_date(arguments.get("as_of"), date.today())
        query = select(Account).order_by(Account.person_id, Account.id)
        if arguments.get("status"):
            query = query.where(Account.status == str(arguments["status"]))
        rows = list(db.scalars(query).all())
        result = {
            "as_of": target.isoformat(),
            "accounts": [
                {
                    "account_id": item.id,
                    "person": item.person.name,
                    "account": item.name,
                    "status": item.status,
                    "regular_payment": float(item.regular_payment or 0),
                    "calculation": calculate_account(db, item.id, target),
                }
                for item in rows
            ],
        }
    elif name == "get_account_balance":
        account_id = int(arguments["account_id"])
        account = db.get(Account, account_id)
        if not account:
            raise ValueError("Account not found")
        target = _parse_date(arguments.get("as_of"), date.today())
        result = {
            "account": {"id": account.id, "person": account.person.name, "name": account.name},
            "calculation": calculate_account(db, account.id, target),
        }
    elif name == "get_account_ledger":
        account_id = int(arguments["account_id"])
        account = db.get(Account, account_id)
        if not account:
            raise ValueError("Account not found")
        limit = max(1, min(int(arguments.get("limit") or 50), 200))
        rows = account_ledger(db, account.id)
        result = {
            "account": {"id": account.id, "person": account.person.name, "name": account.name},
            "transactions": rows[-limit:],
            "immutable": True,
        }
    elif name == "list_payment_plans":
        rows = list(db.scalars(select(PaymentPlan).order_by(PaymentPlan.status, PaymentPlan.name)).all())
        result = {"plans": [plan_dict(item) for item in rows]}
    elif name == "forecast_payment_plan":
        plan = db.get(PaymentPlan, int(arguments["plan_id"]))
        if not plan:
            raise ValueError("Payment plan not found")
        result = forecast_payment_plan(db, plan, _horizon(arguments))
    elif name == "list_scenarios":
        query = select(Scenario)
        if arguments.get("plan_id") is not None:
            query = query.where(Scenario.plan_id == int(arguments["plan_id"]))
        rows = list(db.scalars(query.order_by(Scenario.status, Scenario.name, Scenario.id)).all())
        result = {"scenarios": [scenario_dict(item) for item in rows]}
    elif name == "compare_scenario":
        scenario = db.get(Scenario, int(arguments["scenario_id"]))
        if not scenario:
            raise ValueError("Scenario not found")
        result = compare_scenario(db, scenario, _horizon(arguments))
    elif name == "compare_scenarios":
        ids = [int(item) for item in arguments.get("scenario_ids") or []]
        if not ids:
            raise ValueError("Choose at least one scenario")
        result = compare_many(db, ids, _horizon(arguments))
    elif name == "get_audit_history":
        limit = max(1, min(int(arguments.get("limit") or 30), 100))
        rows = list(db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)).all())
        result = {
            "events": [
                {
                    "id": item.id,
                    "entity_type": item.entity_type,
                    "entity_id": item.entity_id,
                    "action": item.action,
                    "summary": item.summary,
                    "reason": item.reason,
                    "actor": item.actor,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in rows
            ]
        }
    elif name == "propose_scenario":
        if not allow_scenario_proposals:
            raise ValueError("Scenario proposals are disabled for this request")
        plan = db.get(PaymentPlan, int(arguments["plan_id"]))
        if not plan:
            raise ValueError("Payment plan not found")
        scenario_name = str(arguments.get("name") or "AI scenario").strip()[:180]
        duplicate = db.scalar(select(Scenario).where(Scenario.plan_id == plan.id, Scenario.name == scenario_name).limit(1))
        if duplicate:
            raise ValueError("A scenario with this name already exists for the payment plan")
        validated = validate_changes(db, plan, list(arguments.get("changes") or []))
        scenario = Scenario(
            plan=plan,
            name=scenario_name,
            description=str(arguments.get("description") or "AI-proposed what-if scenario"),
            status="draft",
            created_by="ai",
        )
        db.add(scenario)
        replace_changes(scenario, validated)
        db.flush()
        snapshot = scenario_dict(scenario)
        log_audit(
            db,
            entity_type="scenario",
            entity_id=scenario.id,
            action="ai_scenario_proposed",
            summary=f"AI proposed draft scenario {scenario.name} for {plan.name}",
            after=snapshot,
            reason="Draft scenario proposed by Bank of Mum AI for human review",
            actor="ai",
        )
        db.commit()
        result = {
            "scenario": scenario_dict(scenario),
            "comparison": compare_scenario(db, scenario, 240),
            "message": "Draft scenario created for review. No ledger entries, contractual rates or baseline plan settings were changed.",
        }
    else:
        raise ValueError(f"Unknown AI tool: {name}")

    event = {"tool": name, "summary": _tool_summary(name, result)}
    return result, event


def _tool_summary(name: str, result: dict) -> str:
    if name == "get_portfolio_summary":
        return f"Calculated portfolio balance £{result['total_balance']:.2f} as at {result['as_of']}"
    if name == "list_accounts":
        return f"Listed {len(result.get('accounts', []))} accounts"
    if name == "get_account_balance":
        return f"Calculated {result['account']['person']} · {result['account']['name']}"
    if name == "get_account_ledger":
        return f"Read {len(result.get('transactions', []))} immutable ledger entries"
    if name == "list_payment_plans":
        return f"Listed {len(result.get('plans', []))} payment plans"
    if name == "forecast_payment_plan":
        return f"Forecast baseline plan to {result['forecast'].get('payoff_date') or 'horizon'}"
    if name == "list_scenarios":
        return f"Listed {len(result.get('scenarios', []))} scenarios"
    if name == "compare_scenario":
        return f"Compared scenario {result['scenario']['name']} with baseline"
    if name == "compare_scenarios":
        return f"Compared {len(result.get('scenarios', []))} scenarios"
    if name == "get_audit_history":
        return f"Read {len(result.get('events', []))} audit events"
    if name == "propose_scenario":
        return f"Prepared draft scenario #{result['scenario']['id']} for review"
    return name


async def chat_with_tools(
    db: Session,
    messages: list[dict],
    *,
    model_override: str | None = None,
    allow_scenario_proposals: bool = True,
) -> dict:
    configuration = ai_settings_dict(db)
    model = model_override or configuration["ollama_model"]
    url = configuration["ollama_url"].rstrip("/") + "/api/chat"
    prompt_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
    events: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}

    async with httpx.AsyncClient(timeout=configuration["timeout_seconds"]) as client:
        for _ in range(configuration["max_tool_calls"]):
            response = await client.post(
                url,
                json={"model": model, "messages": prompt_messages, "tools": AI_TOOLS, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
            usage["input_tokens"] += int(data.get("prompt_eval_count") or 0)
            usage["output_tokens"] += int(data.get("eval_count") or 0)
            message = data.get("message") or {}
            prompt_messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                reply = message.get("content") or "Done."
                _log_ai_chat(db, messages, reply, events, model, usage)
                db.commit()
                return {
                    "reply": reply,
                    "tool_events": events,
                    "model": model,
                    "provider": "ollama",
                    "usage": usage,
                    "accounting_mode": "read_only",
                    "scenario_proposals": "draft_only",
                }
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                try:
                    result, event = execute_ai_tool(
                        db,
                        name,
                        arguments,
                        allow_scenario_proposals=allow_scenario_proposals,
                    )
                except Exception as exc:
                    db.rollback()
                    result = {"error": str(exc)}
                    event = {"tool": name, "summary": f"{name} failed: {exc}", "error": str(exc)}
                events.append(event)
                prompt_messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)})

    reply = "I reached the configured tool-call limit. Review the completed tool activity, then ask me to continue."
    _log_ai_chat(db, messages, reply, events, model, usage)
    db.commit()
    return {
        "reply": reply,
        "tool_events": events,
        "model": model,
        "provider": "ollama",
        "usage": usage,
        "accounting_mode": "read_only",
        "scenario_proposals": "draft_only",
    }


def _log_ai_chat(db: Session, messages: list[dict], reply: str, events: list[dict], model: str, usage: dict) -> None:
    latest_user = next((item.get("content", "") for item in reversed(messages) if item.get("role") == "user"), "")
    log_audit(
        db,
        entity_type="ai_chat",
        entity_id=0,
        action="ai_query",
        summary=f"AI query used {len(events)} tool call(s) with {model}",
        before={"user_message": latest_user[:2000]},
        after={"reply": reply[:4000], "tools": events, "usage": usage},
        reason="Accounting AI query",
        actor="local",
    )

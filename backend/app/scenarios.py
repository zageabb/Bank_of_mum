from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ledger import money
from .models import Account, PaymentPlan, Scenario, ScenarioChange
from .planning import forecast_payment_plan, plan_dict

ACCOUNT_REQUIRED = {"lump_sum", "base_payment_override", "priority_override", "interest_rate"}
VALUE_REQUIRED = {"budget_delta", "budget_override", "lump_sum", "base_payment_override", "priority_override", "interest_rate"}
NON_NEGATIVE = {"budget_override", "lump_sum", "base_payment_override", "interest_rate"}


def change_dict(change: ScenarioChange) -> dict:
    return {
        "id": change.id,
        "change_type": change.change_type,
        "account_id": change.account_id,
        "account": f"{change.account.person.name} · {change.account.name}" if change.account else None,
        "effective_from": change.effective_from.isoformat(),
        "effective_to": change.effective_to.isoformat() if change.effective_to else None,
        "value": float(change.value) if change.value is not None else None,
        "day_count_convention": change.day_count_convention,
        "note": change.note,
    }


def scenario_dict(scenario: Scenario) -> dict:
    return {
        "id": scenario.id,
        "plan_id": scenario.plan_id,
        "plan_name": scenario.plan.name,
        "name": scenario.name,
        "description": scenario.description,
        "status": scenario.status,
        "created_by": scenario.created_by,
        "created_at": scenario.created_at.isoformat() if scenario.created_at else None,
        "updated_at": scenario.updated_at.isoformat() if scenario.updated_at else None,
        "changes": [change_dict(item) for item in scenario.changes],
    }


def validate_changes(db: Session, plan: PaymentPlan, changes: list[dict]) -> list[dict]:
    plan_account_ids = {item.account_id for item in plan.members}
    validated: list[dict] = []
    for index, raw in enumerate(changes, start=1):
        item = dict(raw)
        change_type = str(item.get("change_type") or "")
        effective_from = item.get("effective_from")
        effective_to = item.get("effective_to")
        if isinstance(effective_from, str):
            effective_from = date.fromisoformat(effective_from)
        if isinstance(effective_to, str) and effective_to:
            effective_to = date.fromisoformat(effective_to)
        if not isinstance(effective_from, date):
            raise ValueError(f"Scenario change {index} needs an effective date")
        if effective_to and effective_to < effective_from:
            raise ValueError(f"Scenario change {index} ends before it starts")
        if effective_from < plan.first_payment_date:
            raise ValueError("Scenario changes cannot start before the baseline plan's first payment date")

        account_id = item.get("account_id")
        if change_type in ACCOUNT_REQUIRED and not account_id:
            raise ValueError(f"{change_type.replace('_', ' ')} requires an account")
        if account_id is not None:
            account_id = int(account_id)
            if account_id not in plan_account_ids:
                raise ValueError(f"Account {account_id} is not part of payment plan {plan.name}")
            if not db.get(Account, account_id):
                raise ValueError(f"Account {account_id} not found")

        value = item.get("value")
        value_decimal = Decimal(str(value)) if value is not None else None
        if change_type in VALUE_REQUIRED and value_decimal is None:
            raise ValueError(f"{change_type.replace('_', ' ')} requires a value")
        if change_type in NON_NEGATIVE and value_decimal is not None and value_decimal < 0:
            raise ValueError(f"{change_type.replace('_', ' ')} cannot be negative")
        if change_type == "priority_override" and (value_decimal is None or value_decimal < 1 or value_decimal != value_decimal.to_integral_value()):
            raise ValueError("Priority override must be a whole number of 1 or greater")
        if change_type == "interest_rate" and value_decimal is not None and value_decimal > 100:
            raise ValueError("Scenario interest rate cannot exceed 100%")
        if change_type in {"lump_sum", "interest_rate", "priority_override"} and effective_to:
            raise ValueError(f"{change_type.replace('_', ' ')} is an effective-date change and does not use an end date")
        if change_type == "payment_holiday" and value_decimal is not None:
            raise ValueError("Payment holidays do not use a value")

        validated.append({
            "order": index,
            "change_type": change_type,
            "account_id": account_id,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "value": value_decimal,
            "day_count_convention": str(item.get("day_count_convention") or "actual_365"),
            "note": str(item.get("note") or ""),
        })
    return validated


def replace_changes(scenario: Scenario, validated: list[dict]) -> None:
    scenario.changes.clear()
    for item in validated:
        scenario.changes.append(ScenarioChange(
            change_type=item["change_type"],
            account_id=item["account_id"],
            effective_from=item["effective_from"],
            effective_to=item["effective_to"],
            value=item["value"],
            day_count_convention=item["day_count_convention"],
            note=item["note"],
        ))


def forecast_scenario(db: Session, scenario: Scenario, horizon_months: int = 240) -> dict:
    return forecast_payment_plan(
        db,
        scenario.plan,
        horizon_months,
        scenario_changes=[change_dict(item) for item in scenario.changes],
        scenario_name=scenario.name,
    )


def _month_difference(later: str | None, earlier: str | None) -> int | None:
    if not later or not earlier:
        return None
    left = date.fromisoformat(later)
    right = date.fromisoformat(earlier)
    return (left.year - right.year) * 12 + (left.month - right.month)


def compare_scenario(db: Session, scenario: Scenario, horizon_months: int = 240) -> dict:
    baseline = forecast_payment_plan(db, scenario.plan, horizon_months)
    changed = forecast_scenario(db, scenario, horizon_months)
    base = baseline["forecast"]
    candidate = changed["forecast"]
    months_saved = None
    if base.get("payoff_date") and candidate.get("payoff_date"):
        months_saved = _month_difference(base["payoff_date"], candidate["payoff_date"])
    interest_saved = money(Decimal(str(base["projected_interest"])) - Decimal(str(candidate["projected_interest"])))
    payment_difference = money(Decimal(str(candidate["total_forecast_payments"])) - Decimal(str(base["total_forecast_payments"])))
    remaining_difference = money(Decimal(str(candidate["remaining_balance"])) - Decimal(str(base["remaining_balance"])))
    return {
        "scenario": scenario_dict(scenario),
        "plan": plan_dict(scenario.plan),
        "baseline": base,
        "candidate": candidate,
        "comparison": {
            "baseline_payoff_date": base.get("payoff_date"),
            "scenario_payoff_date": candidate.get("payoff_date"),
            "months_saved": months_saved,
            "baseline_interest": base["projected_interest"],
            "scenario_interest": candidate["projected_interest"],
            "interest_saved": float(interest_saved),
            "payment_difference": float(payment_difference),
            "remaining_balance_difference": float(remaining_difference),
        },
        "non_destructive": True,
    }


def compare_many(db: Session, scenario_ids: list[int], horizon_months: int = 240) -> dict:
    scenarios = list(db.scalars(select(Scenario).where(Scenario.id.in_(scenario_ids)).order_by(Scenario.id)).all())
    if len(scenarios) != len(set(scenario_ids)):
        found = {item.id for item in scenarios}
        missing = sorted(set(scenario_ids) - found)
        raise ValueError(f"Scenario(s) not found: {', '.join(str(item) for item in missing)}")
    if scenarios and len({item.plan_id for item in scenarios}) != 1:
        raise ValueError("Side-by-side scenarios must use the same baseline payment plan")
    if not scenarios:
        return {"plan": None, "baseline": None, "scenarios": []}
    plan = scenarios[0].plan
    baseline = forecast_payment_plan(db, plan, horizon_months)["forecast"]
    rows = []
    for scenario in scenarios:
        candidate = forecast_scenario(db, scenario, horizon_months)["forecast"]
        months_saved = None
        if baseline.get("payoff_date") and candidate.get("payoff_date"):
            months_saved = _month_difference(baseline["payoff_date"], candidate["payoff_date"])
        rows.append({
            "scenario": scenario_dict(scenario),
            "payoff_date": candidate.get("payoff_date"),
            "months_saved": months_saved,
            "projected_interest": candidate["projected_interest"],
            "interest_saved": float(money(Decimal(str(baseline["projected_interest"])) - Decimal(str(candidate["projected_interest"])))),
            "total_forecast_payments": candidate["total_forecast_payments"],
            "remaining_balance": candidate["remaining_balance"],
        })
    return {
        "plan": plan_dict(plan),
        "baseline": baseline,
        "scenarios": rows,
        "non_destructive": True,
    }

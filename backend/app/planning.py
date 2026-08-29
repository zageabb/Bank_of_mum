from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .interest import calculate_account
from .ledger import money
from .models import Account, PaymentPlan, PaymentPlanAccount

ZERO = Decimal("0.00")
SETTLED_TOLERANCE = Decimal("0.005")


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def member_dict(member: PaymentPlanAccount) -> dict:
    return {
        "id": member.id,
        "account_id": member.account_id,
        "person": member.account.person.name,
        "account": member.account.name,
        "priority": member.priority,
        "base_payment": float(money(member.base_payment)),
        "enabled": bool(member.enabled),
        "current_regular_payment": float(member.account.regular_payment or 0),
    }


def plan_dict(plan: PaymentPlan) -> dict:
    members = [member_dict(item) for item in sorted(plan.members, key=lambda row: (row.priority, row.id))]
    return {
        "id": plan.id,
        "name": plan.name,
        "first_payment_date": plan.first_payment_date.isoformat(),
        "frequency": plan.frequency,
        "monthly_budget": float(money(plan.monthly_budget)),
        "strategy": plan.strategy,
        "status": plan.status,
        "notes": plan.notes,
        "created_by": plan.created_by,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "members": members,
    }


def validate_members(
    db: Session,
    members: list[dict],
    monthly_budget: Decimal | float | str,
) -> tuple[list[tuple[Account, int, Decimal, bool]], Decimal]:
    if not members:
        raise ValueError("A payment plan needs at least one account")
    account_ids = [int(item["account_id"]) for item in members]
    priorities = [int(item["priority"]) for item in members]
    if len(account_ids) != len(set(account_ids)):
        raise ValueError("An account can only appear once in a payment plan")
    if len(priorities) != len(set(priorities)):
        raise ValueError("Each plan account must have a unique priority")

    validated: list[tuple[Account, int, Decimal, bool]] = []
    base_total = ZERO
    for item in members:
        account = db.get(Account, int(item["account_id"]))
        if not account:
            raise ValueError(f"Account {item['account_id']} not found")
        priority = int(item["priority"])
        if priority < 1:
            raise ValueError("Plan priorities start at 1")
        base_payment = money(item.get("base_payment", account.regular_payment or 0))
        if base_payment < 0:
            raise ValueError("Base payments cannot be negative")
        enabled = bool(item.get("enabled", True))
        if enabled:
            base_total += base_payment
        validated.append((account, priority, base_payment, enabled))

    budget = money(monthly_budget)
    if budget <= 0:
        budget = money(base_total)
    if budget <= 0:
        raise ValueError("The plan monthly budget must be greater than zero")
    if budget < money(base_total):
        raise ValueError(
            f"Monthly budget £{budget:.2f} is below enabled base payments of £{money(base_total):.2f}"
        )
    return validated, budget


def _find_forecast_row(calculation: dict, forecast_key: str) -> dict:
    for row in reversed(calculation.get("timeline", [])):
        if row.get("forecast_key") == forecast_key:
            return row
    raise ValueError(f"Forecast payment {forecast_key} was not found in the calculation replay")


def _change_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _change_value(change: dict, default: Decimal = ZERO) -> Decimal:
    value = change.get("value")
    return default if value is None else Decimal(str(value))


def _active_change(change: dict, on_date: date) -> bool:
    start = _change_date(change["effective_from"])
    end_value = change.get("effective_to")
    end = _change_date(end_value) if end_value else None
    return start <= on_date and (end is None or on_date <= end)


def _scenario_order(changes: list[dict]) -> list[dict]:
    return sorted(
        changes,
        key=lambda item: (
            _change_date(item["effective_from"]),
            int(item.get("id") or item.get("order") or 0),
        ),
    )


def forecast_payment_plan(
    db: Session,
    plan: PaymentPlan,
    horizon_months: int = 240,
    scenario_changes: list[dict] | None = None,
    scenario_name: str | None = None,
) -> dict:
    if horizon_months < 1 or horizon_months > 600:
        raise ValueError("Forecast horizon must be between 1 and 600 months")
    if plan.frequency != "monthly":
        raise ValueError("Phase 5 currently supports monthly payment plans")
    if plan.strategy != "priority_rollover":
        raise ValueError("Unsupported payment-plan strategy")

    members = [item for item in sorted(plan.members, key=lambda row: (row.priority, row.id)) if item.enabled]
    if not members:
        raise ValueError("The payment plan has no enabled accounts")
    member_by_account = {item.account_id: item for item in members}
    base_total = money(sum((money(item.base_payment) for item in members), ZERO))
    baseline_budget = money(plan.monthly_budget)
    if baseline_budget <= 0:
        baseline_budget = base_total
    if baseline_budget < base_total:
        raise ValueError(
            f"Monthly budget £{baseline_budget:.2f} is below enabled base payments of £{base_total:.2f}"
        )

    changes = _scenario_order(list(scenario_changes or []))
    hypothetical_rates: dict[int, list[dict]] = {item.account_id: [] for item in members}
    lump_changes: list[dict] = []
    for change in changes:
        change_type = change.get("change_type")
        account_id = change.get("account_id")
        if change_type == "interest_rate" and account_id in member_by_account:
            hypothetical_rates[int(account_id)].append({
                "effective_from": _change_date(change["effective_from"]),
                "annual_rate": _change_value(change),
                "day_count_convention": change.get("day_count_convention") or "actual_365",
                "reason": change.get("note") or f"{scenario_name or 'Scenario'} rate assumption",
            })
        elif change_type == "lump_sum":
            lump_changes.append(change)

    def calc(account_id: int, as_of: date, virtual_payments: dict[int, list[dict]]) -> dict:
        return calculate_account(
            db,
            account_id,
            as_of,
            virtual_payments[account_id],
            hypothetical_rates.get(account_id, []),
        )

    def priority_for(member: PaymentPlanAccount, on_date: date) -> int:
        priority = member.priority
        for change in changes:
            if change.get("change_type") == "priority_override" and change.get("account_id") == member.account_id and _active_change(change, on_date):
                priority = max(1, int(_change_value(change, Decimal(priority))))
        return priority

    def base_payment_for(member: PaymentPlanAccount, on_date: date) -> Decimal:
        amount = money(member.base_payment)
        for change in changes:
            if change.get("change_type") == "base_payment_override" and change.get("account_id") == member.account_id and _active_change(change, on_date):
                amount = money(max(_change_value(change), ZERO))
        return amount

    def holiday_for(member: PaymentPlanAccount | None, on_date: date) -> bool:
        for change in changes:
            if change.get("change_type") != "payment_holiday" or not _active_change(change, on_date):
                continue
            account_id = change.get("account_id")
            if account_id is None or (member is not None and account_id == member.account_id):
                return True
        return False

    def budget_for(on_date: date) -> Decimal:
        if holiday_for(None, on_date):
            return ZERO
        amount = baseline_budget
        for change in changes:
            if not _active_change(change, on_date):
                continue
            if change.get("change_type") == "budget_override":
                amount = money(max(_change_value(change), ZERO))
            elif change.get("change_type") == "budget_delta":
                amount = money(max(amount + _change_value(change), ZERO))
        return amount

    virtual_payments: dict[int, list[dict]] = {item.account_id: [] for item in members}
    starting: dict[int, dict] = {
        item.account_id: calc(item.account_id, plan.first_payment_date, virtual_payments)
        for item in members
    }
    paid_totals: dict[int, Decimal] = {item.account_id: ZERO for item in members}
    schedule: list[dict] = []
    scenario_events: list[dict] = []
    global_payoff_date: str | None = None
    last_payment_date = plan.first_payment_date
    processed_lumps: set[int] = set()

    def post_hypothetical(
        member: PaymentPlanAccount,
        payment_date: date,
        requested: Decimal,
        component: str,
        forecast_key: str,
    ) -> tuple[Decimal, dict | None]:
        if requested <= 0:
            return ZERO, None
        current = calc(member.account_id, payment_date, virtual_payments)
        balance = money(max(Decimal(str(current["total_balance"])), ZERO))
        amount = money(min(requested, balance))
        if amount <= 0:
            return ZERO, None
        virtual_payments[member.account_id].append({
            "effective_date": payment_date,
            "amount": amount,
            "note": f"{scenario_name or plan.name} {component.replace('_', ' ')}",
            "forecast_key": forecast_key,
        })
        recalculated = calc(member.account_id, payment_date, virtual_payments)
        row = _find_forecast_row(recalculated, forecast_key)
        paid_totals[member.account_id] += amount
        return amount, {
            "account_id": member.account_id,
            "person": member.account.person.name,
            "account": member.account.name,
            "priority": priority_for(member, payment_date),
            "component": component,
            "amount": amount,
            "allocated_to_fees": money(row.get("allocated_to_fees", 0)),
            "allocated_to_interest": money(row.get("allocated_to_interest", 0)),
            "allocated_to_principal": money(row.get("allocated_to_principal", 0)),
            "balance_before": money(current["total_balance"]),
            "balance_after": money(recalculated["total_balance"]),
            "date": payment_date.isoformat(),
        }

    def process_lumps(up_to: date) -> None:
        for index, change in enumerate(lump_changes):
            if index in processed_lumps:
                continue
            event_date = _change_date(change["effective_from"])
            if event_date < plan.first_payment_date or event_date > up_to:
                continue
            account_id = change.get("account_id")
            member = member_by_account.get(int(account_id)) if account_id is not None else None
            if member is None:
                continue
            key = f"scenario-lump-{index + 1}-{member.account_id}"
            paid, row = post_hypothetical(member, event_date, money(max(_change_value(change), ZERO)), "lump_sum", key)
            processed_lumps.add(index)
            if paid > 0 and row:
                scenario_events.append({
                    **row,
                    "amount": float(row["amount"]),
                    "allocated_to_fees": float(row["allocated_to_fees"]),
                    "allocated_to_interest": float(row["allocated_to_interest"]),
                    "allocated_to_principal": float(row["allocated_to_principal"]),
                    "balance_before": float(row["balance_before"]),
                    "balance_after": float(row["balance_after"]),
                    "note": change.get("note") or "Scenario lump sum",
                })

    for period_index in range(horizon_months):
        payment_date = add_months(plan.first_payment_date, period_index)
        last_payment_date = payment_date
        process_lumps(payment_date)
        before = {
            item.account_id: calc(item.account_id, payment_date, virtual_payments)
            for item in members
        }
        active = [item for item in members if Decimal(str(before[item.account_id]["total_balance"])) > SETTLED_TOLERANCE]
        if not active:
            global_payoff_date = payment_date.isoformat()
            break

        budget = budget_for(payment_date)
        remaining = budget
        allocations: dict[int, dict] = {}
        payment_sequence = 0
        eligible = [item for item in active if not holiday_for(item, payment_date)]
        eligible.sort(key=lambda item: (priority_for(item, payment_date), item.id))

        def post_plan(member: PaymentPlanAccount, requested: Decimal, component: str) -> Decimal:
            nonlocal payment_sequence
            payment_sequence += 1
            key = f"plan-{plan.id}-{period_index + 1}-{member.account_id}-{payment_sequence}"
            paid, row = post_hypothetical(member, payment_date, requested, component, key)
            if paid <= 0 or not row:
                return ZERO
            bucket = allocations.setdefault(member.account_id, {
                "account_id": member.account_id,
                "person": member.account.person.name,
                "account": member.account.name,
                "priority": priority_for(member, payment_date),
                "base_component": ZERO,
                "rollover_component": ZERO,
                "amount": ZERO,
                "allocated_to_fees": ZERO,
                "allocated_to_interest": ZERO,
                "allocated_to_principal": ZERO,
                "balance_before": row["balance_before"],
                "balance_after": row["balance_after"],
            })
            bucket[f"{component}_component"] += paid
            bucket["amount"] += paid
            bucket["allocated_to_fees"] += row["allocated_to_fees"]
            bucket["allocated_to_interest"] += row["allocated_to_interest"]
            bucket["allocated_to_principal"] += row["allocated_to_principal"]
            bucket["balance_after"] = row["balance_after"]
            return paid

        for member in eligible:
            base_request = min(base_payment_for(member, payment_date), remaining)
            paid = post_plan(member, base_request, "base")
            remaining = money(remaining - paid)

        safety = 0
        while remaining > SETTLED_TOLERANCE:
            safety += 1
            if safety > len(members) + 4:
                break
            target = None
            for member in eligible:
                current = calc(member.account_id, payment_date, virtual_payments)
                if Decimal(str(current["total_balance"])) > SETTLED_TOLERANCE:
                    target = member
                    break
            if target is None:
                break
            paid = post_plan(target, remaining, "rollover")
            if paid <= 0:
                break
            remaining = money(remaining - paid)

        after = {
            item.account_id: calc(item.account_id, payment_date, virtual_payments)
            for item in members
        }
        period_payments = []
        for member in members:
            item = allocations.get(member.account_id)
            if not item:
                continue
            period_payments.append({
                **item,
                "base_component": float(money(item["base_component"])),
                "rollover_component": float(money(item["rollover_component"])),
                "amount": float(money(item["amount"])),
                "allocated_to_fees": float(money(item["allocated_to_fees"])),
                "allocated_to_interest": float(money(item["allocated_to_interest"])),
                "allocated_to_principal": float(money(item["allocated_to_principal"])),
                "balance_before": float(money(item["balance_before"])),
                "balance_after": float(money(item["balance_after"])),
            })
        remaining_balance = money(sum((money(after[item.account_id]["total_balance"]) for item in members), ZERO))
        used = money(budget - remaining)
        active_changes = [
            change.get("change_type")
            for change in changes
            if change.get("change_type") != "lump_sum" and _active_change(change, payment_date)
        ]
        schedule.append({
            "period": period_index + 1,
            "date": payment_date.isoformat(),
            "budget": float(budget),
            "used": float(used),
            "unused": float(money(remaining)),
            "remaining_balance": float(max(remaining_balance, ZERO)),
            "payments": period_payments,
            "active_scenario_changes": active_changes,
        })
        if all(Decimal(str(after[item.account_id]["total_balance"])) <= SETTLED_TOLERANCE for item in members):
            global_payoff_date = payment_date.isoformat()
            break

    if global_payoff_date is None:
        process_lumps(last_payment_date)
    final_date = date.fromisoformat(global_payoff_date) if global_payoff_date else last_payment_date
    account_summaries: list[dict] = []
    projected_interest_total = ZERO
    total_forecast_payments = ZERO
    remaining_total = ZERO
    actual_payoff_dates: list[date] = []
    for member in members:
        final = calc(member.account_id, final_date, virtual_payments)
        opening = starting[member.account_id]
        projected_interest = money(
            Decimal(str(final["total_interest_accrued"])) - Decimal(str(opening["total_interest_accrued"]))
        )
        projected_interest = max(projected_interest, ZERO)
        projected_interest_total += projected_interest
        total_forecast_payments += paid_totals[member.account_id]
        remaining_total += money(max(Decimal(str(final["total_balance"])), ZERO))
        payoff_date = None
        if Decimal(str(opening["total_balance"])) <= SETTLED_TOLERANCE:
            payoff_date = plan.first_payment_date.isoformat()
        else:
            for row in final.get("timeline", []):
                if row.get("is_hypothetical") and row.get("date") >= plan.first_payment_date.isoformat() and Decimal(str(row.get("balance_after", 0))) <= SETTLED_TOLERANCE:
                    payoff_date = row["date"]
                    break
        if payoff_date:
            actual_payoff_dates.append(date.fromisoformat(payoff_date))
        account_summaries.append({
            "account_id": member.account_id,
            "person": member.account.person.name,
            "account": member.account.name,
            "priority": member.priority,
            "base_payment": float(money(member.base_payment)),
            "starting_balance": float(money(opening["total_balance"])),
            "forecast_payments": float(money(paid_totals[member.account_id])),
            "projected_interest": float(projected_interest),
            "payoff_date": payoff_date,
            "remaining_balance": float(money(max(Decimal(str(final["total_balance"])), ZERO))),
        })

    if len(actual_payoff_dates) == len(members):
        global_payoff_date = max(actual_payoff_dates).isoformat()

    return {
        "plan": plan_dict(plan),
        "scenario": {
            "name": scenario_name,
            "changes": changes,
            "events": scenario_events,
        } if changes else None,
        "forecast": {
            "first_payment_date": plan.first_payment_date.isoformat(),
            "monthly_budget": float(baseline_budget),
            "base_payment_total": float(base_total),
            "rollover_available_initially": float(money(baseline_budget - base_total)),
            "strategy": plan.strategy,
            "horizon_months": horizon_months,
            "months_generated": len(schedule),
            "payoff_date": global_payoff_date,
            "total_forecast_payments": float(money(total_forecast_payments)),
            "projected_interest": float(money(projected_interest_total)),
            "remaining_balance": float(money(max(remaining_total, ZERO))),
            "accounts": account_summaries,
            "schedule": schedule,
            "scenario_events": scenario_events,
        },
        "non_destructive": True,
        "calculation_note": "Forecast assumptions are simulated in memory and replayed through the dated interest engine. No ledger or contractual rate entries are created.",
    }

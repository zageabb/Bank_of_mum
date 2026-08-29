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


def forecast_payment_plan(db: Session, plan: PaymentPlan, horizon_months: int = 240) -> dict:
    if horizon_months < 1 or horizon_months > 600:
        raise ValueError("Forecast horizon must be between 1 and 600 months")
    if plan.frequency != "monthly":
        raise ValueError("Phase 4 currently supports monthly payment plans")
    if plan.strategy != "priority_rollover":
        raise ValueError("Unsupported payment-plan strategy")

    members = [item for item in sorted(plan.members, key=lambda row: (row.priority, row.id)) if item.enabled]
    if not members:
        raise ValueError("The payment plan has no enabled accounts")
    base_total = money(sum((money(item.base_payment) for item in members), ZERO))
    budget = money(plan.monthly_budget)
    if budget <= 0:
        budget = base_total
    if budget < base_total:
        raise ValueError(
            f"Monthly budget £{budget:.2f} is below enabled base payments of £{base_total:.2f}"
        )

    virtual_payments: dict[int, list[dict]] = {item.account_id: [] for item in members}
    starting: dict[int, dict] = {
        item.account_id: calculate_account(db, item.account_id, plan.first_payment_date, virtual_payments[item.account_id])
        for item in members
    }
    payoff_dates: dict[int, str | None] = {item.account_id: None for item in members}
    paid_totals: dict[int, Decimal] = {item.account_id: ZERO for item in members}
    schedule: list[dict] = []
    global_payoff_date: str | None = None
    last_payment_date = plan.first_payment_date

    for period_index in range(horizon_months):
        payment_date = add_months(plan.first_payment_date, period_index)
        last_payment_date = payment_date
        before = {
            item.account_id: calculate_account(db, item.account_id, payment_date, virtual_payments[item.account_id])
            for item in members
        }
        active = [item for item in members if Decimal(str(before[item.account_id]["total_balance"])) > SETTLED_TOLERANCE]
        if not active:
            global_payoff_date = payment_date.isoformat()
            break

        remaining = budget
        allocations: dict[int, dict] = {}
        payment_sequence = 0

        def post_virtual(member: PaymentPlanAccount, requested: Decimal, component: str) -> Decimal:
            nonlocal payment_sequence
            if requested <= 0:
                return ZERO
            current = calculate_account(db, member.account_id, payment_date, virtual_payments[member.account_id])
            balance = money(max(Decimal(str(current["total_balance"])), ZERO))
            amount = money(min(requested, balance))
            if amount <= 0:
                return ZERO
            payment_sequence += 1
            forecast_key = f"plan-{plan.id}-{period_index + 1}-{member.account_id}-{payment_sequence}"
            virtual_payments[member.account_id].append({
                "effective_date": payment_date,
                "amount": amount,
                "note": f"{plan.name} forecast payment",
                "forecast_key": forecast_key,
            })
            recalculated = calculate_account(db, member.account_id, payment_date, virtual_payments[member.account_id])
            row = _find_forecast_row(recalculated, forecast_key)
            bucket = allocations.setdefault(member.account_id, {
                "account_id": member.account_id,
                "person": member.account.person.name,
                "account": member.account.name,
                "priority": member.priority,
                "base_component": ZERO,
                "rollover_component": ZERO,
                "amount": ZERO,
                "allocated_to_fees": ZERO,
                "allocated_to_interest": ZERO,
                "allocated_to_principal": ZERO,
                "balance_before": money(current["total_balance"]),
                "balance_after": money(recalculated["total_balance"]),
            })
            bucket[f"{component}_component"] += amount
            bucket["amount"] += amount
            bucket["allocated_to_fees"] += money(row.get("allocated_to_fees", 0))
            bucket["allocated_to_interest"] += money(row.get("allocated_to_interest", 0))
            bucket["allocated_to_principal"] += money(row.get("allocated_to_principal", 0))
            bucket["balance_after"] = money(recalculated["total_balance"])
            paid_totals[member.account_id] += amount
            return amount

        # Every active account receives its configured base payment first.
        for member in active:
            base_request = min(money(member.base_payment), remaining)
            paid = post_virtual(member, base_request, "base")
            remaining = money(remaining - paid)

        # Any budget not used by base payments rolls to the highest-priority debt still open.
        safety = 0
        while remaining > SETTLED_TOLERANCE:
            safety += 1
            if safety > len(members) + 4:
                break
            target = None
            for member in members:
                current = calculate_account(db, member.account_id, payment_date, virtual_payments[member.account_id])
                if Decimal(str(current["total_balance"])) > SETTLED_TOLERANCE:
                    target = member
                    break
            if target is None:
                break
            paid = post_virtual(target, remaining, "rollover")
            if paid <= 0:
                break
            remaining = money(remaining - paid)

        after = {
            item.account_id: calculate_account(db, item.account_id, payment_date, virtual_payments[item.account_id])
            for item in members
        }
        for member in members:
            was_open = Decimal(str(before[member.account_id]["total_balance"])) > SETTLED_TOLERANCE
            now_closed = Decimal(str(after[member.account_id]["total_balance"])) <= SETTLED_TOLERANCE
            if was_open and now_closed and payoff_dates[member.account_id] is None:
                payoff_dates[member.account_id] = payment_date.isoformat()

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
        schedule.append({
            "period": period_index + 1,
            "date": payment_date.isoformat(),
            "budget": float(budget),
            "used": float(used),
            "unused": float(money(remaining)),
            "remaining_balance": float(max(remaining_balance, ZERO)),
            "payments": period_payments,
        })
        if all(Decimal(str(after[item.account_id]["total_balance"])) <= SETTLED_TOLERANCE for item in members):
            global_payoff_date = payment_date.isoformat()
            break

    final_date = date.fromisoformat(global_payoff_date) if global_payoff_date else last_payment_date
    account_summaries: list[dict] = []
    projected_interest_total = ZERO
    total_forecast_payments = ZERO
    remaining_total = ZERO
    for member in members:
        final = calculate_account(db, member.account_id, final_date, virtual_payments[member.account_id])
        opening = starting[member.account_id]
        projected_interest = money(
            Decimal(str(final["total_interest_accrued"])) - Decimal(str(opening["total_interest_accrued"]))
        )
        projected_interest = max(projected_interest, ZERO)
        projected_interest_total += projected_interest
        total_forecast_payments += paid_totals[member.account_id]
        remaining_total += money(max(Decimal(str(final["total_balance"])), ZERO))
        account_summaries.append({
            "account_id": member.account_id,
            "person": member.account.person.name,
            "account": member.account.name,
            "priority": member.priority,
            "base_payment": float(money(member.base_payment)),
            "starting_balance": float(money(opening["total_balance"])),
            "forecast_payments": float(money(paid_totals[member.account_id])),
            "projected_interest": float(projected_interest),
            "payoff_date": payoff_dates[member.account_id],
            "remaining_balance": float(money(max(Decimal(str(final["total_balance"])), ZERO))),
        })

    return {
        "plan": plan_dict(plan),
        "forecast": {
            "first_payment_date": plan.first_payment_date.isoformat(),
            "monthly_budget": float(budget),
            "base_payment_total": float(base_total),
            "rollover_available_initially": float(money(budget - base_total)),
            "strategy": plan.strategy,
            "horizon_months": horizon_months,
            "months_generated": len(schedule),
            "payoff_date": global_payoff_date,
            "total_forecast_payments": float(money(total_forecast_payments)),
            "projected_interest": float(money(projected_interest_total)),
            "remaining_balance": float(money(max(remaining_total, ZERO))),
            "accounts": account_summaries,
            "schedule": schedule,
        },
        "non_destructive": True,
        "calculation_note": "Forecast payments are simulated in memory and replayed through the Phase 3 dated interest engine. No ledger entries are created.",
    }

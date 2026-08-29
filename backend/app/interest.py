from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ledger import money
from .models import Account, InterestRatePeriod, LedgerTransaction

RATE = Decimal("0.000001")
ZERO = Decimal("0.00")


def _rate(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(RATE, rounding=ROUND_HALF_UP)


def _days_30_360_us(start: date, end: date) -> int:
    d1 = min(start.day, 30)
    d2 = end.day
    if d1 == 30 and d2 == 31:
        d2 = 30
    return (end.year - start.year) * 360 + (end.month - start.month) * 30 + (d2 - d1)


def day_fraction(start: date, end: date, convention: str) -> Decimal:
    if end <= start:
        return Decimal("0")
    if convention == "30_360":
        return Decimal(_days_30_360_us(start, end)) / Decimal(360)
    days = Decimal((end - start).days)
    if convention == "actual_366":
        return days / Decimal(366)
    if convention == "actual_actual":
        cursor = start
        fraction = Decimal("0")
        while cursor < end:
            next_year = date(cursor.year + 1, 1, 1)
            segment_end = min(end, next_year)
            denominator = Decimal(366 if calendar.isleap(cursor.year) else 365)
            fraction += Decimal((segment_end - cursor).days) / denominator
            cursor = segment_end
        return fraction
    return days / Decimal(365)


def _rate_periods(db: Session, account: Account) -> list[InterestRatePeriod]:
    return db.scalars(
        select(InterestRatePeriod)
        .where(InterestRatePeriod.account_id == account.id)
        .order_by(InterestRatePeriod.effective_from, InterestRatePeriod.id)
    ).all()


def _active_period(periods: list, on_date: date):
    active = None
    for item in periods:
        if item.effective_from <= on_date:
            active = item
        else:
            break
    return active


def _interest_between(
    principal: Decimal,
    start: date,
    end: date,
    periods: list,
    fallback_rate: Decimal,
    fallback_convention: str,
) -> tuple[Decimal, list[dict]]:
    if principal <= 0 or end <= start:
        return ZERO, []

    boundaries = {start, end}
    for item in periods:
        if start < item.effective_from < end:
            boundaries.add(item.effective_from)
    ordered = sorted(boundaries)
    total = ZERO
    detail: list[dict] = []

    for index in range(len(ordered) - 1):
        segment_start, segment_end = ordered[index], ordered[index + 1]
        period = _active_period(periods, segment_start)
        annual_rate = _rate(period.annual_rate if period else fallback_rate)
        convention = period.day_count_convention if period else fallback_convention
        fraction = day_fraction(segment_start, segment_end, convention)
        accrued = (principal * annual_rate / Decimal(100) * fraction).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        total += accrued
        detail.append({
            "from": segment_start.isoformat(),
            "to": segment_end.isoformat(),
            "days": (segment_end - segment_start).days,
            "annual_rate": float(annual_rate),
            "day_count_convention": convention,
            "principal": float(money(principal)),
            "interest": float(accrued),
            "hypothetical_rate": bool(getattr(period, "is_hypothetical", False)) if period else False,
        })
    return total, detail


def calculate_account(
    db: Session,
    account_id: int,
    as_of: date | None = None,
    hypothetical_payments: list[dict] | None = None,
    hypothetical_rate_periods: list[dict] | None = None,
) -> dict:
    """Replay one account to a date with optional in-memory forecast assumptions.

    Hypothetical payments and rates never enter the SQLAlchemy session or immutable accounting
    tables. They are merged into the dated replay only for this calculation call.
    """
    account = db.get(Account, account_id)
    if not account:
        raise ValueError("Account not found")
    target = as_of or date.today()
    periods: list = list(_rate_periods(db, account))
    max_rate_id = max((item.id for item in periods), default=0)
    for index, assumed in enumerate(hypothetical_rate_periods or [], start=1):
        effective_from = assumed.get("effective_from")
        if isinstance(effective_from, str):
            effective_from = date.fromisoformat(effective_from)
        if not isinstance(effective_from, date):
            continue
        periods.append(SimpleNamespace(
            id=max_rate_id + index,
            account_id=account_id,
            effective_from=effective_from,
            annual_rate=_rate(assumed.get("annual_rate", 0)),
            day_count_convention=str(assumed.get("day_count_convention") or account.day_count_convention or "actual_365"),
            reason=str(assumed.get("reason") or "Scenario rate assumption"),
            created_by="scenario",
            created_at=None,
            is_hypothetical=True,
        ))
    periods.sort(key=lambda item: (item.effective_from, item.id))

    transactions = list(db.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id, LedgerTransaction.effective_date <= target)
        .order_by(LedgerTransaction.effective_date, LedgerTransaction.id)
    ).all())

    max_id = max((item.id for item in transactions), default=0)
    for index, payment in enumerate(hypothetical_payments or [], start=1):
        effective_date = payment.get("effective_date")
        if isinstance(effective_date, str):
            effective_date = date.fromisoformat(effective_date)
        if not isinstance(effective_date, date) or effective_date > target:
            continue
        transactions.append(SimpleNamespace(
            id=max_id + index,
            effective_date=effective_date,
            transaction_type="payment",
            direction="credit",
            amount=money(payment.get("amount", 0)),
            note=str(payment.get("note") or "Forecast payment"),
            reverses_transaction_id=None,
            is_hypothetical=True,
            forecast_key=str(payment.get("forecast_key") or f"forecast-{index}"),
        ))
    transactions.sort(key=lambda item: (item.effective_date, item.id))

    if account.start_date and account.start_date > target:
        return {
            "account_id": account.id,
            "as_of": target.isoformat(),
            "principal": 0.0,
            "accrued_interest": 0.0,
            "fees": 0.0,
            "unapplied_credit": 0.0,
            "total_balance": 0.0,
            "total_interest_accrued": 0.0,
            "total_interest_paid": 0.0,
            "timeline": [],
            "rate_periods": [rate_period_dict(item) for item in periods],
        }

    first_date = account.start_date or (transactions[0].effective_date if transactions else target)
    cursor = min(first_date, target)
    principal = ZERO
    accrued_interest = ZERO
    fees = ZERO
    unapplied_credit = ZERO
    total_interest_accrued = ZERO
    total_interest_paid = ZERO
    timeline: list[dict] = []
    allocation_by_transaction: dict[int, dict[str, Decimal]] = {}
    fallback_rate = _rate(account.annual_interest_rate or 0)
    fallback_convention = account.day_count_convention or "actual_365"

    def accrue(to_date: date) -> list[dict]:
        nonlocal cursor, accrued_interest, total_interest_accrued
        amount, detail = _interest_between(
            principal,
            cursor,
            to_date,
            periods,
            fallback_rate,
            fallback_convention,
        )
        accrued_interest += amount
        total_interest_accrued += amount
        cursor = to_date
        return detail

    for item in transactions:
        accrual_detail = accrue(item.effective_date)
        before = {
            "principal": principal,
            "interest": accrued_interest,
            "fees": fees,
            "credit": unapplied_credit,
        }
        deltas = {"principal": ZERO, "interest": ZERO, "fees": ZERO, "credit": ZERO}
        interest_paid = ZERO

        if item.reverses_transaction_id and item.reverses_transaction_id in allocation_by_transaction:
            original = allocation_by_transaction[item.reverses_transaction_id]
            for key in deltas:
                deltas[key] = -original.get(key, ZERO)
            principal += deltas["principal"]
            accrued_interest += deltas["interest"]
            fees += deltas["fees"]
            unapplied_credit += deltas["credit"]
            if deltas["interest"] > 0:
                total_interest_paid -= deltas["interest"]
        elif item.direction == "debit":
            value = money(item.amount)
            if item.transaction_type == "fee":
                fees += value
                deltas["fees"] += value
            elif item.transaction_type == "interest":
                accrued_interest += value
                total_interest_accrued += value
                deltas["interest"] += value
            else:
                principal += value
                deltas["principal"] += value
        else:
            remaining = money(item.amount)
            fee_paid = min(fees, remaining)
            fees -= fee_paid
            remaining -= fee_paid
            deltas["fees"] -= fee_paid

            interest_paid = min(accrued_interest, remaining)
            accrued_interest -= interest_paid
            remaining -= interest_paid
            deltas["interest"] -= interest_paid
            total_interest_paid += interest_paid

            principal_paid = min(principal, remaining)
            principal -= principal_paid
            remaining -= principal_paid
            deltas["principal"] -= principal_paid

            if remaining > 0:
                unapplied_credit += remaining
                deltas["credit"] += remaining

        allocation_by_transaction[item.id] = deltas.copy()
        total = money(principal + accrued_interest + fees - unapplied_credit)
        timeline.append({
            "transaction_id": item.id,
            "date": item.effective_date.isoformat(),
            "type": item.transaction_type,
            "direction": item.direction,
            "amount": float(money(item.amount)),
            "note": item.note,
            "reverses_transaction_id": item.reverses_transaction_id,
            "is_hypothetical": bool(getattr(item, "is_hypothetical", False)),
            "forecast_key": getattr(item, "forecast_key", ""),
            "interest_accrual_before_transaction": float(money(sum(Decimal(str(row["interest"])) for row in accrual_detail))),
            "interest_segments": accrual_detail,
            "allocated_to_fees": float(money(-deltas["fees"] if deltas["fees"] < 0 else ZERO)),
            "allocated_to_interest": float(money(interest_paid)),
            "allocated_to_principal": float(money(-deltas["principal"] if deltas["principal"] < 0 else ZERO)),
            "principal_after": float(money(principal)),
            "interest_after": float(money(accrued_interest)),
            "fees_after": float(money(fees)),
            "unapplied_credit_after": float(money(unapplied_credit)),
            "balance_after": float(total),
            "before": {key: float(money(value)) for key, value in before.items()},
        })

    final_segments = accrue(target)
    total_balance = money(principal + accrued_interest + fees - unapplied_credit)
    return {
        "account_id": account.id,
        "as_of": target.isoformat(),
        "principal": float(money(principal)),
        "accrued_interest": float(money(accrued_interest)),
        "fees": float(money(fees)),
        "unapplied_credit": float(money(unapplied_credit)),
        "total_balance": float(total_balance),
        "total_interest_accrued": float(money(total_interest_accrued)),
        "total_interest_paid": float(money(total_interest_paid)),
        "interest_since_last_transaction": float(money(sum(Decimal(str(row["interest"])) for row in final_segments))),
        "final_interest_segments": final_segments,
        "timeline": timeline,
        "rate_periods": [rate_period_dict(item) for item in periods],
        "interest_method": account.interest_method,
        "day_count_convention": account.day_count_convention,
        "payment_allocation": account.payment_allocation,
    }


def rate_period_dict(item) -> dict:
    return {
        "id": item.id,
        "account_id": item.account_id,
        "effective_from": item.effective_from.isoformat(),
        "annual_rate": float(item.annual_rate),
        "day_count_convention": item.day_count_convention,
        "reason": item.reason,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat() if getattr(item, "created_at", None) else None,
        "is_hypothetical": bool(getattr(item, "is_hypothetical", False)),
    }

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, LedgerTransaction


CREDIT_TYPES = {"payment"}
DEBIT_TYPES = {"advance", "fee", "interest", "adjustment"}


def signed_amount(transaction_type: str, amount: float) -> float:
    if transaction_type in CREDIT_TYPES:
        return -abs(float(amount))
    return abs(float(amount))


def account_balance(db: Session, account_id: int) -> float:
    transactions = db.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id, LedgerTransaction.voided_at.is_(None))
        .order_by(LedgerTransaction.effective_date, LedgerTransaction.id)
    ).all()
    return round(sum(signed_amount(item.transaction_type, item.amount) for item in transactions), 2)


def log_audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    summary: str,
    before_json: str = "",
    after_json: str = "",
    reason: str = "",
    actor: str = "local",
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        summary=summary,
        before_json=before_json,
        after_json=after_json,
        reason=reason,
        actor=actor,
    )
    db.add(event)
    return event

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, LedgerTransaction

MONEY = Decimal("0.01")
CREDIT_TYPES = {"payment", "refund_credit"}
DEBIT_TYPES = {"opening_balance", "advance", "fee", "interest", "refund_debit"}


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def default_direction(transaction_type: str) -> str:
    if transaction_type in CREDIT_TYPES:
        return "credit"
    if transaction_type in DEBIT_TYPES:
        return "debit"
    return "debit"


def signed_amount(direction: str, amount: Decimal | float) -> Decimal:
    value = money(amount)
    return -value if direction == "credit" else value


def _hash_payload(
    *,
    account_id: int,
    effective_date: date,
    transaction_type: str,
    direction: str,
    amount: Decimal | float,
    note: str,
    reference: str,
    source: str,
    created_by: str,
    reverses_transaction_id: int | None,
    correction_group: str,
    previous_hash: str,
) -> str:
    payload = {
        "account_id": account_id,
        "effective_date": effective_date.isoformat(),
        "transaction_type": transaction_type,
        "direction": direction,
        "amount": f"{money(amount):.2f}",
        "note": note or "",
        "reference": reference or "",
        "source": source or "manual",
        "created_by": created_by or "local",
        "reverses_transaction_id": reverses_transaction_id,
        "correction_group": correction_group or "",
        "previous_hash": previous_hash or "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def transaction_snapshot(item: LedgerTransaction) -> dict:
    return {
        "id": item.id,
        "account_id": item.account_id,
        "effective_date": item.effective_date.isoformat(),
        "transaction_type": item.transaction_type,
        "direction": item.direction,
        "amount": float(item.amount),
        "note": item.note,
        "reference": item.reference,
        "source": item.source,
        "created_by": item.created_by,
        "legacy_index": item.legacy_index,
        "reverses_transaction_id": item.reverses_transaction_id,
        "correction_group": item.correction_group,
        "previous_hash": item.previous_hash,
        "entry_hash": item.entry_hash,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def log_audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    action: str,
    summary: str,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    actor: str = "local",
) -> AuditEvent:
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        summary=summary,
        before_json=json.dumps(before or {}, sort_keys=True, default=str),
        after_json=json.dumps(after or {}, sort_keys=True, default=str),
        reason=reason,
        actor=actor,
    )
    db.add(event)
    return event


def append_transaction(
    db: Session,
    *,
    account_id: int,
    effective_date: date,
    transaction_type: str,
    amount: Decimal | float,
    direction: str | None = None,
    note: str = "",
    reference: str = "",
    source: str = "manual",
    created_by: str = "local",
    legacy_index: int | None = None,
    reverses_transaction_id: int | None = None,
    correction_group: str = "",
    audit_action: str = "created",
    audit_reason: str = "",
) -> LedgerTransaction:
    value = money(amount)
    entry_direction = direction or default_direction(transaction_type)
    if value < 0:
        if transaction_type != "payment":
            raise ValueError("Only payments may use a negative amount")
        value = abs(value)
        entry_direction = "debit"
    if value == 0:
        raise ValueError("Transaction amount must not be zero")
    if entry_direction not in {"debit", "credit"}:
        raise ValueError("Transaction direction must be debit or credit")

    previous = db.scalar(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id)
        .order_by(LedgerTransaction.id.desc())
        .limit(1)
    )
    previous_hash = previous.entry_hash if previous else ""
    group = correction_group or ""
    item = LedgerTransaction(
        account_id=account_id,
        effective_date=effective_date,
        transaction_type=transaction_type,
        direction=entry_direction,
        amount=value,
        note=note or "",
        reference=reference or "",
        source=source or "manual",
        created_by=created_by or "local",
        legacy_index=legacy_index,
        reverses_transaction_id=reverses_transaction_id,
        correction_group=group,
        previous_hash=previous_hash,
    )
    item.entry_hash = _hash_payload(
        account_id=account_id,
        effective_date=effective_date,
        transaction_type=transaction_type,
        direction=entry_direction,
        amount=value,
        note=item.note,
        reference=item.reference,
        source=item.source,
        created_by=item.created_by,
        reverses_transaction_id=reverses_transaction_id,
        correction_group=group,
        previous_hash=previous_hash,
    )
    db.add(item)
    db.flush()
    log_audit(
        db,
        entity_type="ledger_transaction",
        entity_id=item.id,
        action=audit_action,
        summary=f"{transaction_type.replace('_', ' ').title()} {entry_direction} £{value:.2f}",
        after=transaction_snapshot(item),
        reason=audit_reason,
        actor=created_by,
    )
    return item


def reverse_transaction(
    db: Session,
    original: LedgerTransaction,
    *,
    reason: str,
    effective_date: date | None = None,
    actor: str = "local",
    correction_group: str = "",
) -> LedgerTransaction:
    existing = db.scalar(
        select(LedgerTransaction).where(LedgerTransaction.reverses_transaction_id == original.id).limit(1)
    )
    if existing:
        raise ValueError(f"Transaction {original.id} has already been reversed by transaction {existing.id}")
    opposite = "credit" if original.direction == "debit" else "debit"
    group = correction_group or uuid4().hex
    reversal = append_transaction(
        db,
        account_id=original.account_id,
        effective_date=effective_date or original.effective_date,
        transaction_type="reversal",
        direction=opposite,
        amount=original.amount,
        note=f"Reversal of transaction #{original.id}: {reason}",
        reference=f"REV-{original.id}",
        source="correction",
        created_by=actor,
        reverses_transaction_id=original.id,
        correction_group=group,
        audit_action="reversal_created",
        audit_reason=reason,
    )
    log_audit(
        db,
        entity_type="ledger_transaction",
        entity_id=original.id,
        action="reversed",
        summary=f"Transaction #{original.id} reversed by #{reversal.id}",
        before=transaction_snapshot(original),
        after={"reversal_transaction_id": reversal.id, "correction_group": group},
        reason=reason,
        actor=actor,
    )
    return reversal


def correct_transaction(
    db: Session,
    original: LedgerTransaction,
    *,
    effective_date: date,
    transaction_type: str,
    amount: Decimal | float,
    note: str,
    reason: str,
    direction: str | None = None,
    actor: str = "local",
) -> tuple[LedgerTransaction, LedgerTransaction]:
    group = uuid4().hex
    reversal = reverse_transaction(
        db,
        original,
        reason=reason,
        effective_date=original.effective_date,
        actor=actor,
        correction_group=group,
    )
    replacement = append_transaction(
        db,
        account_id=original.account_id,
        effective_date=effective_date,
        transaction_type=transaction_type,
        direction=direction,
        amount=amount,
        note=note,
        reference=f"COR-{original.id}",
        source="correction",
        created_by=actor,
        correction_group=group,
        audit_action="correction_created",
        audit_reason=reason,
    )
    log_audit(
        db,
        entity_type="ledger_transaction",
        entity_id=original.id,
        action="corrected",
        summary=f"Transaction #{original.id} corrected with replacement #{replacement.id}",
        before=transaction_snapshot(original),
        after={"reversal_transaction_id": reversal.id, "replacement_transaction_id": replacement.id, "correction_group": group},
        reason=reason,
        actor=actor,
    )
    return reversal, replacement


def account_ledger(db: Session, account_id: int) -> list[dict]:
    rows = db.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id)
        .order_by(LedgerTransaction.effective_date, LedgerTransaction.id)
    ).all()
    running = Decimal("0.00")
    result: list[dict] = []
    reversed_ids = {item.reverses_transaction_id for item in rows if item.reverses_transaction_id is not None}
    for item in rows:
        delta = signed_amount(item.direction, item.amount)
        running = money(running + delta)
        snapshot = transaction_snapshot(item)
        snapshot.update({
            "delta": float(delta),
            "running_balance": float(running),
            "is_reversed": item.id in reversed_ids,
            "is_reversal": item.reverses_transaction_id is not None,
        })
        result.append(snapshot)
    return result


def account_balance(db: Session, account_id: int) -> float:
    rows = account_ledger(db, account_id)
    return rows[-1]["running_balance"] if rows else 0.0


def backfill_account_chain(db: Session, account_id: int) -> int:
    """Populate/rebuild the hash chain before immutability triggers are installed."""
    rows = db.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id)
        .order_by(LedgerTransaction.id)
    ).all()
    expected_previous = ""
    changed = 0
    for item in rows:
        calculated = _hash_payload(
            account_id=item.account_id,
            effective_date=item.effective_date,
            transaction_type=item.transaction_type,
            direction=item.direction or default_direction(item.transaction_type),
            amount=item.amount,
            note=item.note or "",
            reference=item.reference or "",
            source=item.source or "manual",
            created_by=item.created_by or "local",
            reverses_transaction_id=item.reverses_transaction_id,
            correction_group=item.correction_group or "",
            previous_hash=expected_previous,
        )
        if item.direction not in {"debit", "credit"}:
            item.direction = default_direction(item.transaction_type)
            changed += 1
        if item.previous_hash != expected_previous:
            item.previous_hash = expected_previous
            changed += 1
        if item.entry_hash != calculated:
            item.entry_hash = calculated
            changed += 1
        expected_previous = calculated
    return changed


def verify_account_chain(db: Session, account_id: int) -> dict:
    rows = db.scalars(
        select(LedgerTransaction)
        .where(LedgerTransaction.account_id == account_id)
        .order_by(LedgerTransaction.id)
    ).all()
    expected_previous = ""
    failures: list[int] = []
    for item in rows:
        calculated = _hash_payload(
            account_id=item.account_id,
            effective_date=item.effective_date,
            transaction_type=item.transaction_type,
            direction=item.direction,
            amount=item.amount,
            note=item.note,
            reference=item.reference,
            source=item.source,
            created_by=item.created_by,
            reverses_transaction_id=item.reverses_transaction_id,
            correction_group=item.correction_group,
            previous_hash=expected_previous,
        )
        if item.previous_hash != expected_previous or item.entry_hash != calculated:
            failures.append(item.id)
        expected_previous = item.entry_hash
    return {"ok": not failures, "entries": len(rows), "failed_transaction_ids": failures, "head_hash": expected_previous}

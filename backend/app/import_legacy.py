from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Account, LedgerTransaction, Person

SAMPLE_IDS = {"alice", "bob"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _person_and_account_name(legacy_id: str, payload: dict) -> tuple[str, str]:
    display = str(payload.get("child") or legacy_id.replace("_", " ").strip()).strip()
    if " - " in display:
        person, account = display.split(" - ", 1)
        return person.strip(), account.strip()
    if "_-_" in legacy_id:
        left, right = legacy_id.split("_-_", 1)
        return left.replace("_", " ").title(), right.replace("_", " ").title()
    return display, "Loan"


def import_legacy_json(db: Session, root: Path | None = None) -> dict:
    source_root = (root or settings.legacy_data_root).resolve()
    if not source_root.exists():
        return {"imported_accounts": 0, "imported_payments": 0, "skipped": [], "source": str(source_root)}

    imported_accounts = 0
    imported_payments = 0
    skipped: list[str] = []

    for path in sorted(source_root.glob("*.json")):
        legacy_id = path.stem
        if legacy_id.lower() in SAMPLE_IDS:
            skipped.append(legacy_id)
            continue
        if db.scalar(select(Account).where(Account.legacy_id == legacy_id)):
            skipped.append(legacy_id)
            continue

        payload = json.loads(path.read_text(encoding="utf-8"))
        person_name, account_name = _person_and_account_name(legacy_id, payload)
        person = db.scalar(select(Person).where(Person.name == person_name))
        if not person:
            person = Person(name=person_name)
            db.add(person)
            db.flush()

        account = Account(
            person_id=person.id,
            name=account_name,
            opening_principal=float(payload.get("principal") or 0),
            annual_interest_rate=float(payload.get("interest_rate") or 0),
            regular_payment=float(payload.get("payment_per_month") or 0),
            start_date=_parse_date(payload.get("start_date")),
            legacy_id=legacy_id,
        )
        db.add(account)
        db.flush()
        imported_accounts += 1

        for index, payment in enumerate(payload.get("payments") or []):
            paid_on = _parse_date(payment.get("date")) or account.start_date or date.today()
            db.add(LedgerTransaction(
                account_id=account.id,
                effective_date=paid_on,
                transaction_type="payment",
                amount=float(payment.get("amount") or 0),
                note=str(payment.get("comment") or ""),
                source="legacy_import",
                legacy_index=index,
            ))
            imported_payments += 1

    db.commit()
    return {"imported_accounts": imported_accounts, "imported_payments": imported_payments, "skipped": skipped, "source": str(source_root)}

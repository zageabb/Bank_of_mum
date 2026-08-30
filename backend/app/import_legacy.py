from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .ledger import append_transaction, log_audit
from .models import Account, InterestRatePeriod, Person

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
    imported_openings = 0
    imported_rates = 0
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
            log_audit(
                db,
                entity_type="person",
                entity_id=person.id,
                action="legacy_import_created",
                summary=f"Imported person {person.name}",
                after={"name": person.name},
                actor="legacy_import",
            )

        principal = float(payload.get("principal") or 0)
        annual_rate = Decimal(str(payload.get("interest_rate") or 0))
        account = Account(
            person_id=person.id,
            name=account_name,
            opening_principal=principal,
            annual_interest_rate=float(annual_rate),
            regular_payment=float(payload.get("payment_per_month") or 0),
            start_date=_parse_date(payload.get("start_date")),
            legacy_id=legacy_id,
            interest_method="daily_simple",
            day_count_convention="actual_365",
            payment_allocation="fees_interest_principal",
        )
        db.add(account)
        db.flush()
        imported_accounts += 1
        log_audit(
            db,
            entity_type="account",
            entity_id=account.id,
            action="legacy_import_created",
            summary=f"Imported account {person.name} · {account.name}",
            after={
                "person": person.name,
                "name": account.name,
                "opening_principal": principal,
                "annual_interest_rate": account.annual_interest_rate,
                "regular_payment": account.regular_payment,
                "start_date": account.start_date,
                "legacy_id": legacy_id,
            },
            actor="legacy_import",
        )

        opening_date = account.start_date or date.today()
        rate_period = InterestRatePeriod(
            account_id=account.id,
            effective_from=opening_date,
            annual_rate=annual_rate,
            day_count_convention=account.day_count_convention,
            reason="Imported legacy contractual rate",
            created_by="legacy_import",
        )
        db.add(rate_period)
        db.flush()
        imported_rates += 1
        log_audit(
            db,
            entity_type="interest_rate_period",
            entity_id=rate_period.id,
            action="legacy_rate_imported",
            summary=f"Imported rate {annual_rate}% effective {opening_date.isoformat()}",
            after={
                "account_id": account.id,
                "effective_from": opening_date.isoformat(),
                "annual_rate": str(annual_rate),
                "day_count_convention": account.day_count_convention,
            },
            actor="legacy_import",
        )

        if principal > 0:
            append_transaction(
                db,
                account_id=account.id,
                effective_date=opening_date,
                transaction_type="opening_balance",
                direction="debit",
                amount=principal,
                note="Opening principal imported from legacy Bank of Mum",
                reference=f"LEGACY-{legacy_id}-OPEN",
                source="legacy_import",
                created_by="legacy_import",
                audit_action="legacy_opening_imported",
            )
            imported_openings += 1

        for index, payment in enumerate(payload.get("payments") or []):
            amount = float(payment.get("amount") or 0)
            if amount == 0:
                continue
            paid_on = _parse_date(payment.get("date")) or opening_date
            append_transaction(
                db,
                account_id=account.id,
                effective_date=paid_on,
                transaction_type="payment",
                direction="debit" if amount < 0 else "credit",
                amount=amount,
                note=str(payment.get("comment") or ""),
                reference=f"LEGACY-{legacy_id}-PAY-{index + 1}",
                source="legacy_import",
                created_by="legacy_import",
                legacy_index=index,
                audit_action="legacy_payment_imported",
            )
            imported_payments += 1

    db.commit()
    return {
        "imported_accounts": imported_accounts,
        "imported_opening_balances": imported_openings,
        "imported_interest_rates": imported_rates,
        "imported_payments": imported_payments,
        "skipped": skipped,
        "source": str(source_root),
    }

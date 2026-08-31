from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, engine, get_db
from .interest import calculate_account, rate_period_dict
from .ledger import append_transaction, log_audit, money, verify_account_chain
from .models import Account, AuditEvent, InterestRatePeriod, LedgerTransaction, PaymentPlanAccount, Person, ScenarioChange

router = APIRouter(prefix="/api", tags=["phase7"])
BACKUP_DIR = settings.data_root / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = settings.data_root / "bank-of-mum.db"


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    notes: str = ""


class PersonUpdate(PersonCreate):
    reason: str = Field(min_length=3, max_length=500)


class AccountCreate(BaseModel):
    person_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=180)
    account_type: str = Field(default="loan", min_length=1, max_length=40)
    opening_principal: Decimal = Field(default=Decimal("0.00"), ge=0)
    annual_interest_rate: Decimal = Field(default=Decimal("0.00"), ge=0, le=100)
    regular_payment: Decimal = Field(default=Decimal("0.00"), ge=0)
    start_date: date
    day_count_convention: str = Field(default="actual_365", pattern="^(actual_365|actual_366|actual_actual|30_360)$")


class AccountUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    account_type: str = Field(default="loan", min_length=1, max_length=40)
    regular_payment: Decimal = Field(default=Decimal("0.00"), ge=0)
    status: str = Field(default="active", pattern="^(active|paused|archived|settled)$")
    reason: str = Field(min_length=3, max_length=500)


class RestoreRequest(BaseModel):
    confirmation: str = Field(min_length=7, max_length=80)


def _person_dict(person: Person) -> dict:
    return {
        "id": person.id,
        "name": person.name,
        "notes": person.notes,
        "created_at": person.created_at.isoformat() if person.created_at else None,
        "accounts": len(person.accounts),
        "active_accounts": sum(1 for item in person.accounts if item.status not in {"archived", "settled"}),
    }


def _account_dict(db: Session, account: Account, as_of: date | None = None) -> dict:
    calculation = calculate_account(db, account.id, as_of)
    return {
        "id": account.id,
        "person_id": account.person_id,
        "person": account.person.name,
        "name": account.name,
        "account_type": account.account_type,
        "opening_principal": float(account.opening_principal),
        "annual_interest_rate": float(account.annual_interest_rate),
        "regular_payment": float(account.regular_payment),
        "start_date": account.start_date.isoformat() if account.start_date else None,
        "status": account.status,
        "day_count_convention": account.day_count_convention,
        "principal": calculation["principal"],
        "accrued_interest": calculation["accrued_interest"],
        "fees": calculation["fees"],
        "balance": calculation["total_balance"],
        "calculated_as_of": calculation["as_of"],
    }


def _period_totals(db: Session, account_id: int, start: date, end: date) -> tuple[dict, dict, float, float]:
    if end < start:
        raise ValueError("Report end date cannot be before start date")
    opening_day = start - timedelta(days=1)
    opening = calculate_account(db, account_id, opening_day)
    closing = calculate_account(db, account_id, end)
    interest_accrued = round(closing["total_interest_accrued"] - opening["total_interest_accrued"], 2)
    interest_paid = round(closing["total_interest_paid"] - opening["total_interest_paid"], 2)
    return opening, closing, interest_accrued, interest_paid


def account_statement(db: Session, account: Account, start: date, end: date) -> dict:
    opening, closing, interest_accrued, interest_paid = _period_totals(db, account.id, start, end)
    timeline = [
        row for row in closing["timeline"]
        if start <= date.fromisoformat(row["date"]) <= end and not row.get("is_hypothetical")
    ]
    debit_total = round(sum(row["amount"] for row in timeline if row["direction"] == "debit"), 2)
    credit_total = round(sum(row["amount"] for row in timeline if row["direction"] == "credit"), 2)
    return {
        "account": _account_dict(db, account, end),
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "opening": {
            "date": (start - timedelta(days=1)).isoformat(),
            "principal": opening["principal"],
            "interest": opening["accrued_interest"],
            "fees": opening["fees"],
            "balance": opening["total_balance"],
        },
        "closing": {
            "date": end.isoformat(),
            "principal": closing["principal"],
            "interest": closing["accrued_interest"],
            "fees": closing["fees"],
            "balance": closing["total_balance"],
        },
        "interest_accrued": interest_accrued,
        "interest_paid": interest_paid,
        "debits": debit_total,
        "credits": credit_total,
        "transactions": timeline,
        "immutable_source": True,
    }


def annual_interest_summary(db: Session, year: int) -> dict:
    if year < 2000 or year > 2200:
        raise ValueError("Year must be between 2000 and 2200")
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    rows: list[dict] = []
    for account in db.scalars(select(Account).order_by(Account.person_id, Account.id)).all():
        opening, closing, accrued, paid = _period_totals(db, account.id, start, end)
        rows.append({
            "account_id": account.id,
            "person": account.person.name,
            "account": account.name,
            "status": account.status,
            "opening_balance": opening["total_balance"],
            "closing_balance": closing["total_balance"],
            "interest_accrued": accrued,
            "interest_paid": paid,
            "principal_at_year_end": closing["principal"],
        })
    return {
        "year": year,
        "accounts": rows,
        "totals": {
            "opening_balance": round(sum(row["opening_balance"] for row in rows), 2),
            "closing_balance": round(sum(row["closing_balance"] for row in rows), 2),
            "interest_accrued": round(sum(row["interest_accrued"] for row in rows), 2),
            "interest_paid": round(sum(row["interest_paid"] for row in rows), 2),
            "principal_at_year_end": round(sum(row["principal_at_year_end"] for row in rows), 2),
        },
    }


def _csv_response(filename: str, rows: list[list[object]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _backup_filename(prefix: str = "bank-of-mum") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.zip"


def _sqlite_integrity(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def create_backup(prefix: str = "bank-of-mum") -> Path:
    filename = _backup_filename(prefix)
    target = BACKUP_DIR / filename
    with tempfile.TemporaryDirectory() as temp_dir:
        snapshot = Path(temp_dir) / "bank-of-mum.db"
        with sqlite3.connect(DATABASE_PATH) as source, sqlite3.connect(snapshot) as destination:
            source.backup(destination)
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        manifest = {
            "app": "Bank of Mum",
            "phase": 8,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": "bank-of-mum.db",
            "sha256": digest,
            "integrity": _sqlite_integrity(snapshot),
        }
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "bank-of-mum.db")
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return target


def validate_backup(path: Path) -> dict:
    if not path.exists() or path.parent.resolve() != BACKUP_DIR.resolve() or path.suffix.lower() != ".zip":
        raise ValueError("Backup not found")
    with tempfile.TemporaryDirectory() as temp_dir, zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "bank-of-mum.db" not in names or "manifest.json" not in names:
            raise ValueError("Backup archive is missing required files")
        archive.extract("bank-of-mum.db", temp_dir)
        manifest = json.loads(archive.read("manifest.json"))
        snapshot = Path(temp_dir) / "bank-of-mum.db"
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        if manifest.get("sha256") != digest:
            raise ValueError("Backup checksum does not match")
        integrity = _sqlite_integrity(snapshot)
        if integrity.lower() != "ok":
            raise ValueError(f"Backup SQLite integrity check failed: {integrity}")
        return {**manifest, "filename": path.name, "valid": True}


@router.get("/people")
def list_people(db: Session = Depends(get_db)):
    rows = db.scalars(select(Person).order_by(Person.name)).all()
    return {"people": [_person_dict(item) for item in rows]}


@router.post("/people")
def create_person(body: PersonCreate, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Person).where(func.lower(Person.name) == name.lower()).limit(1)):
        raise HTTPException(409, "A person with this name already exists")
    person = Person(name=name, notes=body.notes)
    db.add(person)
    db.flush()
    log_audit(db, entity_type="person", entity_id=person.id, action="person_created", summary=f"Person {name} created", after=_person_dict(person), reason="Person created", actor="local")
    db.commit()
    return {"person": _person_dict(person)}


@router.put("/people/{person_id}")
def update_person(person_id: int, body: PersonUpdate, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    duplicate = db.scalar(select(Person).where(func.lower(Person.name) == body.name.strip().lower(), Person.id != person_id).limit(1))
    if duplicate:
        raise HTTPException(409, "A person with this name already exists")
    before = _person_dict(person)
    person.name = body.name.strip()
    person.notes = body.notes
    db.flush()
    after = _person_dict(person)
    log_audit(db, entity_type="person", entity_id=person.id, action="person_updated", summary=f"Person {person.name} updated", before=before, after=after, reason=body.reason, actor="local")
    db.commit()
    return {"person": _person_dict(person)}


@router.get("/managed-accounts")
def managed_accounts(include_archived: bool = False, db: Session = Depends(get_db)):
    query = select(Account)
    if not include_archived:
        query = query.where(Account.status != "archived")
    rows = db.scalars(query.order_by(Account.person_id, Account.id)).all()
    return {"accounts": [_account_dict(db, item) for item in rows]}


@router.post("/managed-accounts")
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    person = db.get(Person, body.person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    account = Account(
        person=person,
        name=body.name.strip(),
        account_type=body.account_type.strip(),
        opening_principal=float(money(body.opening_principal)),
        annual_interest_rate=float(body.annual_interest_rate),
        regular_payment=float(money(body.regular_payment)),
        start_date=body.start_date,
        status="active",
        day_count_convention=body.day_count_convention,
    )
    db.add(account)
    db.flush()
    if body.opening_principal > 0:
        append_transaction(
            db,
            account_id=account.id,
            effective_date=body.start_date,
            transaction_type="opening_balance",
            amount=body.opening_principal,
            direction="debit",
            note="Opening principal",
            reference=f"ACCOUNT-{account.id}-OPEN",
            source="account_setup",
            created_by="local",
            audit_reason="Account opening balance",
        )
    rate = InterestRatePeriod(
        account_id=account.id,
        effective_from=body.start_date,
        annual_rate=body.annual_interest_rate,
        day_count_convention=body.day_count_convention,
        reason="Initial account rate",
        created_by="local",
    )
    db.add(rate)
    db.flush()
    log_audit(db, entity_type="interest_rate_period", entity_id=rate.id, action="rate_created", summary=f"Initial interest rate {body.annual_interest_rate}% created", after=rate_period_dict(rate), reason="Account creation", actor="local")
    log_audit(db, entity_type="account", entity_id=account.id, action="account_created", summary=f"Account {person.name} · {account.name} created", after=_account_dict(db, account, body.start_date), reason="Account created", actor="local")
    db.commit()
    return {"account": _account_dict(db, account)}


@router.put("/managed-accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    before = _account_dict(db, account)
    account.name = body.name.strip()
    account.account_type = body.account_type.strip()
    account.regular_payment = float(money(body.regular_payment))
    account.status = body.status
    db.flush()
    after = _account_dict(db, account)
    log_audit(db, entity_type="account", entity_id=account.id, action="account_updated", summary=f"Account {account.person.name} · {account.name} updated", before=before, after=after, reason=body.reason, actor="local")
    db.commit()
    return {"account": _account_dict(db, account)}


@router.get("/reports/accounts/{account_id}/statement")
def statement(account_id: int, from_date: date = Query(...), to_date: date = Query(...), db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    try:
        return account_statement(db, account, from_date, to_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/reports/accounts/{account_id}/statement.csv")
def statement_csv(account_id: int, from_date: date = Query(...), to_date: date = Query(...), db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    report = account_statement(db, account, from_date, to_date)
    rows: list[list[object]] = [["Date", "Type", "Direction", "Amount", "Interest accrued before", "To fees", "To interest", "To principal", "Balance after", "Note"]]
    for item in report["transactions"]:
        rows.append([item["date"], item["type"], item["direction"], item["amount"], item["interest_accrual_before_transaction"], item["allocated_to_fees"], item["allocated_to_interest"], item["allocated_to_principal"], item["balance_after"], item["note"]])
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in f"{account.person.name}-{account.name}")
    return _csv_response(f"{safe_name}-{from_date}-{to_date}.csv", rows)


@router.get("/reports/annual-interest/{year}")
def annual_interest(year: int, db: Session = Depends(get_db)):
    try:
        return annual_interest_summary(db, year)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/reports/annual-interest/{year}.csv")
def annual_interest_csv(year: int, db: Session = Depends(get_db)):
    report = annual_interest_summary(db, year)
    rows: list[list[object]] = [["Person", "Account", "Status", "Opening balance", "Interest accrued", "Interest paid", "Closing balance", "Principal at year end"]]
    for item in report["accounts"]:
        rows.append([item["person"], item["account"], item["status"], item["opening_balance"], item["interest_accrued"], item["interest_paid"], item["closing_balance"], item["principal_at_year_end"]])
    return _csv_response(f"bank-of-mum-interest-{year}.csv", rows)


@router.get("/maintenance/backups")
def list_backups():
    rows = []
    for path in sorted(BACKUP_DIR.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            details = validate_backup(path)
            rows.append({**details, "size": path.stat().st_size})
        except Exception as exc:
            rows.append({"filename": path.name, "valid": False, "error": str(exc), "size": path.stat().st_size})
    return {"backups": rows}


@router.post("/maintenance/backups")
def create_backup_api(db: Session = Depends(get_db)):
    target = create_backup()
    details = validate_backup(target)
    log_audit(db, entity_type="maintenance", entity_id=0, action="backup_created", summary=f"Backup {target.name} created", after=details, reason="Manual backup", actor="local")
    db.commit()
    return {"backup": {**details, "size": target.stat().st_size}}


@router.get("/maintenance/backups/{filename}")
def download_backup(filename: str):
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(400, "Invalid backup filename")
    path = BACKUP_DIR / safe
    try:
        validate_backup(path)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=safe, media_type="application/zip")


@router.post("/maintenance/backups/{filename}/restore")
def restore_backup(filename: str, body: RestoreRequest):
    if body.confirmation != "RESTORE":
        raise HTTPException(422, "Type RESTORE exactly to confirm")
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(400, "Invalid backup filename")
    path = BACKUP_DIR / safe
    try:
        details = validate_backup(path)
        pre_restore = create_backup("pre-restore")
        with tempfile.TemporaryDirectory() as temp_dir, zipfile.ZipFile(path) as archive:
            archive.extract("bank-of-mum.db", temp_dir)
            source_path = Path(temp_dir) / "bank-of-mum.db"
            engine.dispose()
            with sqlite3.connect(source_path) as source, sqlite3.connect(DATABASE_PATH) as destination:
                source.backup(destination)
        with SessionLocal() as db:
            log_audit(db, entity_type="maintenance", entity_id=0, action="backup_restored", summary=f"Backup {safe} restored", after={"restored": details, "pre_restore_backup": pre_restore.name}, reason="Confirmed restore", actor="local")
            db.commit()
        return {"restored": details, "pre_restore_backup": pre_restore.name, "message": "Backup restored successfully"}
    except (ValueError, zipfile.BadZipFile, sqlite3.DatabaseError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/maintenance/verification")
def maintenance_verification(db: Session = Depends(get_db)):
    integrity = db.execute(text("PRAGMA integrity_check")).scalar_one()
    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    ledger_checks = [verify_account_chain(db, account.id) for account in accounts]
    missing_opening = []
    missing_rate = []
    for account in accounts:
        if account.opening_principal > 0 and not db.scalar(select(LedgerTransaction.id).where(LedgerTransaction.account_id == account.id, LedgerTransaction.transaction_type == "opening_balance").limit(1)):
            missing_opening.append(account.id)
        if not db.scalar(select(InterestRatePeriod.id).where(InterestRatePeriod.account_id == account.id).limit(1)):
            missing_rate.append(account.id)
    orphan_plan_members = db.scalar(select(func.count(PaymentPlanAccount.id)).where(~PaymentPlanAccount.account_id.in_(select(Account.id)))) or 0
    orphan_scenario_changes = db.scalar(select(func.count(ScenarioChange.id)).where(ScenarioChange.account_id.is_not(None), ~ScenarioChange.account_id.in_(select(Account.id)))) or 0
    warnings = []
    if missing_opening:
        warnings.append(f"Accounts missing opening ledger entries: {missing_opening}")
    if missing_rate:
        warnings.append(f"Accounts missing rate history: {missing_rate}")
    if orphan_plan_members:
        warnings.append(f"Orphan payment plan members: {orphan_plan_members}")
    if orphan_scenario_changes:
        warnings.append(f"Orphan scenario changes: {orphan_scenario_changes}")
    failed_hashes = [row for row in ledger_checks if not row.get("ok")]
    if failed_hashes:
        warnings.append(f"Ledger hash failures: {len(failed_hashes)}")
    return {
        "phase": 8,
        "database_integrity": integrity,
        "ledger_integrity": ledger_checks,
        "counts": {
            "people": db.scalar(select(func.count(Person.id))) or 0,
            "accounts": len(accounts),
            "transactions": db.scalar(select(func.count(LedgerTransaction.id))) or 0,
            "interest_rate_periods": db.scalar(select(func.count(InterestRatePeriod.id))) or 0,
            "audit_events": db.scalar(select(func.count(AuditEvent.id))) or 0,
        },
        "warnings": warnings,
        "ok": str(integrity).lower() == "ok" and not failed_hashes and not warnings,
    }

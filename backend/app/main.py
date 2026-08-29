from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .import_legacy import import_legacy_json
from .interest import calculate_account, rate_period_dict
from .ledger import (
    account_balance,
    account_ledger,
    append_transaction,
    correct_transaction,
    log_audit,
    reverse_transaction,
    transaction_snapshot,
    verify_account_chain,
)
from .migrations import (
    install_immutability_guards,
    prepare_phase2_data,
    prepare_phase3_data,
    run_phase2_schema_migrations,
    run_phase3_schema_migrations,
)
from .models import Account, AuditEvent, InterestRatePeriod, LedgerTransaction, Person
from .schemas import (
    AccountInterestSettingsUpdate,
    InterestRateCreate,
    TransactionCorrection,
    TransactionCreate,
    TransactionReverse,
)

Base.metadata.create_all(engine)
run_phase2_schema_migrations(engine)
run_phase3_schema_migrations(engine)
with SessionLocal() as _startup_db:
    prepare_phase2_data(_startup_db)
    prepare_phase3_data(_startup_db)
install_immutability_guards(engine)

app = FastAPI(title=settings.app_name, version="2.0.0-phase3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _account_dict(db: Session, account: Account, as_of: date | None = None) -> dict:
    calculation = calculate_account(db, account.id, as_of)
    return {
        "id": account.id,
        "person": account.person.name,
        "name": account.name,
        "opening_principal": account.opening_principal,
        "annual_interest_rate": account.annual_interest_rate,
        "regular_payment": account.regular_payment,
        "start_date": account.start_date,
        "status": account.status,
        "interest_method": account.interest_method,
        "day_count_convention": account.day_count_convention,
        "payment_allocation": account.payment_allocation,
        "current_balance": calculation["total_balance"],
        "principal_balance": calculation["principal"],
        "accrued_interest": calculation["accrued_interest"],
        "fees": calculation["fees"],
        "nominal_ledger_balance": account_balance(db, account.id),
        "calculated_as_of": calculation["as_of"],
    }


def _audit_dict(item: AuditEvent) -> dict:
    def decode(value: str) -> dict:
        try:
            return json.loads(value or "{}")
        except json.JSONDecodeError:
            return {"raw": value}

    return {
        "id": item.id,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "action": item.action,
        "summary": item.summary,
        "before": decode(item.before_json),
        "after": decode(item.after_json),
        "reason": item.reason,
        "actor": item.actor,
        "created_at": item.created_at,
    }


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": settings.app_name,
        "version": "2.0.0-phase3",
        "ledger_immutable": True,
        "interest_engine": "daily_simple",
    }


@app.get("/api/bootstrap")
def bootstrap(db: Session = Depends(get_db)):
    people = db.scalars(select(Person).order_by(Person.name)).all()
    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    payment_total = db.scalar(
        select(func.coalesce(func.sum(LedgerTransaction.amount), Decimal("0.00"))).where(
            LedgerTransaction.transaction_type == "payment",
            LedgerTransaction.direction == "credit",
        )
    ) or Decimal("0.00")
    calculations = [calculate_account(db, item.id) for item in accounts]
    audit_count = db.scalar(select(func.count(AuditEvent.id))) or 0
    return {
        "summary": {
            "people": len(people),
            "accounts": len(accounts),
            "opening_principal": round(sum(item.opening_principal for item in accounts), 2),
            "recorded_payments": round(float(payment_total), 2),
            "ledger_balance": round(sum(item["total_balance"] for item in calculations), 2),
            "outstanding_principal": round(sum(item["principal"] for item in calculations), 2),
            "accrued_interest": round(sum(item["accrued_interest"] for item in calculations), 2),
            "total_interest_accrued": round(sum(item["total_interest_accrued"] for item in calculations), 2),
            "total_interest_paid": round(sum(item["total_interest_paid"] for item in calculations), 2),
            "audit_events": int(audit_count),
        },
        "people": [{"id": p.id, "name": p.name, "accounts": len(p.accounts)} for p in people],
        "accounts": [_account_dict(db, item) for item in accounts],
        "settings": {"ollama_url": settings.ollama_url, "ollama_model": settings.ollama_model},
        "phase": 3,
        "balance_note": "Balances now include deterministic date-sensitive accrued interest. Every payment causes the account to be replayed from its dated ledger history.",
    }


@app.post("/api/admin/import-legacy")
def import_legacy(db: Session = Depends(get_db)):
    return import_legacy_json(db)


@app.get("/api/accounts/{account_id}")
def account_detail(account_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        **_account_dict(db, account, as_of),
        "transactions": account_ledger(db, account.id),
        "calculation": calculate_account(db, account.id, as_of),
        "integrity": verify_account_chain(db, account.id),
    }


@app.get("/api/accounts/{account_id}/calculation")
def account_calculation(account_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    try:
        return calculate_account(db, account_id, as_of)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/accounts/{account_id}/rates")
def account_rates(account_id: int, db: Session = Depends(get_db)):
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    rows = db.scalars(
        select(InterestRatePeriod)
        .where(InterestRatePeriod.account_id == account_id)
        .order_by(InterestRatePeriod.effective_from, InterestRatePeriod.id)
    ).all()
    return {"rates": [rate_period_dict(item) for item in rows], "immutable": True}


@app.post("/api/accounts/{account_id}/rates")
def create_interest_rate(account_id: int, body: InterestRateCreate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    previous = db.scalar(
        select(InterestRatePeriod)
        .where(
            InterestRatePeriod.account_id == account_id,
            InterestRatePeriod.effective_from <= body.effective_from,
        )
        .order_by(InterestRatePeriod.effective_from.desc(), InterestRatePeriod.id.desc())
        .limit(1)
    )
    item = InterestRatePeriod(
        account_id=account_id,
        effective_from=body.effective_from,
        annual_rate=body.annual_rate,
        day_count_convention=body.day_count_convention,
        reason=body.reason,
        created_by="local",
    )
    db.add(item)
    db.flush()
    log_audit(
        db,
        entity_type="interest_rate_period",
        entity_id=item.id,
        action="rate_created",
        summary=f"Interest rate {body.annual_rate}% effective {body.effective_from.isoformat()}",
        before=rate_period_dict(previous) if previous else {},
        after=rate_period_dict(item),
        reason=body.reason,
        actor="local",
    )
    db.commit()
    return {
        "rate": rate_period_dict(item),
        "calculation": calculate_account(db, account_id),
        "message": "Rate periods are append-only. Add another period to supersede this rate from a chosen effective date.",
    }


@app.put("/api/accounts/{account_id}/interest-settings")
def update_interest_settings(account_id: int, body: AccountInterestSettingsUpdate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    before = {
        "interest_method": account.interest_method,
        "day_count_convention": account.day_count_convention,
        "payment_allocation": account.payment_allocation,
    }
    account.interest_method = body.interest_method
    account.day_count_convention = body.day_count_convention
    account.payment_allocation = body.payment_allocation
    after = {
        "interest_method": account.interest_method,
        "day_count_convention": account.day_count_convention,
        "payment_allocation": account.payment_allocation,
    }
    log_audit(
        db,
        entity_type="account",
        entity_id=account.id,
        action="interest_settings_updated",
        summary=f"Interest calculation settings updated for {account.person.name} · {account.name}",
        before=before,
        after=after,
        reason=body.reason,
        actor="local",
    )
    db.commit()
    return {"account": _account_dict(db, account), "calculation": calculate_account(db, account.id)}


@app.get("/api/accounts/{account_id}/ledger")
def account_ledger_api(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        "account": _account_dict(db, account),
        "transactions": account_ledger(db, account_id),
        "calculation": calculate_account(db, account_id),
        "integrity": verify_account_chain(db, account_id),
    }


@app.get("/api/accounts/{account_id}/integrity")
def account_integrity(account_id: int, db: Session = Depends(get_db)):
    if not db.get(Account, account_id):
        raise HTTPException(404, "Account not found")
    return verify_account_chain(db, account_id)


@app.post("/api/accounts/{account_id}/transactions")
def create_transaction(account_id: int, body: TransactionCreate, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    try:
        item = append_transaction(
            db,
            account_id=account_id,
            effective_date=body.effective_date,
            transaction_type=body.transaction_type,
            amount=body.amount,
            direction=body.direction,
            note=body.note,
            reference=body.reference,
            source=body.source,
            created_by="local",
            audit_reason=body.reason,
        )
        db.commit()
        return {
            "transaction": transaction_snapshot(item),
            "nominal_ledger_balance": account_balance(db, account_id),
            "calculation": calculate_account(db, account_id),
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/transactions/{transaction_id}/reverse")
def reverse_transaction_api(transaction_id: int, body: TransactionReverse, db: Session = Depends(get_db)):
    original = db.get(LedgerTransaction, transaction_id)
    if not original:
        raise HTTPException(404, "Transaction not found")
    try:
        reversal = reverse_transaction(
            db,
            original,
            reason=body.reason,
            effective_date=body.effective_date,
            actor="local",
        )
        db.commit()
        return {
            "original": transaction_snapshot(original),
            "reversal": transaction_snapshot(reversal),
            "nominal_ledger_balance": account_balance(db, original.account_id),
            "calculation": calculate_account(db, original.account_id),
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/transactions/{transaction_id}/correct")
def correct_transaction_api(transaction_id: int, body: TransactionCorrection, db: Session = Depends(get_db)):
    original = db.get(LedgerTransaction, transaction_id)
    if not original:
        raise HTTPException(404, "Transaction not found")
    try:
        reversal, replacement = correct_transaction(
            db,
            original,
            effective_date=body.effective_date,
            transaction_type=body.transaction_type,
            amount=body.amount,
            direction=body.direction,
            note=body.note,
            reason=body.reason,
            actor="local",
        )
        db.commit()
        return {
            "original": transaction_snapshot(original),
            "reversal": transaction_snapshot(reversal),
            "replacement": transaction_snapshot(replacement),
            "nominal_ledger_balance": account_balance(db, original.account_id),
            "calculation": calculate_account(db, original.account_id),
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/ledger")
def ledger(limit: int = Query(default=500, ge=1, le=5000), db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    rows: list[dict] = []
    for account in accounts:
        for item in account_ledger(db, account.id):
            rows.append({**item, "account": account.name, "person": account.person.name})
    rows.sort(key=lambda item: (item["effective_date"], item["id"]), reverse=True)
    return {"transactions": rows[:limit], "count": len(rows), "immutable": True}


@app.get("/api/audit")
def audit(
    limit: int = Query(default=250, ge=1, le=5000),
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = select(AuditEvent)
    if account_id is not None:
        account = db.get(Account, account_id)
        if not account:
            raise HTTPException(404, "Account not found")
        transaction_ids = list(db.scalars(select(LedgerTransaction.id).where(LedgerTransaction.account_id == account_id)).all())
        rate_ids = list(db.scalars(select(InterestRatePeriod.id).where(InterestRatePeriod.account_id == account_id)).all())
        filters = [(AuditEvent.entity_type == "account") & (AuditEvent.entity_id == account_id)]
        if transaction_ids:
            filters.append((AuditEvent.entity_type == "ledger_transaction") & (AuditEvent.entity_id.in_(transaction_ids)))
        if rate_ids:
            filters.append((AuditEvent.entity_type == "interest_rate_period") & (AuditEvent.entity_id.in_(rate_ids)))
        query = query.where(or_(*filters))
    rows = db.scalars(query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)).all()
    return {"events": [_audit_dict(item) for item in rows], "immutable": True}

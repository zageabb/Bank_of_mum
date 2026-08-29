from __future__ import annotations

import json
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .import_legacy import import_legacy_json
from .ledger import (
    account_balance,
    account_ledger,
    append_transaction,
    correct_transaction,
    reverse_transaction,
    transaction_snapshot,
    verify_account_chain,
)
from .migrations import install_immutability_guards, prepare_phase2_data, run_phase2_schema_migrations
from .models import Account, AuditEvent, LedgerTransaction, Person
from .schemas import TransactionCorrection, TransactionCreate, TransactionReverse

Base.metadata.create_all(engine)
run_phase2_schema_migrations(engine)
with SessionLocal() as _phase2_db:
    prepare_phase2_data(_phase2_db)
install_immutability_guards(engine)

app = FastAPI(title=settings.app_name, version="2.0.0-phase2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _account_dict(db: Session, account: Account) -> dict:
    return {
        "id": account.id,
        "person": account.person.name,
        "name": account.name,
        "opening_principal": account.opening_principal,
        "annual_interest_rate": account.annual_interest_rate,
        "regular_payment": account.regular_payment,
        "start_date": account.start_date,
        "status": account.status,
        "current_balance": account_balance(db, account.id),
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
    return {"ok": True, "app": settings.app_name, "version": "2.0.0-phase2", "ledger_immutable": True}


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
    balances = [account_balance(db, item.id) for item in accounts]
    audit_count = db.scalar(select(func.count(AuditEvent.id))) or 0
    return {
        "summary": {
            "people": len(people),
            "accounts": len(accounts),
            "opening_principal": round(sum(item.opening_principal for item in accounts), 2),
            "recorded_payments": round(float(payment_total), 2),
            "ledger_balance": round(sum(balances), 2),
            "audit_events": int(audit_count),
        },
        "people": [{"id": p.id, "name": p.name, "accounts": len(p.accounts)} for p in people],
        "accounts": [_account_dict(db, item) for item in accounts],
        "settings": {"ollama_url": settings.ollama_url, "ollama_model": settings.ollama_model},
        "phase": 2,
        "balance_note": "Ledger balances are now transaction-derived and audited. Date-sensitive interest accrual is added in Phase 3.",
    }


@app.post("/api/admin/import-legacy")
def import_legacy(db: Session = Depends(get_db)):
    return import_legacy_json(db)


@app.get("/api/accounts/{account_id}")
def account_detail(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        **_account_dict(db, account),
        "transactions": account_ledger(db, account.id),
        "integrity": verify_account_chain(db, account.id),
    }


@app.get("/api/accounts/{account_id}/ledger")
def account_ledger_api(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    return {
        "account": _account_dict(db, account),
        "transactions": account_ledger(db, account_id),
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
        return {"transaction": transaction_snapshot(item), "current_balance": account_balance(db, account_id)}
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
            "current_balance": account_balance(db, original.account_id),
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
            "current_balance": account_balance(db, original.account_id),
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
        transaction_ids = db.scalars(
            select(LedgerTransaction.id).where(LedgerTransaction.account_id == account_id)
        ).all()
        if transaction_ids:
            query = query.where(
                (AuditEvent.entity_type == "account") & (AuditEvent.entity_id == account_id)
                | (AuditEvent.entity_type == "ledger_transaction") & (AuditEvent.entity_id.in_(transaction_ids))
            )
        else:
            query = query.where(AuditEvent.entity_type == "account", AuditEvent.entity_id == account_id)
    rows = db.scalars(query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)).all()
    return {"events": [_audit_dict(item) for item in rows], "immutable": True}

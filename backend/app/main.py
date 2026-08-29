from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_db
from .import_legacy import import_legacy_json
from .models import Account, LedgerTransaction, Person

Base.metadata.create_all(engine)
app = FastAPI(title=settings.app_name, version="2.0.0-phase1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.app_name, "version": "2.0.0-phase1"}


@app.get("/api/bootstrap")
def bootstrap(db: Session = Depends(get_db)):
    people = db.scalars(select(Person).order_by(Person.name)).all()
    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    payments = db.scalar(select(func.coalesce(func.sum(LedgerTransaction.amount), 0.0)).where(LedgerTransaction.transaction_type == "payment")) or 0.0
    opening = sum(item.opening_principal for item in accounts)
    return {
        "summary": {
            "people": len(people),
            "accounts": len(accounts),
            "opening_principal": round(opening, 2),
            "recorded_payments": round(float(payments), 2),
            "provisional_balance": round(opening - float(payments), 2),
        },
        "people": [{"id": p.id, "name": p.name, "accounts": len(p.accounts)} for p in people],
        "accounts": [
            {
                "id": a.id,
                "person": a.person.name,
                "name": a.name,
                "opening_principal": a.opening_principal,
                "annual_interest_rate": a.annual_interest_rate,
                "regular_payment": a.regular_payment,
                "start_date": a.start_date,
                "status": a.status,
            }
            for a in accounts
        ],
        "settings": {"ollama_url": settings.ollama_url, "ollama_model": settings.ollama_model},
        "phase": 1,
        "balance_note": "Phase 1 provisional balance excludes date-sensitive interest. The Phase 3 accounting engine will become authoritative.",
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
        "id": account.id,
        "person": account.person.name,
        "name": account.name,
        "opening_principal": account.opening_principal,
        "annual_interest_rate": account.annual_interest_rate,
        "regular_payment": account.regular_payment,
        "start_date": account.start_date,
        "transactions": [
            {"id": t.id, "date": t.effective_date, "type": t.transaction_type, "amount": t.amount, "note": t.note, "source": t.source}
            for t in account.transactions
        ],
    }

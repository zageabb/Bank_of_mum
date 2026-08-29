from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .ai import ai_settings_dict, chat_with_tools, list_ollama_models, update_ai_settings
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
from .models import (
    Account,
    AuditEvent,
    InterestRatePeriod,
    LedgerTransaction,
    PaymentPlan,
    PaymentPlanAccount,
    Person,
    Scenario,
)
from .planning import forecast_payment_plan, plan_dict, validate_members
from .phase7 import router as phase7_router
from .scenarios import compare_many, compare_scenario, forecast_scenario, replace_changes, scenario_dict, validate_changes
from .schemas import (
    AccountInterestSettingsUpdate,
    AIChatRequest,
    AISettingsUpdate,
    InterestRateCreate,
    PaymentPlanCreate,
    PaymentPlanUpdate,
    ScenarioCreate,
    ScenarioUpdate,
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

app = FastAPI(title=settings.app_name, version="2.0.0-phase7")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(phase7_router)


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


def _plan_member_payload(body) -> list[dict]:
    return [item.model_dump() for item in body.members]


def _scenario_change_payload(body) -> list[dict]:
    return [item.model_dump() for item in body.changes]


def _replace_plan_members(plan: PaymentPlan, validated: list[tuple[Account, int, Decimal, bool]]) -> None:
    plan.members.clear()
    for account, priority, base_payment, enabled in validated:
        plan.members.append(PaymentPlanAccount(
            account=account,
            priority=priority,
            base_payment=base_payment,
            enabled=enabled,
        ))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "app": settings.app_name,
        "version": "2.0.0-phase7",
        "ledger_immutable": True,
        "interest_engine": "daily_simple",
        "payment_planning": "priority_rollover",
        "scenario_engine": "deterministic_what_if",
        "ai": "ollama_bounded_tools",
        "ai_accounting_mode": "read_only",
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
    plan_count = db.scalar(select(func.count(PaymentPlan.id))) or 0
    scenario_count = db.scalar(select(func.count(Scenario.id))) or 0
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
            "payment_plans": int(plan_count),
            "scenarios": int(scenario_count),
        },
        "people": [{"id": p.id, "name": p.name, "accounts": len(p.accounts)} for p in people],
        "accounts": [_account_dict(db, item) for item in accounts],
        "settings": ai_settings_dict(db),
        "phase": 7,
        "balance_note": "The immutable ledger remains authoritative. Phase 7 adds audited management, statements, annual interest reporting, exports, backup/restore and integrity verification.",
    }


@app.get("/api/ai/capabilities")
def ai_capabilities():
    return {
        "provider": "ollama",
        "accounting_mode": "read_only",
        "ledger_mutations": False,
        "contractual_rate_mutations": False,
        "scenario_proposals": "draft_only",
        "tools": [
            "get_portfolio_summary",
            "list_accounts",
            "get_account_balance",
            "get_account_ledger",
            "list_payment_plans",
            "forecast_payment_plan",
            "list_scenarios",
            "compare_scenario",
            "compare_scenarios",
            "get_audit_history",
            "propose_scenario",
        ],
    }


@app.get("/api/ai/settings")
def get_ai_settings(db: Session = Depends(get_db)):
    return ai_settings_dict(db)


@app.put("/api/ai/settings")
def set_ai_settings(body: AISettingsUpdate, db: Session = Depends(get_db)):
    try:
        result = update_ai_settings(
            db,
            ollama_url=body.ollama_url,
            ollama_model=body.ollama_model,
            max_tool_calls=body.max_tool_calls,
            timeout_seconds=body.timeout_seconds,
            reason=body.reason,
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/ai/models")
async def get_ai_models(base_url: str | None = None, db: Session = Depends(get_db)):
    configured = ai_settings_dict(db)
    target = (base_url or configured["ollama_url"]).strip()
    if not target:
        raise HTTPException(422, "Ollama URL is required")
    try:
        models = await list_ollama_models(target)
        return {"base_url": target.rstrip("/"), "models": models}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Could not query Ollama models: {exc}") from exc


@app.post("/api/ai/chat")
async def ai_chat(body: AIChatRequest, db: Session = Depends(get_db)):
    try:
        return await chat_with_tools(
            db,
            [item.model_dump() for item in body.messages],
            model_override=body.model,
            allow_scenario_proposals=body.allow_scenario_proposals,
        )
    except httpx.HTTPError as exc:
        db.rollback()
        raise HTTPException(502, f"Ollama request failed: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


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


@app.get("/api/payment-plans")
def payment_plans(db: Session = Depends(get_db)):
    plans = db.scalars(select(PaymentPlan).order_by(PaymentPlan.status, PaymentPlan.name, PaymentPlan.id)).all()
    return {"plans": [plan_dict(item) for item in plans]}


@app.post("/api/payment-plans")
def create_payment_plan(body: PaymentPlanCreate, db: Session = Depends(get_db)):
    if db.scalar(select(PaymentPlan).where(PaymentPlan.name == body.name).limit(1)):
        raise HTTPException(409, "A payment plan with this name already exists")
    try:
        validated, budget = validate_members(db, _plan_member_payload(body), body.monthly_budget)
        plan = PaymentPlan(
            name=body.name.strip(),
            first_payment_date=body.first_payment_date,
            frequency="monthly",
            monthly_budget=budget,
            strategy=body.strategy,
            status=body.status,
            notes=body.notes,
            created_by="local",
        )
        db.add(plan)
        _replace_plan_members(plan, validated)
        db.flush()
        snapshot = plan_dict(plan)
        log_audit(
            db,
            entity_type="payment_plan",
            entity_id=plan.id,
            action="plan_created",
            summary=f"Payment plan {plan.name} created with monthly budget £{budget:.2f}",
            after=snapshot,
            reason="Payment plan created",
            actor="local",
        )
        db.commit()
        return {"plan": plan_dict(plan)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/payment-plans/{plan_id}")
def payment_plan_detail(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(PaymentPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Payment plan not found")
    return {"plan": plan_dict(plan)}


@app.put("/api/payment-plans/{plan_id}")
def update_payment_plan(plan_id: int, body: PaymentPlanUpdate, db: Session = Depends(get_db)):
    plan = db.get(PaymentPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Payment plan not found")
    duplicate = db.scalar(select(PaymentPlan).where(PaymentPlan.name == body.name, PaymentPlan.id != plan_id).limit(1))
    if duplicate:
        raise HTTPException(409, "A payment plan with this name already exists")
    before = plan_dict(plan)
    try:
        validated, budget = validate_members(db, _plan_member_payload(body), body.monthly_budget)
        plan.name = body.name.strip()
        plan.first_payment_date = body.first_payment_date
        plan.frequency = "monthly"
        plan.monthly_budget = budget
        plan.strategy = body.strategy
        plan.status = body.status
        plan.notes = body.notes
        _replace_plan_members(plan, validated)
        db.flush()
        after = plan_dict(plan)
        log_audit(
            db,
            entity_type="payment_plan",
            entity_id=plan.id,
            action="plan_updated",
            summary=f"Payment plan {plan.name} updated",
            before=before,
            after=after,
            reason=body.reason,
            actor="local",
        )
        db.commit()
        return {"plan": plan_dict(plan)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/payment-plans/{plan_id}/forecast")
def payment_plan_forecast(
    plan_id: int,
    horizon_months: int = Query(default=240, ge=1, le=600),
    db: Session = Depends(get_db),
):
    plan = db.get(PaymentPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Payment plan not found")
    try:
        return forecast_payment_plan(db, plan, horizon_months)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios")
def scenarios(plan_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Scenario)
    if plan_id is not None:
        if not db.get(PaymentPlan, plan_id):
            raise HTTPException(404, "Payment plan not found")
        query = query.where(Scenario.plan_id == plan_id)
    rows = db.scalars(query.order_by(Scenario.status, Scenario.name, Scenario.id)).all()
    return {"scenarios": [scenario_dict(item) for item in rows]}


@app.post("/api/scenarios")
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db)):
    plan = db.get(PaymentPlan, body.plan_id)
    if not plan:
        raise HTTPException(404, "Payment plan not found")
    duplicate = db.scalar(select(Scenario).where(Scenario.plan_id == plan.id, Scenario.name == body.name).limit(1))
    if duplicate:
        raise HTTPException(409, "A scenario with this name already exists for the payment plan")
    try:
        validated = validate_changes(db, plan, _scenario_change_payload(body))
        scenario = Scenario(
            plan=plan,
            name=body.name.strip(),
            description=body.description,
            status=body.status,
            created_by="local",
        )
        db.add(scenario)
        replace_changes(scenario, validated)
        db.flush()
        snapshot = scenario_dict(scenario)
        log_audit(
            db,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario_created",
            summary=f"Scenario {scenario.name} created for {plan.name}",
            after=snapshot,
            reason="What-if scenario created",
            actor="local",
        )
        db.commit()
        return {"scenario": scenario_dict(scenario)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/compare")
def compare_scenarios_api(
    scenario_ids: list[int] = Query(default=[]),
    horizon_months: int = Query(default=240, ge=1, le=600),
    db: Session = Depends(get_db),
):
    try:
        return compare_many(db, scenario_ids, horizon_months)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/{scenario_id}")
def scenario_detail(scenario_id: int, db: Session = Depends(get_db)):
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    return {"scenario": scenario_dict(scenario)}


@app.put("/api/scenarios/{scenario_id}")
def update_scenario(scenario_id: int, body: ScenarioUpdate, db: Session = Depends(get_db)):
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    plan = db.get(PaymentPlan, body.plan_id)
    if not plan:
        raise HTTPException(404, "Payment plan not found")
    duplicate = db.scalar(
        select(Scenario).where(
            Scenario.plan_id == plan.id,
            Scenario.name == body.name,
            Scenario.id != scenario_id,
        ).limit(1)
    )
    if duplicate:
        raise HTTPException(409, "A scenario with this name already exists for the payment plan")
    before = scenario_dict(scenario)
    try:
        validated = validate_changes(db, plan, _scenario_change_payload(body))
        scenario.plan = plan
        scenario.name = body.name.strip()
        scenario.description = body.description
        scenario.status = body.status
        replace_changes(scenario, validated)
        db.flush()
        after = scenario_dict(scenario)
        log_audit(
            db,
            entity_type="scenario",
            entity_id=scenario.id,
            action="scenario_updated",
            summary=f"Scenario {scenario.name} updated",
            before=before,
            after=after,
            reason=body.reason,
            actor="local",
        )
        db.commit()
        return {"scenario": scenario_dict(scenario)}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/{scenario_id}/forecast")
def scenario_forecast_api(
    scenario_id: int,
    horizon_months: int = Query(default=240, ge=1, le=600),
    db: Session = Depends(get_db),
):
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    try:
        return forecast_scenario(db, scenario, horizon_months)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/scenarios/{scenario_id}/comparison")
def scenario_comparison_api(
    scenario_id: int,
    horizon_months: int = Query(default=240, ge=1, le=600),
    db: Session = Depends(get_db),
):
    scenario = db.get(Scenario, scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    try:
        return compare_scenario(db, scenario, horizon_months)
    except ValueError as exc:
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

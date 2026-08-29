from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session


LEDGER_COLUMNS = {
    "direction": "TEXT NOT NULL DEFAULT 'debit'",
    "reference": "TEXT NOT NULL DEFAULT ''",
    "created_by": "TEXT NOT NULL DEFAULT 'local'",
    "reverses_transaction_id": "INTEGER NULL REFERENCES ledger_transactions(id)",
    "correction_group": "TEXT NOT NULL DEFAULT ''",
    "previous_hash": "TEXT NOT NULL DEFAULT ''",
    "entry_hash": "TEXT NOT NULL DEFAULT ''",
}

ACCOUNT_PHASE3_COLUMNS = {
    "interest_method": "TEXT NOT NULL DEFAULT 'daily_simple'",
    "day_count_convention": "TEXT NOT NULL DEFAULT 'actual_365'",
    "payment_allocation": "TEXT NOT NULL DEFAULT 'fees_interest_principal'",
}


def _columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def _tables(connection) -> set[str]:
    return {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _drop_immutability_guards(connection) -> None:
    for trigger in (
        "ledger_transactions_no_update",
        "ledger_transactions_no_delete",
        "audit_events_no_update",
        "audit_events_no_delete",
        "interest_rate_periods_no_update",
        "interest_rate_periods_no_delete",
    ):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {trigger}")


def run_phase2_schema_migrations(engine: Engine) -> None:
    """Upgrade a Phase 1 SQLite database without discarding imported data."""
    with engine.begin() as connection:
        _drop_immutability_guards(connection)
        if "ledger_transactions" in _tables(connection):
            existing = _columns(connection, "ledger_transactions")
            for name, definition in LEDGER_COLUMNS.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE ledger_transactions ADD COLUMN {name} {definition}")
            connection.exec_driver_sql(
                "UPDATE ledger_transactions SET direction = CASE WHEN transaction_type = 'payment' THEN 'credit' ELSE 'debit' END "
                "WHERE direction IS NULL OR direction = '' OR (transaction_type = 'payment' AND direction = 'debit')"
            )


def run_phase3_schema_migrations(engine: Engine) -> None:
    """Add date-sensitive interest configuration to an existing v2 database."""
    with engine.begin() as connection:
        _drop_immutability_guards(connection)
        if "accounts" in _tables(connection):
            existing = _columns(connection, "accounts")
            for name, definition in ACCOUNT_PHASE3_COLUMNS.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")


def prepare_phase2_data(db: Session) -> dict:
    """Backfill hashes and add opening ledger entries for existing Phase 1 accounts."""
    from .ledger import append_transaction, backfill_account_chain
    from .models import Account, LedgerTransaction

    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    hash_updates = 0
    opening_entries = 0
    for account in accounts:
        hash_updates += backfill_account_chain(db, account.id)
        opening = db.scalar(
            select(LedgerTransaction)
            .where(
                LedgerTransaction.account_id == account.id,
                LedgerTransaction.transaction_type == "opening_balance",
            )
            .limit(1)
        )
        if not opening and float(account.opening_principal or 0) > 0:
            append_transaction(
                db,
                account_id=account.id,
                effective_date=account.start_date or date.today(),
                transaction_type="opening_balance",
                direction="debit",
                amount=account.opening_principal,
                note="Opening principal migrated from Phase 1 account metadata",
                reference=f"PHASE1-OPEN-{account.id}",
                source="phase2_migration",
                created_by="system",
                audit_action="opening_balance_migrated",
            )
            opening_entries += 1
    db.commit()
    return {"accounts": len(accounts), "hash_updates": hash_updates, "opening_entries": opening_entries}


def prepare_phase3_data(db: Session) -> dict:
    """Seed one contractual rate period per existing account without changing ledger history."""
    from .ledger import log_audit
    from .models import Account, InterestRatePeriod, LedgerTransaction

    accounts = db.scalars(select(Account).order_by(Account.id)).all()
    created = 0
    for account in accounts:
        existing = db.scalar(
            select(InterestRatePeriod)
            .where(InterestRatePeriod.account_id == account.id)
            .order_by(InterestRatePeriod.effective_from, InterestRatePeriod.id)
            .limit(1)
        )
        if existing:
            continue
        first_transaction_date = db.scalar(
            select(LedgerTransaction.effective_date)
            .where(LedgerTransaction.account_id == account.id)
            .order_by(LedgerTransaction.effective_date, LedgerTransaction.id)
            .limit(1)
        )
        effective_from = account.start_date or first_transaction_date or date.today()
        item = InterestRatePeriod(
            account_id=account.id,
            effective_from=effective_from,
            annual_rate=Decimal(str(account.annual_interest_rate or 0)),
            day_count_convention=account.day_count_convention or "actual_365",
            reason="Initial rate migrated from account metadata",
            created_by="system",
        )
        db.add(item)
        db.flush()
        log_audit(
            db,
            entity_type="interest_rate_period",
            entity_id=item.id,
            action="rate_migrated",
            summary=f"Initial interest rate {item.annual_rate}% effective {effective_from.isoformat()}",
            after={
                "account_id": account.id,
                "effective_from": effective_from.isoformat(),
                "annual_rate": str(item.annual_rate),
                "day_count_convention": item.day_count_convention,
            },
            actor="system",
        )
        created += 1
    db.commit()
    return {"accounts": len(accounts), "rate_periods_created": created}


def install_immutability_guards(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS ledger_transactions_no_update "
            "BEFORE UPDATE ON ledger_transactions BEGIN "
            "SELECT RAISE(ABORT, 'Ledger transactions are immutable; post a reversal/correction instead'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS ledger_transactions_no_delete "
            "BEFORE DELETE ON ledger_transactions BEGIN "
            "SELECT RAISE(ABORT, 'Ledger transactions are immutable; post a reversal instead'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS audit_events_no_update "
            "BEFORE UPDATE ON audit_events BEGIN "
            "SELECT RAISE(ABORT, 'Audit events are immutable'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS audit_events_no_delete "
            "BEFORE DELETE ON audit_events BEGIN "
            "SELECT RAISE(ABORT, 'Audit events are immutable'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS interest_rate_periods_no_update "
            "BEFORE UPDATE ON interest_rate_periods BEGIN "
            "SELECT RAISE(ABORT, 'Interest rate periods are immutable; add a superseding rate period'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS interest_rate_periods_no_delete "
            "BEFORE DELETE ON interest_rate_periods BEGIN "
            "SELECT RAISE(ABORT, 'Interest rate periods are immutable'); END"
        )

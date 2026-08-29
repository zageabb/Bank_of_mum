from __future__ import annotations

from sqlalchemy import Engine


LEDGER_COLUMNS = {
    "direction": "TEXT NOT NULL DEFAULT 'debit'",
    "reference": "TEXT NOT NULL DEFAULT ''",
    "created_by": "TEXT NOT NULL DEFAULT 'local'",
    "reverses_transaction_id": "INTEGER NULL REFERENCES ledger_transactions(id)",
    "correction_group": "TEXT NOT NULL DEFAULT ''",
    "previous_hash": "TEXT NOT NULL DEFAULT ''",
    "entry_hash": "TEXT NOT NULL DEFAULT ''",
}


def _columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}


def run_phase2_schema_migrations(engine: Engine) -> None:
    """Upgrade a Phase 1 SQLite database without discarding imported data."""
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS ledger_transactions_no_update")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS ledger_transactions_no_delete")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS audit_events_no_update")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS audit_events_no_delete")

        if "ledger_transactions" in {
            row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }:
            existing = _columns(connection, "ledger_transactions")
            for name, definition in LEDGER_COLUMNS.items():
                if name not in existing:
                    connection.exec_driver_sql(f"ALTER TABLE ledger_transactions ADD COLUMN {name} {definition}")
            connection.exec_driver_sql(
                "UPDATE ledger_transactions SET direction = CASE WHEN transaction_type = 'payment' THEN 'credit' ELSE 'debit' END "
                "WHERE direction IS NULL OR direction = '' OR (transaction_type = 'payment' AND direction = 'debit')"
            )


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

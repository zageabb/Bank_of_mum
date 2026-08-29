# Phase 2 — Immutable ledger and audit

Phase 2 converts the v2 foundation from simple imported payment rows into an auditable ledger model.

## Goals

- Ledger transactions are append-only accounting records.
- Corrections create a reversal and replacement rather than overwriting history.
- Reversals require a reason and preserve the original record.
- Every create, correction and reversal generates an audit event.
- Account balances are calculated from active ledger transactions.
- Phase 3 will add date-sensitive interest accrual on top of this ledger.

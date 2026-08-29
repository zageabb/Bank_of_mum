# Phase 2 — Immutable ledger and audit

Phase 2 converts the v2 foundation from simple imported payment rows into an auditable accounting ledger.

## Implemented

- Ledger transactions are append-only accounting records.
- SQLite triggers reject `UPDATE` and `DELETE` operations against ledger transactions.
- Audit events are also append-only and protected from update/delete.
- Opening principal is represented as an explicit debit ledger transaction.
- Payments are credit ledger transactions.
- Corrections create two new entries: an opposite reversal and a replacement transaction.
- Reversals require a reason and preserve the original transaction permanently.
- Every create, reversal and correction generates audit events.
- Every account ledger is SHA-256 hash chained in append order.
- Account balances are calculated by replaying debit/credit ledger entries.
- Existing Phase 1 SQLite databases are upgraded in place and their ledger hashes are backfilled.
- The legacy JSON importer now writes through the same immutable ledger service.
- Payments, Ledger and Audit screens are active in the Next.js UI.
- Backend regression tests cover balance restoration, correction behavior, audit generation and database immutability.
- GitHub Actions validates backend tests and the Next.js production build.

## Transaction model

Each ledger transaction records:

- effective date
- transaction type
- debit or credit direction
- monetary amount
- note and reference
- source and actor
- reversal relationship when applicable
- correction group
- previous ledger hash
- entry hash
- creation timestamp

The ledger does not expose edit or delete APIs.

## Correction example

A payment of £200 that should have been £250 is not overwritten.

1. Original payment remains: £200 credit.
2. Reversal is posted: £200 debit linked to the original.
3. Replacement is posted: £250 credit.
4. Audit records capture the reason and all related transaction IDs.

The net accounting result is a £250 payment while the historical record remains complete.

## Phase 3 boundary

Phase 2 makes the ledger authoritative for principal movements, payments, fees and manually posted interest entries. Phase 3 will add the date-sensitive interest engine: daily accrual, rate periods, payment allocation and deterministic recalculation from dated events.

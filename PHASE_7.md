# Phase 7 — Reporting, Management & Operational Polish

Phase 7 turns Bank of Mum v2 into a rounded day-to-day application around the accounting, interest, planning, scenario and AI engines delivered in Phases 1–6.

## Principles retained

- The immutable dated ledger remains the accounting source of truth.
- Interest is still calculated only by the deterministic Phase 3 engine.
- Payment plans and scenarios remain non-destructive forecasts.
- AI remains unable to mutate ledger history or contractual rates.
- Management actions are audited.
- Accounts are archived rather than deleted so financial history is retained.

## People and account management

New API:

- `GET /api/people`
- `POST /api/people`
- `PUT /api/people/{person_id}`
- `GET /api/managed-accounts`
- `POST /api/managed-accounts`
- `PUT /api/managed-accounts/{account_id}`

New UI: `/manage`

Creating an account performs one atomic setup flow:

1. create the account metadata;
2. post the opening principal as an immutable `opening_balance` debit;
3. create the initial append-only interest-rate period;
4. write audit events for the rate and account creation.

Editable account metadata is intentionally limited to name, account type, regular payment and lifecycle status. Opening principal is not directly editable because it belongs to the ledger. Interest-rate changes continue to use append-only rate periods.

Supported account lifecycle statuses:

- active
- paused
- settled
- archived

There is no account DELETE endpoint.

## Account statements

New API:

- `GET /api/reports/accounts/{account_id}/statement`
- `GET /api/reports/accounts/{account_id}/statement.csv`

The statement includes:

- opening balance and components;
- closing balance and components;
- debits and credits in the period;
- interest accrued during the period;
- interest paid during the period;
- each immutable transaction;
- the interest accrued before each transaction;
- payment allocation to fees, interest and principal;
- balance after every transaction.

All figures are derived from `calculate_account`; the reporting layer does not contain separate amortisation maths.

## Annual interest summary

New API:

- `GET /api/reports/annual-interest/{year}`
- `GET /api/reports/annual-interest/{year}.csv`

The report provides per-account and portfolio totals for:

- opening balance;
- interest accrued;
- interest paid;
- closing balance;
- year-end principal.

New UI: `/reports`

## Backups and restore

New API:

- `GET /api/maintenance/backups`
- `POST /api/maintenance/backups`
- `GET /api/maintenance/backups/{filename}`
- `POST /api/maintenance/backups/{filename}/restore`

Backups use SQLite's native backup API rather than copying a potentially live database file.

Each backup is a ZIP containing:

- `bank-of-mum.db`
- `manifest.json`

The manifest records:

- creation time;
- application phase;
- SHA-256 database checksum;
- SQLite integrity-check result.

Restore safeguards:

1. only files in `data-v2/backups` can be restored;
2. the ZIP structure is validated;
3. the SHA-256 checksum must match;
4. SQLite `PRAGMA integrity_check` must pass;
5. the user must submit the exact confirmation word `RESTORE`;
6. a new `pre-restore-*.zip` backup of the current live database is created first;
7. the restored database receives a new audit event documenting the restore and safety backup.

New UI: `/maintenance`

## End-to-end integrity verification

New API:

- `GET /api/maintenance/verification`

Checks include:

- SQLite `PRAGMA integrity_check`;
- the Phase 2 SHA-256 ledger chain for every account;
- expected opening-balance ledger entries;
- interest-rate history presence;
- orphan payment-plan membership references;
- orphan scenario-account references;
- record counts for people, accounts, transactions, rate periods and audit events.

The Maintenance workspace presents a single overall PASS / REVIEW state plus any warnings.

## Frontend routing

The existing main rail now routes:

- People → `/manage`
- Accounts → `/manage`
- Reports → `/reports`
- AI → `/ai`
- Settings → `/settings`

Persistent shortcuts also expose:

- People & Accounts
- Reports
- Scenarios
- AI
- Maintenance
- Settings

## Validation

Phase 7 regression tests cover:

- statement interest accrual and payment allocation;
- annual-interest reconciliation;
- account archival without ledger deletion;
- backup SQLite integrity and SHA-256 validation;
- full database / ledger verification.

The complete existing Phase 2–6 regression suite continues to run in CI alongside the Next.js production build.

## Operational boundary

Phase 7 intentionally does not introduce:

- destructive deletion of people/accounts with financial history;
- direct editing of opening principal;
- direct editing/deletion of immutable transactions;
- direct editing/deletion of interest-rate history;
- AI write access to accounting history.

See `DEPLOYMENT.md` for run, upgrade, backup and recovery guidance.

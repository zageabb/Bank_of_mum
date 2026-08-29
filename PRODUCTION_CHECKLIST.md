# Bank of Mum — Production Checklist

Use this checklist when promoting a tested Bank of Mum revision to the long-running server.

## Before deployment

- [ ] Working tree/server checkout is clean.
- [ ] Target commit is known and recorded.
- [ ] GitHub CI is green for backend and frontend.
- [ ] Create a Bank of Mum backup from `/maintenance`.
- [ ] Download or otherwise copy at least one valid backup off the application host.
- [ ] Confirm the latest backup validates successfully.
- [ ] Record current `/api/system/diagnostics` result.
- [ ] Confirm deep accounting integrity is PASS before upgrade.

## Environment

- [ ] `BANK_OF_MUM_ENVIRONMENT=production`
- [ ] `BANK_OF_MUM_DATA_ROOT` points to persistent storage.
- [ ] `BANK_OF_MUM_CORS_ORIGINS` contains only intended frontend origins.
- [ ] Ollama URL/model are correct for this deployment.
- [ ] Free disk threshold is appropriate for the host.
- [ ] Backend and frontend services run as non-root users where practical.
- [ ] Reverse proxy/TLS configuration is active if exposed beyond a trusted LAN.

## Upgrade

- [ ] Stop or drain the frontend/backend services if required by the deployment method.
- [ ] Pull the intended revision from `main`.
- [ ] Reinstall backend requirements when changed.
- [ ] Reinstall frontend packages when changed.
- [ ] Run the Next.js production build.
- [ ] Start the backend.
- [ ] Confirm `/api/health/live` returns HTTP 200.
- [ ] Confirm `/api/health/ready` returns HTTP 200.
- [ ] Start/restart the frontend.

## Post-deployment verification

- [ ] Open `/system`.
- [ ] System state is READY.
- [ ] SQLite journal mode reports `wal`.
- [ ] Foreign keys report On.
- [ ] Required schema is complete.
- [ ] Data and backup storage are writable with sufficient free space.
- [ ] Deep accounting integrity reports PASS.
- [ ] Existing people/accounts display correctly.
- [ ] Dashboard balance agrees with expected pre-upgrade values.
- [ ] A representative account statement opens successfully.
- [ ] Payment-plan forecast opens successfully.
- [ ] Scenario comparison opens successfully.
- [ ] Audit history is present.
- [ ] Backup listing is present.
- [ ] AI is tested if Ollama is expected to be online.

## Accounting smoke test

Do not create a throwaway transaction in production merely to test the UI because ledger rows are immutable. Instead use read-only checks unless a real transaction is due.

Recommended read-only checks:

- account calculation as of today
- account ledger display
- statement generation
- annual interest report
- payment-plan forecast
- scenario comparison
- AI read-only account question
- maintenance verification

## If readiness fails

Do not post new financial transactions until the readiness reason is understood.

Typical causes:

- database cannot be opened
- required schema table missing
- data directory not writable
- backup directory not writable
- free disk below configured threshold

Ollama being offline does **not** make the accounting application unready. AI can be repaired independently.

## If deep integrity fails

- Stop financial writes.
- Record the warnings shown in `/system`.
- Do not delete or manually edit ledger/audit rows to make the warning disappear.
- Create a filesystem/server snapshot if possible.
- Validate available Bank of Mum backups.
- Compare the last known-good backup/commit.
- Restore only through the guarded maintenance restore flow when appropriate.

## Rollback

Preferred order:

1. roll the application code back to the previous known-good commit if the database itself is healthy;
2. if the database was damaged or an incompatible migration occurred, use a validated Bank of Mum backup;
3. remember that restore automatically creates a pre-restore recovery point.

After rollback, rerun `/api/health/ready` and `/api/system/diagnostics` before resuming normal use.

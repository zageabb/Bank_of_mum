# Bank of Mum v2 — Phase 1 Foundation

Phase 1 introduces the new application alongside the existing Flask implementation. The legacy app remains untouched at repository root until the replacement stack has been validated.

## Architecture

- `frontend/` — Next.js/React interface using the Context Studio visual language.
- `backend/` — FastAPI service with SQLite and SQLAlchemy.
- `data/` — existing legacy JSON records; unchanged.
- `data-v2/` — runtime SQLite data directory created by the new backend and not intended for source control.

## Phase 1 data model

- `Person` — a borrower/person. One person can own multiple accounts.
- `Account` — an individual loan/account with opening terms and a legacy identifier.
- `LedgerTransaction` — dated imported or future transactions. Phase 2 will extend this into the immutable audited ledger.
- `ApplicationSetting` — persistent configuration foundation.

Phase 1 deliberately labels the dashboard balance as **provisional**. It subtracts recorded payments from opening principal only. The authoritative date-sensitive interest and replay engine is scheduled for Phase 3.

## Existing-data migration

The endpoint `POST /api/admin/import-legacy` scans the existing `data/*.json` records, skips known sample records (`alice`, `bob`), creates people/accounts and migrates payment history into `LedgerTransaction` rows.

The importer is idempotent by `legacy_id`: already migrated accounts are skipped rather than duplicated.

## Run backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Then migrate the existing JSON once:

```bash
curl -X POST http://localhost:8000/api/admin/import-legacy
```

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on port `5075` and expects the API on `http://localhost:8000/api`. Override with `NEXT_PUBLIC_API_URL` when required.

## AI configuration foundation

Defaults currently mirror Context Studio:

- Ollama URL: `http://192.168.1.249:11434`
- Model: `qwen3:14b`

They can be overridden with:

```text
BANK_OF_MUM_OLLAMA_URL=
BANK_OF_MUM_OLLAMA_MODEL=
```

A proper editable Settings UI and Ollama model discovery will be activated with the AI/settings development work; Phase 1 carries the configuration through the new architecture and displays it read-only in the dashboard.

## Next phase

Phase 2 replaces simple imported transactions with the authoritative immutable accounting ledger, reversal/correction transactions and audit history.

# Bank of Mum v2 — Deployment & Recovery Guide

This guide covers the Phase 7 FastAPI + Next.js application. The legacy Flask application remains in the repository but is not the v2 runtime.

## Runtime layout

- Backend: FastAPI / SQLAlchemy / SQLite
- Frontend: Next.js / React
- Database: `backend/data-v2/bank-of-mum.db` when the backend is started from the `backend` directory with the default configuration
- Backups: `backend/data-v2/backups/`
- Default frontend port: `5075`
- Recommended backend port: `8000`
- Default Ollama endpoint: `http://192.168.1.249:11434`
- Default Ollama model: `qwen3:14b`

The Ollama URL and model can be changed from the Phase 6 Settings workspace and are stored in the application database.

## Backend install

Requires Python 3.12 or later.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Optional environment file:

```bash
cp .env.example .env
```

Start the backend:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```text
GET http://SERVER:8000/api/health
```

Phase 7 integrity check:

```text
GET http://SERVER:8000/api/maintenance/verification
```

## Frontend install

Requires Node.js 20 or later.

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run build
npm start
```

The frontend listens on port `5075`.

Set `NEXT_PUBLIC_API_URL` to the browser-reachable backend URL, for example:

```env
NEXT_PUBLIC_API_URL=http://192.168.1.50:8000/api
```

Do not use `localhost` in the frontend environment when users access the browser from another machine unless the backend is running on that same client machine.

## Backend environment variables

Settings use the `BANK_OF_MUM_` prefix.

Common variables:

```env
BANK_OF_MUM_DATA_ROOT=data-v2
BANK_OF_MUM_LEGACY_DATA_ROOT=../data
BANK_OF_MUM_CORS_ORIGINS=http://localhost:5075,http://192.168.1.50:5075
BANK_OF_MUM_OLLAMA_URL=http://192.168.1.249:11434
BANK_OF_MUM_OLLAMA_MODEL=qwen3:14b
```

Keep `BANK_OF_MUM_DATA_ROOT` on persistent storage.

## Recommended Linux systemd units

### Backend

`/etc/systemd/system/bank-of-mum-api.service`

```ini
[Unit]
Description=Bank of Mum FastAPI backend
After=network.target

[Service]
Type=simple
User=bankofmum
WorkingDirectory=/opt/bank-of-mum/backend
EnvironmentFile=/opt/bank-of-mum/backend/.env
ExecStart=/opt/bank-of-mum/backend/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Frontend

`/etc/systemd/system/bank-of-mum-web.service`

```ini
[Unit]
Description=Bank of Mum Next.js frontend
After=network.target bank-of-mum-api.service

[Service]
Type=simple
User=bankofmum
WorkingDirectory=/opt/bank-of-mum/frontend
EnvironmentFile=/opt/bank-of-mum/frontend/.env.local
ExecStart=/usr/bin/npm start
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bank-of-mum-api bank-of-mum-web
```

## Upgrade procedure

Before upgrading:

1. Open `/maintenance`.
2. Run integrity verification and confirm PASS, or review any warnings.
3. Create a validated backup.
4. Download a copy if you want an off-server recovery point.

Then update the code and dependencies:

```bash
git pull
cd backend
source .venv/bin/activate
pip install -r requirements.txt
cd ../frontend
npm install
npm run build
sudo systemctl restart bank-of-mum-api bank-of-mum-web
```

After restart:

1. check `/api/health`;
2. open `/maintenance`;
3. run integrity verification again;
4. open one account and confirm its current calculated balance;
5. run a statement covering recent activity.

## Database migrations

Startup currently performs:

1. SQLAlchemy `create_all` for missing tables;
2. Phase 2 ledger/audit schema migration;
3. Phase 2 ledger data preparation and hash-chain backfill;
4. Phase 3 interest schema migration;
5. Phase 3 initial rate-period preparation;
6. immutable database trigger installation.

Phase 4–7 features use existing/new tables created by SQLAlchemy and do not rewrite ledger history.

Do not manually edit `ledger_transactions` or `audit_events`. SQLite triggers intentionally reject updates/deletes to these tables.

## Legacy migration

Legacy JSON data remains under `data/` in the repository. The v2 importer is idempotent by legacy account identifier and is available through:

```text
POST /api/admin/import-legacy
```

After import, run:

```text
GET /api/maintenance/verification
```

For each imported account, verify:

- opening balance exists;
- legacy payments appear in the ledger;
- ledger hash integrity passes;
- a rate period exists;
- the calculated balance is reasonable for the imported dates/rate.

## Backup format

Phase 7 backups live in `data-v2/backups` and contain:

```text
bank-of-mum.db
manifest.json
```

The manifest includes a SHA-256 checksum and SQLite integrity result. Restore refuses archives that fail either check.

## Restore procedure

Preferred UI procedure:

1. Open `/maintenance`.
2. Locate a backup marked `valid`.
3. Press Restore.
4. Type `RESTORE` exactly.
5. Wait for the response and note the generated `pre-restore-*.zip` safety backup.
6. Run integrity verification immediately after restore.

The restore process validates the chosen archive and creates a new backup of the current live database before replacing it.

If a restored backup contains application settings from an older point in time, re-check the Ollama URL/model in `/settings`.

## Reverse proxy

For a LAN deployment, the simplest option is to expose ports 5075 and 8000 directly. For a single hostname, place nginx/Caddy/Traefik in front and route:

- `/api/*` → FastAPI port 8000
- everything else → Next.js port 5075

When using a reverse proxy, set `NEXT_PUBLIC_API_URL` to the externally visible `/api` URL and include the frontend origin in `BANK_OF_MUM_CORS_ORIGINS` if backend and frontend remain cross-origin.

## Security notes

Bank of Mum is currently designed as a trusted family/LAN application. Before exposing it to the public Internet, add authentication and HTTPS at minimum.

Particularly sensitive operations:

- transaction posting/correction/reversal;
- contractual rate changes;
- account management;
- backup download;
- database restore.

The AI tool surface remains read-only for accounting and cannot perform these accounting mutations.

# Phase 8 — Production Hardening

Phase 8 hardens the completed Bank of Mum v2 application for normal long-running deployment. It does not change the accounting, interest, payment-plan or scenario mathematics.

## Goals

- make SQLite safer under a web workload
- make process health observable
- distinguish accounting readiness from optional AI availability
- add request tracing without logging private request bodies
- prevent internal exception text being returned to browsers
- prove the actual FastAPI application can boot in CI
- provide an operator-facing system status workspace

## SQLite runtime

Every application connection now applies:

- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=5000`
- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- SQLAlchemy `pool_pre_ping=True`

WAL improves read/write coexistence for the web application. The busy timeout reduces transient `database is locked` failures. Foreign-key enforcement protects relationship integrity at the SQLite layer.

The immutable ledger triggers from Phase 2 remain unchanged.

## Request hardening

Every backend request receives a request ID. A valid caller-supplied `X-Request-ID` is preserved; otherwise Bank of Mum generates one.

Responses include:

- `X-Request-ID`
- `X-Response-Time-Ms`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- a restrictive browser permissions policy
- `Cache-Control: no-store` for API responses
- HSTS when the request is received over HTTPS

Unhandled backend exceptions return only:

```json
{
  "detail": "Internal server error",
  "request_id": "..."
}
```

The internal exception text is not returned to the client.

Request logs contain method, path, status, elapsed time and request ID. Request or response bodies are not logged by the Phase 8 middleware.

Requests slower than `BANK_OF_MUM_SLOW_REQUEST_MS` are logged at warning level.

## Health endpoints

### `GET /api/health/live`

Answers whether the FastAPI process is alive.

This does not inspect the accounting database deeply and is suitable for a process/container liveness probe.

### `GET /api/health/ready`

Answers whether Bank of Mum is ready to serve accounting traffic.

It checks:

- database connectivity
- SQLite runtime mode
- foreign-key enforcement
- busy timeout
- required schema tables
- data directory existence/write access
- backup directory existence/write access
- configured minimum free disk space

It returns HTTP 503 when a critical readiness check fails.

Ollama is deliberately **not** a readiness dependency. Bank of Mum must continue to provide ledger, payments, reports, forecasts and backups when the AI server is unavailable.

### `GET /api/system/diagnostics`

Runs the deeper operator check:

- normal readiness
- Phase 7 SQLite integrity verification
- every immutable ledger hash chain
- opening-balance/rate-history checks
- orphan planning/scenario reference checks
- application record counts
- backup count/latest backup
- Ollama connectivity and model count
- runtime/process information

This endpoint is used by the Phase 8 System workspace.

## System workspace

`/system` shows:

- application version/environment/uptime
- SQLite journal mode and foreign-key state
- schema status
- data/backup free space
- backup count and latest recovery point
- Ollama connectivity without treating it as an accounting outage
- deep accounting integrity PASS/REVIEW result
- key record counts
- integrity warnings

It refreshes automatically and can also be refreshed manually.

## Runtime environment settings

New optional environment variables:

```text
BANK_OF_MUM_ENVIRONMENT=development
BANK_OF_MUM_LOG_LEVEL=INFO
BANK_OF_MUM_SLOW_REQUEST_MS=1000
BANK_OF_MUM_MINIMUM_FREE_DISK_MB=100
BANK_OF_MUM_OLLAMA_HEALTH_TIMEOUT_SECONDS=3
```

Production deployments should normally set:

```text
BANK_OF_MUM_ENVIRONMENT=production
```

## CI production smoke check

The backend CI job now performs:

1. dependency installation
2. Python byte-code compilation
3. complete pytest regression suite
4. real Uvicorn startup
5. liveness request
6. readiness request
7. assertion that both endpoints report Phase 8 healthy

The frontend still requires a successful production Next.js build.

This means a green Phase 8 build proves the application entry point starts successfully, not merely that isolated functions pass tests.

## Safety boundaries retained

Phase 8 does not alter the core invariants:

- ledger entries remain append-only
- audit events remain append-only
- contractual rate periods remain append-only
- corrections remain reversal + replacement
- interest remains deterministic and date-sensitive
- forecasts/scenarios do not write future ledger entries
- AI has no ledger mutation tools
- AI scenario proposals remain draft-only
- backup restore remains confirmation-gated with a pre-restore snapshot

## Phase 8 completion criteria

- all Phase 2–7 tests remain green
- new hardening tests pass
- Next.js production build passes
- Uvicorn starts in CI
- liveness passes
- readiness passes
- `/system` builds successfully
- Phase 8 PR is cleanly based on the fully merged Phase 1–7 `main`

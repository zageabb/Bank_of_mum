# Bank of Mum — V2 source review and deployment blockers

**Status: REVIEW ONLY. No live deployment, database writes, migration, or automatic activation approved.**

Source: `DEPLOYMENT.md`, `backend/app/main.py`, `backend/app/config.py`,
`frontend/package.json`, `frontend/next.config.mjs` and lockfile.

- Active v2 architecture is **FastAPI + Next.js**, not root legacy `app.py`.
- Documented Ubuntu services: `bank-of-mum-api.service` at port 5082 and
  `bank-of-mum-web.service` at port 5080; development frontend package defaults
  to port 5075. Source candidate explicitly selects 5080.
- Backend health: `/api/health`; frontend homepage health alone is insufficient
  to prove API data health. Preserve both as required.
- Source database path: `backend/data-v2/bank-of-mum.db` with backups in
  `backend/data-v2/backups/`. Old `data/` is a separate legacy source.
- **CRITICAL:** importing `backend.app.main` runs SQLAlchemy `create_all`,
  Phase 2/3 schema migrations, ledger preparation and immutable triggers.
  The candidate's declared API entrypoint therefore mutates the database at
  process start. Do NOT activate against production until migrations are moved
  to explicit, reviewable, backup-gated lifecycle operations and shown idempotent.
- `frontend/next.config.mjs` supports `NEXT_PUBLIC_BASE_PATH` and rewrites
  `/api/:path*` to loopback 5082. The V2 candidate also declares an
  authenticated `api/` reverse-proxy route; review routing precedence,
  forwarding-prefix behaviour, CORS and exact asset URL generation before acceptance.
- The `frontend/package-lock.json` is present (lockfile v3). Review Node
  and Python versions in an isolated build.

## Required checks

- [ ] Canonical UDA V2 schema and semantic validation.
- [ ] Independent disposable build and API/UI regression tests with disposable DB.
- [ ] Read-only effective host service unit, port and Caddy/UDA route comparison.
- [ ] Effective data-root, SQLite journal, backup and restore evidence.
- [ ] Confirm user/group access and named runtime configuration (not values).
- [ ] Separate migrations, seeding/ledger data changes and trigger installation
      from process startup; obtain explicit database-write capability approval.
- [ ] Owner sign-off on route precedence, persistence and release.

Do not use the example system-wide units in the deployment guide to overwrite
live user units. No prod action is implied by a syntactically valid manifest.

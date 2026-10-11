# Development Status

## OPS-UDA-001 — Bank of Mum UDA web subpath
Status: IN PROGRESS

Web interface receives UDA/Caddy forwarded prefix through one-hop ProxyFix. Flask-generated links stay prefix-aware, direct LAN root stays functional, user loan files unchanged. Use isolated test data; never expose direct backend to untrusted forwarded headers.
Deployment note: registry also records a distinct API service (port 5082); verify its ownership/routing independently before enabling public proxy access.
- Next.js frontend now supports `NEXT_PUBLIC_BASE_PATH` and proxies its same-origin
  `/api` route to the local API service for UDA publication. Production build verified
  with `/apps/bank-of-mum` on 11 October 2026.
- [ ] CI green and merged into main
- [ ] UDA browser loan listing, forms, payments/download and API connectivity validated

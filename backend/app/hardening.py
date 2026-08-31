from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from .database import SessionLocal, engine, get_db
from .phase7 import BACKUP_DIR, maintenance_verification

APP_VERSION = "2.0.0-phase8"
APP_PHASE = 8
STARTED_AT = datetime.now(timezone.utc)
STARTED_MONOTONIC = time.monotonic()
REQUIRED_TABLES = {
    "people",
    "accounts",
    "ledger_transactions",
    "audit_events",
    "interest_rate_periods",
    "payment_plans",
    "payment_plan_accounts",
    "scenarios",
    "scenario_changes",
    "application_settings",
}

logging.basicConfig(
    level=getattr(logging, settings.normalized_log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bank_of_mum.runtime")
router = APIRouter(prefix="/api", tags=["system"])


def _json_log(level: int, event: str, **fields) -> None:
    logger.log(level, json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _safe_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and len(candidate) <= 128 and all(ch.isalnum() or ch in "-_." for ch in candidate):
        return candidate
    return uuid.uuid4().hex


class RequestHardeningMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = _safe_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            _json_log(
                logging.ERROR,
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                elapsed_ms=elapsed_ms,
                exc_info=True,
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "request_id": request_id},
            )

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")

        level = logging.WARNING if elapsed_ms >= max(1, settings.slow_request_ms) else logging.INFO
        _json_log(
            level,
            "request_completed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=status_code,
            elapsed_ms=elapsed_ms,
        )
        return response


def _directory_check(path: Path) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    free_mb = round(usage.free / 1024 / 1024, 1)
    writable = os.access(path, os.W_OK)
    return {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "writable": writable,
        "free_mb": free_mb,
        "minimum_free_mb": settings.minimum_free_disk_mb,
        "ok": path.exists() and writable and free_mb >= settings.minimum_free_disk_mb,
    }


def readiness_snapshot() -> dict:
    checks: dict[str, dict] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            journal_mode = str(connection.execute(text("PRAGMA journal_mode")).scalar_one()).lower()
            foreign_keys = int(connection.execute(text("PRAGMA foreign_keys")).scalar_one())
            busy_timeout = int(connection.execute(text("PRAGMA busy_timeout")).scalar_one())
        checks["database"] = {
            "ok": True,
            "journal_mode": journal_mode,
            "foreign_keys": foreign_keys == 1,
            "busy_timeout_ms": busy_timeout,
        }
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}

    try:
        tables = set(inspect(engine).get_table_names())
        missing = sorted(REQUIRED_TABLES - tables)
        checks["schema"] = {"ok": not missing, "missing_tables": missing, "table_count": len(tables)}
    except Exception as exc:
        checks["schema"] = {"ok": False, "error": str(exc)}

    try:
        checks["data_storage"] = _directory_check(settings.data_root)
    except Exception as exc:
        checks["data_storage"] = {"ok": False, "error": str(exc)}

    try:
        checks["backup_storage"] = _directory_check(BACKUP_DIR)
    except Exception as exc:
        checks["backup_storage"] = {"ok": False, "error": str(exc)}

    ok = all(bool(item.get("ok")) for item in checks.values())
    return {
        "ok": ok,
        "phase": APP_PHASE,
        "version": APP_VERSION,
        "environment": settings.environment,
        "checks": checks,
    }


async def ollama_diagnostics() -> dict:
    base_url = settings.ollama_url.rstrip("/")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=max(1, settings.ollama_health_timeout_seconds)) as client:
            response = await client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
        return {
            "ok": True,
            "base_url": base_url,
            "configured_model": settings.ollama_model,
            "models_available": len(payload.get("models") or []),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "required_for_accounting_readiness": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "base_url": base_url,
            "configured_model": settings.ollama_model,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "required_for_accounting_readiness": False,
        }


def runtime_snapshot() -> dict:
    return {
        "version": APP_VERSION,
        "phase": APP_PHASE,
        "environment": settings.environment,
        "started_at": STARTED_AT.isoformat(),
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 1),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "process_id": os.getpid(),
        "data_root": str(settings.data_root.resolve()),
        "log_level": settings.normalized_log_level,
        "slow_request_ms": settings.slow_request_ms,
    }


@router.get("/health/live")
def liveness():
    return {
        "ok": True,
        "status": "live",
        "version": APP_VERSION,
        "phase": APP_PHASE,
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 1),
    }


@router.get("/health/ready")
def readiness():
    snapshot = readiness_snapshot()
    if snapshot["ok"]:
        return snapshot
    return JSONResponse(status_code=503, content=snapshot)


@router.get("/system/runtime")
def runtime():
    return runtime_snapshot()


@router.get("/system/diagnostics")
async def diagnostics(db: Session = Depends(get_db)):
    readiness = readiness_snapshot()
    verification = maintenance_verification(db)
    ollama = await ollama_diagnostics()
    backups = sorted(BACKUP_DIR.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    latest_backup = None
    if backups:
        latest = backups[0]
        latest_backup = {
            "filename": latest.name,
            "size": latest.stat().st_size,
            "modified_at": datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat(),
        }
    return {
        "ok": bool(readiness["ok"] and verification.get("ok")),
        "runtime": runtime_snapshot(),
        "readiness": readiness,
        "accounting_integrity": verification,
        "ollama": ollama,
        "backup": {"directory": str(BACKUP_DIR.resolve()), "count": len(backups), "latest": latest_backup},
    }


def install_hardening(app: FastAPI) -> None:
    if getattr(app.state, "phase8_hardening_installed", False):
        return
    app.state.phase8_hardening_installed = True
    app.add_middleware(RequestHardeningMiddleware)
    app.include_router(router)

    @app.on_event("startup")
    async def phase8_startup() -> None:
        snapshot = readiness_snapshot()
        _json_log(
            logging.INFO if snapshot["ok"] else logging.ERROR,
            "application_startup",
            version=APP_VERSION,
            phase=APP_PHASE,
            environment=settings.environment,
            readiness=snapshot,
        )

    @app.on_event("shutdown")
    async def phase8_shutdown() -> None:
        _json_log(logging.INFO, "application_shutdown", version=APP_VERSION, phase=APP_PHASE)

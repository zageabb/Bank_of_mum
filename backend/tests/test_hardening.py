from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importing main runs the normal schema/startup migration path before readiness is checked.
from app import main as application_main  # noqa: F401
from app.hardening import APP_PHASE, APP_VERSION, RequestHardeningMiddleware, readiness_snapshot, runtime_snapshot


def test_phase8_readiness_checks_accounting_runtime_not_ollama():
    result = readiness_snapshot()
    assert result["ok"] is True
    assert result["phase"] == 8
    assert result["version"] == APP_VERSION
    assert "ollama" not in result["checks"]
    assert result["checks"]["database"]["ok"] is True
    assert result["checks"]["database"]["foreign_keys"] is True
    assert result["checks"]["database"]["journal_mode"] == "wal"
    assert result["checks"]["database"]["busy_timeout_ms"] >= 5000
    assert result["checks"]["schema"]["missing_tables"] == []


def test_runtime_snapshot_marks_phase8():
    result = runtime_snapshot()
    assert APP_PHASE == 8
    assert result["phase"] == 8
    assert result["version"] == "2.0.0-phase8"
    assert result["uptime_seconds"] >= 0
    assert result["python"]


def test_request_middleware_adds_trace_and_security_headers():
    app = FastAPI()
    app.add_middleware(RequestHardeningMiddleware)

    @app.get("/api/example")
    def example():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/api/example", headers={"X-Request-ID": "pytest-request-1"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "pytest-request-1"
    assert float(response.headers["X-Response-Time-Ms"]) >= 0
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


def test_request_middleware_hides_unhandled_exception_and_returns_trace_id():
    app = FastAPI()
    app.add_middleware(RequestHardeningMiddleware)

    @app.get("/api/broken")
    def broken():
        raise RuntimeError("sensitive internal failure text")

    client = TestClient(app)
    response = client.get("/api/broken")
    assert response.status_code == 500
    payload = response.json()
    assert payload["detail"] == "Internal server error"
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert "sensitive internal failure text" not in response.text

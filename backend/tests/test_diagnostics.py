import json

from app.config import BACKEND_ROOT, Settings
from app.diagnostics import application_diagnostics, database_diagnostics
from app.import_legacy import import_legacy_json
from app.models import Person
from test_ai import make_account, make_session


def test_paths_independent_of_cwd(tmp_path, monkeypatch):
    before = Settings(_env_file=None)
    monkeypatch.chdir(tmp_path)
    after = Settings(_env_file=None)
    assert before.database_url == after.database_url
    assert after.data_root == BACKEND_ROOT / "data-v2"
    assert after.legacy_data_root == BACKEND_ROOT.parent / "data"
    assert Settings(data_root=tmp_path, _env_file=None).database_path == tmp_path / "bank-of-mum.db"
    assert Settings(data_root="custom", _env_file=None).data_root == BACKEND_ROOT / "custom"


def test_diagnostics_and_import_idempotency(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "legacy_data_root", tmp_path)
    engine, db = make_session()
    (tmp_path / "alice.json").write_text("{}")
    assert database_diagnostics(db)["legacy_records_available"] is False
    (tmp_path / "family.json").write_text(json.dumps({"child": "Family", "principal": 100, "start_date": "2026-01-01", "payments": [{"amount": 10, "date": "2026-02-01"}]}))
    assert "have not been imported" in database_diagnostics(db)["warning"]
    assert import_legacy_json(db)["imported_accounts"] == 1
    before = database_diagnostics(db)
    assert before["accounts"] == 1 and before["people"] == 1 and before["ledger_transactions"] == 2
    assert before["warning"] is None
    assert import_legacy_json(db)["imported_accounts"] == 0
    assert database_diagnostics(db) == before
    engine.dispose()


def test_diagnostics_endpoint(tmp_path, monkeypatch):
    # Keep module startup migrations confined to a temporary database.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.database as database
    isolated = create_engine(f"sqlite:///{tmp_path / 'startup.db'}")
    monkeypatch.setattr(database, "engine", isolated)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=isolated))
    from fastapi.testclient import TestClient
    from app.main import app
    engine, db = make_session()
    person = Person(name="Diagnostics family")
    db.add(person)
    db.flush()
    make_account(db, person)
    db.commit()
    app.dependency_overrides[database.get_db] = lambda: db
    try:
        response = TestClient(app).get("/api/diagnostics")
        assert response.status_code == 200
        data = response.json()
        assert data["accounts"] == 1 and data["people"] == 1
        assert data["database_empty"] is False
        assert "git_commit" in data and "version" in data
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        isolated.dispose()


def test_diagnostics_reports_connected_file_and_redacts_url(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import ApplicationSetting
    path = tmp_path / "actual.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ApplicationSetting(key="ai.ollama_url", value="http://user:secret@localhost:11434?token=private#hidden"))
        db.commit()
        result = application_diagnostics(db)
        assert result["database_path"] == str(path)
        assert result["database_exists"] is True
        assert result["ollama_url"] == "http://localhost:11434"
        assert "secret" not in json.dumps(result) and "token=" not in json.dumps(result)
    engine.dispose()

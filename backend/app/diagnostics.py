"""Read-only diagnostics from the same session used by the API and AI."""
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import BACKEND_ROOT, settings
from .import_legacy import SAMPLE_IDS
from .models import Account, LedgerTransaction, Person

VERSION = "2.0.0-phase7"


def database_diagnostics(db: Session) -> dict:
    database = db.get_bind().url.database
    path = Path(database).resolve() if database and database != ":memory:" else None
    accounts = db.scalar(select(func.count(Account.id))) or 0
    legacy_files = [p for p in settings.legacy_data_root.glob("*.json") if p.stem.lower() not in SAMPLE_IDS]
    warning = None
    if accounts == 0:
        warning = "No v2 accounts found."
        if legacy_files:
            warning += " Legacy Bank of Mum records are available but have not been imported. Check the database path and backups before using the maintenance import action."
    return {
        "database_path": str(path) if path else database,
        "database_exists": path.is_file() if path else False,
        "accounts": accounts,
        "people": db.scalar(select(func.count(Person.id))) or 0,
        "ledger_transactions": db.scalar(select(func.count(LedgerTransaction.id))) or 0,
        "database_empty": accounts == 0,
        "legacy_records_available": bool(legacy_files),
        "warning": warning,
    }


def application_diagnostics(db: Session) -> dict:
    from .ai import ai_settings_dict
    configuration = ai_settings_dict(db)
    url = urlsplit(configuration["ollama_url"])
    # Strip credentials, query parameters and fragments from diagnostic output.
    safe_url = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND_ROOT,
                                capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {**database_diagnostics(db), "ollama_url": safe_url,
            "ollama_model": configuration["ollama_model"], "version": VERSION, "git_commit": commit}

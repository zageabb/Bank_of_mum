from pathlib import Path

from pydantic import field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_name: str = "Bank of Mum"
    data_root: Path = Path("data-v2")
    legacy_data_root: Path = Path("../data")
    cors_origins: str = "http://localhost:5075,http://127.0.0.1:5075"
    ollama_url: str = "http://192.168.1.249:11434"
    ollama_model: str = "qwen3:14b"
    model_config = SettingsConfigDict(env_prefix="BANK_OF_MUM_", env_file=BACKEND_ROOT / ".env", extra="ignore")

    @field_validator("data_root", "legacy_data_root", mode="after")
    @classmethod
    def resolve_data_path(cls, value: Path) -> Path:
        value = value.expanduser()
        return (value if value.is_absolute() else BACKEND_ROOT / value).resolve()

    @property
    def database_path(self) -> Path:
        return self.data_root / "bank-of-mum.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"


settings = Settings()
settings.data_root.mkdir(parents=True, exist_ok=True)

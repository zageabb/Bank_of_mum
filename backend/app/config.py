from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Bank of Mum"
    environment: str = "development"
    data_root: Path = Path("data-v2")
    legacy_data_root: Path = Path("../data")
    cors_origins: str = "http://localhost:5075,http://127.0.0.1:5075"
    ollama_url: str = "http://192.168.1.249:11434"
    ollama_model: str = "qwen3:14b"
    log_level: str = "INFO"
    slow_request_ms: int = 1000
    minimum_free_disk_mb: int = 100
    ollama_health_timeout_seconds: int = 3
    model_config = SettingsConfigDict(env_prefix="BANK_OF_MUM_", env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_root / 'bank-of-mum.db').resolve()}"

    @property
    def normalized_log_level(self) -> str:
        value = self.log_level.strip().upper()
        return value if value in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"


settings = Settings()
settings.data_root.mkdir(parents=True, exist_ok=True)

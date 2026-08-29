from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Bank of Mum"
    data_root: Path = Path("data-v2")
    legacy_data_root: Path = Path("../data")
    cors_origins: str = "http://localhost:5075,http://127.0.0.1:5075"
    ollama_url: str = "http://192.168.1.249:11434"
    ollama_model: str = "qwen3:14b"
    model_config = SettingsConfigDict(env_prefix="BANK_OF_MUM_", env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_root / 'bank-of-mum.db').resolve()}"


settings = Settings()
settings.data_root.mkdir(parents=True, exist_ok=True)

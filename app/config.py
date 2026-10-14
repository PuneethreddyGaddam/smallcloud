"""Central configuration. Every setting comes from environment variables or .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str  # required: the app refuses to start without it
    encryption_key: str = ""  # Fernet key, needed from milestone M3 (secrets)
    data_dir: Path = Path("data")
    database_url: str | None = None
    base_domain: str = "localhost"
    apps_domain: str = "localhost"
    mail_backend: str = "console"


@lru_cache
def get_settings() -> Settings:
    return Settings()

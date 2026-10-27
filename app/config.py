"""Application settings, loaded from environment variables (prefix LOSCO_)."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime configuration. Overridable via LOSCO_* env vars or a .env file."""

    database_path: Path = Path("data/reservas.db")

    # Local business timezone: drives the reminder schedule (T5).
    tz: str = "America/Argentina/Buenos_Aires"

    model_config = {"env_prefix": "LOSCO_", "env_file": ".env", "env_file_encoding": "utf-8"}

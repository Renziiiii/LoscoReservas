"""Application settings, loaded from environment variables (prefix LOSCO_)."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime configuration. Overridable via LOSCO_* env vars or a .env file."""

    database_path: Path = Path("data/reservas.db")

    # Local business timezone: drives the reminder schedule (T5).
    tz: str = "America/Argentina/Buenos_Aires"

    # --- Auth (T3) ---
    # Comma-separated "username:password" pairs. Set real values via env in
    # production; defaults exist only for local development.
    users: str = "valen:losco"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 480

    @property
    def user_map(self) -> dict[str, str]:
        pairs = {}
        for chunk in self.users.split(","):
            username, _, password = chunk.strip().partition(":")
            if username and password:
                pairs[username] = password
        return pairs

    model_config = {"env_prefix": "LOSCO_", "env_file": ".env", "env_file_encoding": "utf-8"}

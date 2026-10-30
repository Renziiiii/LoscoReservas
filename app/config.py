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

    # --- Chatwoot / mensajería (T5) ---
    chatwoot_base_url: str = ""
    chatwoot_api_token: str = ""
    chatwoot_account_id: int = 0
    template_confirmacion: str = "reserva_confirmacion"
    template_invitados: str = "invitados_confirmacion"
    # "HH:MM" — T-48h confirmations run in the morning; guest-count
    # confirmations run inside Valen's 17:00-18:00 window.
    hora_confirmacion: str = "09:00"
    hora_invitados: str = "17:15"
    scheduler_enabled: bool = False

    # --- Iframe embebible en Chatwoot (T6, S4) ---
    # Shared secret in the iframe URL (the iframe cannot do cookie login).
    # Empty key => iframe always 403 (fail closed).
    iframe_key: str = ""
    # Comma-separated origins allowed to embed the iframe via CSP
    # frame-ancestors (e.g. "https://chat.losco.com"). "*" allows any —
    # acceptable because the key protects the data, but restrict it in
    # production.
    iframe_origins: str = "*"

    @property
    def user_map(self) -> dict[str, str]:
        pairs = {}
        for chunk in self.users.split(","):
            username, _, password = chunk.strip().partition(":")
            if username and password:
                pairs[username] = password
        return pairs

    model_config = {"env_prefix": "LOSCO_", "env_file": ".env", "env_file_encoding": "utf-8"}

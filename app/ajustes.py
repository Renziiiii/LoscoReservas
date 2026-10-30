"""DB-backed runtime configuration (L19): messaging hours and WhatsApp
template names are edited in the admin panel and persisted in SQLite —
they are NOT Docker env vars. Env values only seed the first boot.

T15: the `etiqueta_confirmada` column was added after initial deploys, so
we also provide an idempotent SQLite migration for existing databases.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Configuracion


def _asegurar_columna_etiqueta(engine: Engine) -> None:
    """Idempotent migration: add `etiqueta_confirmada` to `configuracion`
    if it is missing. SQLite's `create_all` does not ALTER existing tables,
    so deployed databases created before T15 need this."""
    with engine.begin() as conn:
        columns = [
            row[1]
            for row in conn.execute(text("PRAGMA table_info(configuracion)")).fetchall()
        ]
        if "etiqueta_confirmada" not in columns:
            conn.execute(
                text(
                    "ALTER TABLE configuracion ADD COLUMN etiqueta_confirmada "
                    "VARCHAR(120) NOT NULL DEFAULT 'Reserva confirmada'"
                )
            )


def obtener_configuracion(session: Session, settings: Settings) -> Configuracion:
    """Return configuration row 1, seeding it from the env defaults on the
    first access (after that, the DB wins and the env is ignored)."""
    config = session.get(Configuracion, 1)
    if config is None:
        config = Configuracion(
            id=1,
            hora_confirmacion=settings.hora_confirmacion,
            hora_invitados=settings.hora_invitados,
            plantilla_confirmacion=settings.template_confirmacion,
            plantilla_invitados=settings.template_invitados,
            etiqueta_confirmada="Reserva confirmada",
        )
        session.add(config)
        session.commit()
    return config

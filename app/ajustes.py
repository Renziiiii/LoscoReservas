"""DB-backed runtime configuration (L19): messaging hours and WhatsApp
template names are edited in the admin panel and persisted in SQLite —
they are NOT Docker env vars. Env values only seed the first boot."""

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Configuracion


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
        )
        session.add(config)
        session.commit()
    return config

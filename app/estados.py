"""Configured reservation states (S3/S4, odd/tasks/tags-estados-sync.md).

The table `estados_reserva` is the single source of truth for which
`Reserva.estado` values exist. On first boot it is seeded with the three
states that were previously hardcoded (`pendiente`/`confirmada`/`cancelada`),
keeping the legacy `etiqueta_confirmada` mapping on `confirmada` so existing
deploys upgrade seamlessly (same migration pattern as app/ajustes.py T15).

Fail-soft by design (mirror of app/etiquetas.py L6): nothing here may break
the app boot or a reserva save over a Chatwoot problem — and Chatwoot is not
even touched from this module (labels sync lives in app/etiquetas.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ajustes import obtener_configuracion
from app.config import Settings
from app.db import get_session
from app.models import EstadoReserva

router = APIRouter(prefix="/api/estados", tags=["estados"])

# Seed rows in display order. `confirmada` inherits the current value of the
# legacy `etiqueta_confirmada` column on first boot (T15-style migration for
# existing databases; a fresh install seeds with Settings' default).
_SEEDS = (
    ("Pendiente", "pendiente", True, None),
    ("Confirmada", "confirmada", True, "__etiqueta_confirmada__"),
    ("Cancelada", "cancelada", False, None),
)


def asegurar_estados(engine, settings: Settings) -> None:
    """Idempotent first-boot seed of `estados_reserva`: create it if empty
    with the three base states. Called from create_app's lifespan next to
    create_all/_asegurar_columna_etiqueta; never raises past seeding."""
    with Session(engine) as session:
        total = session.scalar(select(func.count()).select_from(EstadoReserva))
        if total:
            return
        config = obtener_configuracion(session, settings)
        for nombre, slug, envia, etiqueta in _SEEDS:
            if etiqueta == "__etiqueta_confirmada__":
                etiqueta = config.etiqueta_confirmada
            session.add(EstadoReserva(nombre=nombre, slug=slug,
                                      etiqueta_chatwoot=etiqueta,
                                      envia_recordatorio=envia, es_base=True))
        session.commit()


def normalizar_estado(value: str | None) -> str:
    """Normalize what a client sent as `estado` to the stored slug form:
    trimmed, lowercased, defaulting to 'pendiente' (Pydantic keeps empty
    strings at bay already; this also protects the dashboard form path)."""
    return (value or "").strip().lower() or "pendiente"


def estado_valido(session: Session, value: str | None) -> str:
    """The normalized slug, or HTTPException 422 when the state is not in
    the configured table (S3: dynamic validation replaces schemas' `Estado`
    Literal — same status code and semantics the API promised before)."""
    slug = normalizar_estado(value)
    existe = session.scalar(
        select(EstadoReserva).where(EstadoReserva.slug == slug)
    )
    if existe is None:
        disponibles = [s for (s,) in session.execute(select(EstadoReserva.slug))]
        raise HTTPException(
            status_code=422,
            detail=(
                f"estado desconocido: {slug!r}. Estados configurados: "
                f"{', '.join(disponibles) or '(ninguno)'}"
            ),
        )
    return slug


def _fila(estado: EstadoReserva) -> dict:
    return {
        "id": estado.id,
        "nombre": estado.nombre,
        "slug": estado.slug,
        "etiqueta_chatwoot": estado.etiqueta_chatwoot,
        "color": estado.color,
        "envia_recordatorio": estado.envia_recordatorio,
        "es_base": estado.es_base,
    }


@router.get("", summary="Estados de reserva configurados (con su tag de Chatwoot)")
def listar_estados(session: Session = Depends(get_session)):
    return [_fila(e) for e in session.scalars(select(EstadoReserva).order_by(EstadoReserva.id))]

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

import random
import re as _re

from app.ajustes import obtener_configuracion
from app.config import Settings
from app.db import get_session
from app.models import EstadoReserva, Reserva

router = APIRouter(prefix="/api/estados", tags=["estados"])

# Seed rows in display order. `confirmada` inherits the current value of the
# legacy `etiqueta_confirmada` column on first boot (T15-style migration for
# existing databases; a fresh install seeds with Settings' default).
# Colors: base states seed with their historical palette colors so the stored
# `color` is the single source of truth (colores personalizables).
_COLORES_BASE = {
    "pendiente": "#f0a500",
    "confirmada": "#00b877",
    "cancelada": "#e54666",
}

# Same palette and order as web/src/lib/reservas.ts — hash-derived colors
# must match what clients already showed before colors were stored.
_PALETA = (
    "#6366f1", "#0ea5e9", "#a855f7", "#f97316", "#14b8a6", "#ec4899",
)

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
                                      color=_COLORES_BASE.get(slug),
                                      envia_recordatorio=envia, es_base=True))
        session.commit()


def backfill_colores(engine) -> None:
    """Idempotent color backfill for databases created before colors were
    editable: base states with NULL recover their historical color; custom
    states with NULL get the hash-derived palette color the client already
    displayed (same hash as web/src/lib/reservas.ts — nothing shifts).
    Colors already stored are never overwritten. Called from lifespan next
    to asegurar_estados."""
    with Session(engine) as session:
        estados = session.scalars(select(EstadoReserva)).all()
        cambiaron = False
        for estado in estados:
            if estado.color:
                continue
            estado.color = _COLORES_BASE.get(estado.slug) or color_por_hash(estado.slug)
            cambiaron = True
        if cambiaron:
            session.commit()


def hash_slug(slug: str) -> int:
    """Same deterministic hash as web/src/lib/reservas.ts (h*31 + char,
    wrapped to unsigned 32 bits)."""
    h = 0
    for ch in slug:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def color_por_hash(slug: str) -> str:
    """The palette color the Svelte client shows for this slug today."""
    return _PALETA[hash_slug(slug) % len(_PALETA)]


def normalizar_color(value: str | None) -> str | None:
    """'' / None -> None; '#RRGGBB' -> lowercase hex; anything else -> 422.
    Only safe hex reaches the DB and the templates."""
    texto = (value or "").strip().lower()
    if not texto:
        return None
    if not _re.fullmatch(r"#[0-9a-f]{6}", texto):
        raise HTTPException(
            status_code=422,
            detail="color inválido: usá el formato #rrggbb",
        )
    return texto


def color_desde_paleta(session: Session) -> str:
    """A palette color for a new state: random among the colors not used
    yet by other states, so new tags visibly differ from day one; when the
    palette is exhausted, any color goes."""
    usados = set(session.scalars(select(EstadoReserva.color)))
    libres = [c for c in _PALETA if c not in usados]
    return random.choice(libres) if libres else random.choice(_PALETA)


def normalizar_estado(value: str | None) -> str:
    """Normalize what a client sent as `estado` to the stored slug form:
    trimmed, lowercased, defaulting to 'pendiente' (Pydantic keeps empty
    strings at bay already; this also protects the dashboard form path)."""
    return (value or "").strip().lower() or "pendiente"


def estado_valido(session: Session, value: str | None) -> str:
    """The normalized slug, or HTTPException 422 when the state is not in
    the configured table (S3: dynamic validation replaces schemas' `Estado`
    Literal — same status code and semantics the API promised before).
    S5 (tags-panel-v2, D2): the DEFAULT slug is special — the user may have
    deleted `pendiente` (base states are all deletable now), so an implicit
    default falls back to the first configured estado instead of failing;
    an explicit unknown slug still raises 422."""
    slug = normalizar_estado(value)
    existe = session.scalar(
        select(EstadoReserva).where(EstadoReserva.slug == slug)
    )
    if existe is not None:
        return slug
    if value in (None, "", "pendiente"):
        primero = session.scalar(
            select(EstadoReserva.slug).order_by(EstadoReserva.id).limit(1)
        )
        if primero is not None:
            return primero
        raise HTTPException(
            status_code=422,
            detail="no hay estados configurados: creá uno en Ajustes > Tags",
        )
    disponibles = [s for (s,) in session.execute(select(EstadoReserva.slug))]
    raise HTTPException(
        status_code=422,
        detail=(
            f"estado desconocido: {slug!r}. Estados configurados: "
            f"{', '.join(disponibles) or '(ninguno)'}"
        ),
    )


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


def mapa_etiquetas(session: Session) -> dict[str, str | None]:
    """estado slug -> Chatwoot label (None when unmapped) — the shape the
    sync service in app/etiquetas.py consumes (T2)."""
    filas = session.scalars(select(EstadoReserva))
    return {e.slug: e.etiqueta_chatwoot for e in filas}


@router.get("", summary="Estados de reserva configurados (con su tag de Chatwoot)")
def listar_estados(session: Session = Depends(get_session)):
    return [_fila(e) for e in session.scalars(select(EstadoReserva).order_by(EstadoReserva.id))]


# --- T4: CRUD (Tokens section in /ajustes/tags) ---

def _slug_de(nombre: str) -> str:
    """Display name -> stored slug: trimmed, lowercased, inner whitespace
    runs to single hyphens (accents/diacritics kept — labels are Spanish)."""
    import re as _re
    texto = (nombre or "").strip().lower()
    return _re.sub(r"\s+", "-", texto) or "estado"


def _campo_nombre_saneado(value: str) -> str:
    nombre = (value or "").strip()
    if not nombre or len(nombre) > 60:
        raise HTTPException(
            status_code=422,
            detail="el nombre del estado es obligatorio (máx 60 caracteres)",
        )
    return nombre


@router.post("", status_code=201, summary="Crear un estado de reserva")
def crear_estado(
    payload: dict, session: Session = Depends(get_session)
):
    nombre = _campo_nombre_saneado(payload.get("nombre") or "")
    slug = _slug_de(nombre)
    if session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug)):
        raise HTTPException(status_code=409, detail=f"ya existe el estado {slug!r}")
    etiqueta_chatwoot = (payload.get("etiqueta_chatwoot") or "").strip() or None
    if etiqueta_chatwoot and len(etiqueta_chatwoot) > 120:
        raise HTTPException(status_code=422, detail="etiqueta de Chatwoot demasiado larga (máx 120)")
    color = normalizar_color(payload.get("color")) or color_desde_paleta(session)
    estado = EstadoReserva(
        nombre=nombre,
        slug=slug,
        etiqueta_chatwoot=etiqueta_chatwoot,
        color=color,
        envia_recordatorio=bool(payload.get("envia_recordatorio", True)),
        es_base=False,
    )
    session.add(estado)
    session.commit()
    session.refresh(estado)
    return _fila(estado)


@router.patch("/{slug}", summary="Editar un estado (etiqueta, envío, color, nombre)")
def editar_estado(slug: str, payload: dict, session: Session = Depends(get_session)):
    estado = session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug))
    if estado is None:
        raise HTTPException(status_code=404, detail="estado no encontrado")
    if "slug" in payload and payload["slug"] != slug:
        raise HTTPException(
            status_code=422,
            detail="el slug no se puede cambiar (es el valor almacenado de las reservas)",
        )
    if "etiqueta_chatwoot" in payload:
        valor = payload.get("etiqueta_chatwoot")
        valor = (valor or "").strip()

        if valor and len(valor) > 120:
            raise HTTPException(status_code=422, detail="etiqueta de Chatwoot demasiado larga (máx 120)")
        estado.etiqueta_chatwoot = valor or None
    if "envia_recordatorio" in payload:
        estado.envia_recordatorio = bool(payload["envia_recordatorio"])
    if "color" in payload:
        estado.color = normalizar_color(payload.get("color"))
    if "nombre" in payload:
        estado.nombre = _campo_nombre_saneado(payload["nombre"])
    session.commit()
    session.refresh(estado)
    return _fila(estado)


@router.delete("/{slug}", summary="Eliminar un estado (sin reservas asociadas)")
def eliminar_estado(slug: str, session: Session = Depends(get_session)):
    estado = session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug))
    if estado is None:
        raise HTTPException(status_code=404, detail="estado no encontrado")
    # S5 (tags-panel-v2, D2): los estados base ya no están protegidos — son
    # un template; el único guard que queda es reservas en uso.
    en_uso = session.scalar(
        select(func.count()).select_from(Reserva).where(Reserva.estado == slug)
    )
    if en_uso:
        raise HTTPException(
            status_code=409,
            detail=f"el estado lo usan {en_uso} reserva(s): reasignalas antes de eliminarlo",
        )
    session.delete(estado)
    session.commit()
    return {"eliminado": slug}

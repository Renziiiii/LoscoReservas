"""CRUD REST endpoints for reservas (S1: "CRUD con API")."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatwoot import ChatwootClient
from app.db import get_session
from app.etiquetas import sincronizar_estado_reserva
from app.estados import estado_valido, mapa_etiquetas
from app.models import Reserva
from app.schemas import ReservaOut, ReservaCreate, ReservaUpdate

router = APIRouter(prefix="/api/reservas", tags=["reservas"])


def _get_or_404(reserva_id: int, session: Session) -> Reserva:
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        raise HTTPException(status_code=404, detail="reserva no encontrada")
    return reserva


@router.post("", response_model=ReservaOut, status_code=201, summary="Registrar una reserva")
def create_reserva(
    request: Request, payload: ReservaCreate, session: Session = Depends(get_session)
):
    """Crea una reserva nueva. El teléfono se normaliza a dígitos con '+' inicial.
    El estado se valida contra la tabla configurada (S3, tags-estados-sync).
    tags-panel-v2 S5: se persiste el slug VALIDADO (con el fallback del
    default cuando `pendiente` fue eliminada), no el valor crudo."""
    reserva = Reserva(**{**payload.model_dump(),
                         "estado": estado_valido(session, payload.estado)})
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    sincronizar_estado_reserva(
        request.app.state.settings, reserva.telefono, mapa_etiquetas(session),
        None, reserva.estado,
    )
    return reserva


@router.get("", response_model=list[ReservaOut], summary="Listar reservas (con filtros opcionales)")
def list_reservas(
    estado: str | None = None,
    fecha: str | None = None,
    telefono: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    session: Session = Depends(get_session),
):
    """List reservations, newest-first by fecha/hora. Filters are optional.
    `fecha_desde`/`fecha_hasta` (inclusive, ISO yyyy-mm-dd) give a date range,
    used by the calendar to fetch only the visible window."""
    query = select(Reserva).order_by(Reserva.fecha, Reserva.hora)
    if estado:
        query = query.where(Reserva.estado == estado)
    if fecha:
        query = query.where(Reserva.fecha == fecha)
    if fecha_desde:
        query = query.where(Reserva.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.where(Reserva.fecha <= fecha_hasta)
    if telefono:
        from app.schemas import _normalize_telefono

        query = query.where(Reserva.telefono == _normalize_telefono(telefono))
    return list(session.scalars(query))


@router.get("/{reserva_id}", response_model=ReservaOut, summary="Obtener una reserva por id")
def get_reserva(reserva_id: int, session: Session = Depends(get_session)):
    return _get_or_404(reserva_id, session)


@router.patch("/{reserva_id}", response_model=ReservaOut, summary="Actualizar campos de una reserva")
def update_reserva(
    reserva_id: int,
    payload: ReservaUpdate,
    request: Request,
    session: Session = Depends(get_session),
):
    reserva = _get_or_404(reserva_id, session)
    estado_anterior = reserva.estado
    changes = payload.model_dump(exclude_unset=True)
    if "estado" in changes:
        estado_valido(session, changes["estado"])
    for field, value in changes.items():
        setattr(reserva, field, value)
    session.commit()
    session.refresh(reserva)
    sincronizar_estado_reserva(
        request.app.state.settings, reserva.telefono, mapa_etiquetas(session),
        estado_anterior, reserva.estado,
    )
    return reserva


@router.get("/{reserva_id}/chat", summary="URL de la conversación de Chatwoot de esta reserva")
def reserva_chat_url(
    reserva_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    """S9 (calendario en raíz): the calendar's event panel links straight to
    the Chatwoot conversation where the reservation was handled. Fail-soft:
    any Chatwoot problem answers url=None + motivo, never a 5xx."""
    reserva = _get_or_404(reserva_id, session)
    settings = request.app.state.settings
    motivo = None
    if not (
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    ):
        motivo = "Chatwoot no está configurado (faltan URL, token o account id en el entorno)."
    else:
        try:
            chatwoot = ChatwootClient(
                settings.chatwoot_base_url, settings.chatwoot_api_token, settings.chatwoot_account_id
            )
            contacto = chatwoot.buscar_contacto(reserva.telefono)
            if contacto is None:
                motivo = "No hay contacto de Chatwoot con ese teléfono."
            else:
                conversaciones = chatwoot.conversaciones(contacto["id"])
                if not conversaciones:
                    motivo = "El contacto no tiene conversaciones en Chatwoot."
                else:
                    abiertas = [c for c in conversaciones if c.get("status") == "open"]
                    conversacion = (abiertas or conversaciones)[0]
                    url = (
                        f"{settings.chatwoot_base_url.rstrip('/')}"
                        f"/app/accounts/{settings.chatwoot_account_id}"
                        f"/conversations/{conversacion['id']}"
                    )
                    return {"url": url, "motivo": None}
        except Exception as exc:
            motivo = f"Chatwoot no respondió: {exc}"
    return {"url": None, "motivo": motivo}


@router.delete("/{reserva_id}", status_code=204, summary="Borrar una reserva")
def delete_reserva(reserva_id: int, session: Session = Depends(get_session)):
    reserva = _get_or_404(reserva_id, session)
    session.delete(reserva)
    session.commit()
    return Response(status_code=204)

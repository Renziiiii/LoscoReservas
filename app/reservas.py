"""CRUD REST endpoints for reservas (S1: "CRUD con API")."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import obtener_configuracion
from app.db import get_session
from app.etiquetas import etiquetar_confirmada
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
    """Crea una reserva nueva. El teléfono se normaliza a dígitos con '+' inicial."""
    reserva = Reserva(**payload.model_dump())
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    config = obtener_configuracion(session, request.app.state.settings)
    etiquetar_confirmada(request.app.state.settings, reserva, config.etiqueta_confirmada)
    return reserva


@router.get("", response_model=list[ReservaOut], summary="Listar reservas (con filtros opcionales)")
def list_reservas(
    estado: str | None = None,
    fecha: str | None = None,
    telefono: str | None = None,
    session: Session = Depends(get_session),
):
    """List reservations, newest-first by fecha/hora. Filters are optional."""
    query = select(Reserva).order_by(Reserva.fecha, Reserva.hora)
    if estado:
        query = query.where(Reserva.estado == estado)
    if fecha:
        query = query.where(Reserva.fecha == fecha)
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
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(reserva, field, value)
    session.commit()
    session.refresh(reserva)
    config = obtener_configuracion(session, request.app.state.settings)
    etiquetar_confirmada(request.app.state.settings, reserva, config.etiqueta_confirmada)
    return reserva


@router.delete("/{reserva_id}", status_code=204, summary="Borrar una reserva")
def delete_reserva(reserva_id: int, session: Session = Depends(get_session)):
    reserva = _get_or_404(reserva_id, session)
    session.delete(reserva)
    session.commit()
    return Response(status_code=204)

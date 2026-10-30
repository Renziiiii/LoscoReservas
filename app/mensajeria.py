"""Reminder engine (S3/S8) — Valen's rule, channel-aware (L7).

- T-48h (reservas_para_confirmar): message confirming the reservation.
  "2 dias antes mejor (para confirmar la reserva)"
- Same day, sent at the configured hour (Valen: "Entre as 17 y 18"):
  message confirming the guest count.

Every send is idempotent per reservation via *_enviada_at timestamps, so the
daily cron can run safely any number of times.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatwoot import ChatwootClient, FueraDeVentana, NoHayConversacion
from app.models import Reserva

logger = logging.getLogger("losco.mensajeria")


def _query(session: Session, fecha: date, columna_enviada) -> list[Reserva]:
    stmt = (
        select(Reserva)
        .where(Reserva.fecha == fecha)
        .where(Reserva.estado != "cancelada")
        .where(Reserva.enviar_recordatorio.is_(True))
        .where(columna_enviada.is_(None))
        .order_by(Reserva.hora)
    )
    return list(session.scalars(stmt))


def reservas_para_confirmar(session: Session, hoy: date) -> list[Reserva]:
    """Reservations two days out (T-48h) that still owe the confirmation."""
    dos_dias = date.fromordinal(hoy.toordinal() + 2)
    return _query(session, dos_dias, Reserva.confirmacion_enviada_at)


def reservas_para_recordar_invitados(session: Session, hoy: date) -> list[Reserva]:
    """Today's reservations that still owe the guest-count message."""
    return _query(session, hoy, Reserva.invitados_enviada_at)


def _texto_confirmacion(reserva: Reserva) -> str:
    return (
        f"Hola {reserva.nombre}! Te escribimos de Losco para confirmar tu reserva "
        f"del {reserva.fecha} a las {reserva.hora.strftime('%H:%M')} para {reserva.personas} personas. "
        f"¿Contamos con vos?"
    )


def _texto_invitados(reserva: Reserva) -> str:
    return (
        f"Hola {reserva.nombre}! Tu reserva en Losco es HOY a las "
        f"{reserva.hora.strftime('%H:%M')} para {reserva.personas} personas. "
        f"¿Confirmás la cantidad de invitados?"
    )


def _plantilla_params(reserva: Reserva) -> dict:
    return {
        "1": reserva.nombre,
        "2": reserva.fecha.strftime("%d/%m"),
        "3": reserva.hora.strftime("%H:%M"),
        "4": str(reserva.personas),
    }


def _enviar_lote(
    session: Session,
    chatwoot: ChatwootClient,
    reservas: list[Reserva],
    texto_fn,
    plantilla: str,
    columna_enviada,
) -> dict:
    """Try to message each reservation; record per-item success/failure."""
    report = {"enviados": [], "fallas": []}
    for reserva in reservas:
        try:
            conversacion = chatwoot.conversacion_abierta(reserva.telefono)
            chatwoot.enviar_mensaje(
                conversacion,
                texto=texto_fn(reserva),
                plantilla=plantilla,
                plantilla_params=_plantilla_params(reserva),
            )
            setattr(reserva, columna_enviada.name, datetime.now(UTC))
            report["enviados"].append(reserva.id)
        except (NoHayConversacion, FueraDeVentana) as exc:
            report["fallas"].append({"reserva_id": reserva.id, "motivo": str(exc)})
        except Exception as exc:  # HTTP/transport errors: log and continue
            logger.warning("envío falló para reserva %s: %s", reserva.id, exc)
            report["fallas"].append({"reserva_id": reserva.id, "motivo": f"chatwoot: {exc}"})
    session.commit()
    return report


def enviar_confirmaciones(
    session: Session, chatwoot: ChatwootClient, hoy: date, template_confirmacion: str
) -> dict:
    """Daily job: T-48h reservation confirmations."""
    reservas = reservas_para_confirmar(session, hoy)
    return _enviar_lote(
        session, chatwoot, reservas, _texto_confirmacion,
        template_confirmacion, Reserva.confirmacion_enviada_at,
    )


def enviar_recordatorios_invitados(
    session: Session, chatwoot: ChatwootClient, hoy: date, template_invitados: str
) -> dict:
    """Daily job: same-day guest-count confirmations (Valen: 17:00-18:00)."""
    reservas = reservas_para_recordar_invitados(session, hoy)
    return _enviar_lote(
        session, chatwoot, reservas, _texto_invitados,
        template_invitados, Reserva.invitados_enviada_at,
    )

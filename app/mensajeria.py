"""Reminder engine (S3/S8/S12) — Valen's rule, always by WhatsApp.

- T-48h (reservas_para_confirmar): message confirming the reservation.
  "2 dias antes mejor (para confirmar la reserva)"
- Same day, sent at the configured hour (Valen: "Entre as 17 y 18"):
  message confirming the guest count.

Every send goes to the WhatsApp conversation of the phone stored on the
reserva (S12/L47), no matter which channel managed it: the engine never
consults the contact's conversation list (that could pick an Instagram
chat, which Meta rejects outside its 24h window) — it asks Chatwoot to
ensure the WhatsApp conversation of that phone and sends the selected
template on it.

Every send is idempotent per reservation via *_enviada_at timestamps, so the
daily cron can run safely any number of times.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chatwoot import (
    ChatwootClient,
    NoHayConversacion,
    PlantillaNoDisponible,
)
from app.config import Settings
from app.etiquetas import sincronizar_conversacion_nueva
from app.models import Configuracion, EstadoReserva, Reserva

logger = logging.getLogger("losco.mensajeria")


def _query(session: Session, fecha: date, columna_enviada) -> list[Reserva]:
    """S2 (tags-panel-v2): whether a reserva gets the automated message is
    now decided by the TRIGGER TAG configured in /ajustes
    (`Configuracion.tag_disparadora`): only reservas whose estado has that
    tag mapped (`EstadoReserva.etiqueta_chatwoot`) are picked. INNER join on
    purpose: an unmapped or unknown estado sends nothing (D1: the trigger
    lives solely in Mensajes automatizados now). The legacy per-estado
    `envia_recordatorio` flag is no longer part of the decision (S6)."""
    tag_disparadora = (
        select(Configuracion.tag_disparadora)
        .where(Configuracion.id == 1)
        .scalar_subquery()
    )
    stmt = (
        select(Reserva)
        .join(EstadoReserva, EstadoReserva.slug == Reserva.estado)
        .where(EstadoReserva.etiqueta_chatwoot == tag_disparadora)
        .where(Reserva.fecha == fecha)
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


def _enviar_lote(
    session: Session,
    chatwoot: ChatwootClient,
    reservas: list[Reserva],
    plantilla: str,
    columna_enviada,
    settings: Settings | None = None,
) -> dict:
    """Try to message each reservation; record per-item success/failure.

    A failure on one reserva never stops the batch, and *_enviada_at is
    only set after a successful send (idempotent retries).

    T6 (tags-estados-sync): when `settings` carries the Chatwoot
    credentials, the conversation asegurar resolved/created gets the
    contact's labels BEFORE the template send (S6/D2 — the new WhatsApp
    chat inherits the contact's tags even without the webhook)."""
    report = {"enviados": [], "fallas": []}
    for reserva in reservas:
        try:
            conversacion = chatwoot.asegurar_conversacion_whatsapp(
                reserva.telefono, reserva.nombre
            )
            if settings is not None:
                try:
                    sincronizar_conversacion_nueva(
                        settings, conversacion.get("id"),
                        telefono=reserva.telefono,
                        transport=getattr(chatwoot, "http_transport", None),
                    )
                except Exception as exc:  # fail-soft (L6)
                    logger.warning(
                        "sync de etiquetas post-asegurar falló para reserva %s: %s",
                        reserva.id, exc,
                    )
            chatwoot.enviar_mensaje(conversacion, plantilla)
            setattr(reserva, columna_enviada.name, datetime.now(UTC))
            report["enviados"].append(reserva.id)
        except (NoHayConversacion, PlantillaNoDisponible) as exc:
            report["fallas"].append({"reserva_id": reserva.id, "motivo": str(exc)})
        except Exception as exc:  # HTTP/transport errors: log and continue
            logger.warning("envío falló para reserva %s: %s", reserva.id, exc)
            report["fallas"].append({"reserva_id": reserva.id, "motivo": f"chatwoot: {exc}"})
    session.commit()
    return report


def enviar_confirmaciones(
    session: Session, chatwoot: ChatwootClient, hoy: date, template_confirmacion: str,
    settings: Settings | None = None,
) -> dict:
    """Daily job: T-48h reservation confirmations."""
    reservas = reservas_para_confirmar(session, hoy)
    return _enviar_lote(
        session, chatwoot, reservas,
        template_confirmacion, Reserva.confirmacion_enviada_at,
        settings=settings,
    )


def enviar_recordatorios_invitados(
    session: Session, chatwoot: ChatwootClient, hoy: date, template_invitados: str,
    settings: Settings | None = None,
) -> dict:
    """Daily job: same-day guest-count confirmations (Valen: 17:00-18:00)."""
    reservas = reservas_para_recordar_invitados(session, hoy)
    return _enviar_lote(
        session, chatwoot, reservas,
        template_invitados, Reserva.invitados_enviada_at,
        settings=settings,
    )

"""Contact labeling in Chatwoot (S10/L21): "la gente que tenga reserva
confiramada, tiene que tener una tag en chatwoot que sea:Reserva confirmada".

When a reservation is (or becomes) "confirmada", the Chatwoot contact gets
the "Reserva confirmada" label. The Chatwoot labels API REPLACES the whole
label list, so existing labels are read and merged first.

Fail-soft by design (L6): without a configured Chatwoot token, or on any API
error, the reservation save must never fail — the incident is only logged.
"""

import logging

from app.chatwoot import ChatwootClient
from app.config import Settings
from app.models import Reserva

logger = logging.getLogger("losco.etiquetas")

ETIQUETA_CONFIRMADA = "Reserva confirmada"


def _configurado(settings: Settings) -> bool:
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


def etiquetar_confirmada(
    settings: Settings, reserva: Reserva, transport=None
) -> bool:
    """Add the "Reserva confirmada" label to the reserva's contact in
    Chatwoot. Returns True when the label ended up on the contact; False
    otherwise (not confirmada / unconfigured / API failure). Never raises.

    `transport` is an httpx transport override for tests."""
    if reserva.estado != "confirmada":
        return False
    if not _configurado(settings):
        logger.info("Chatwoot sin configurar: no se etiquetó la reserva %s", reserva.id)
        return False
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
        contacto = chatwoot.buscar_contacto(reserva.telefono)
        if contacto is None:
            logger.info(
                "sin contacto en Chatwoot para %s: no se etiquetó la reserva %s",
                reserva.telefono,
                reserva.id,
            )
            return False
        actuales = chatwoot.listar_labels_contacto(contacto["id"])
        if ETIQUETA_CONFIRMADA in actuales:
            return True  # idempotent: already labeled
        chatwoot.agregar_labels_contacto(contacto["id"], actuales + [ETIQUETA_CONFIRMADA])
        return True
    except Exception as exc:  # transport/HTTP errors must not break the save
        logger.warning("etiquetar en Chatwoot falló para la reserva %s: %s", reserva.id, exc)
        return False

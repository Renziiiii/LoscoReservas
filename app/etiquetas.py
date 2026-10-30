"""Contact labeling in Chatwoot (S10/L21/L24): confirmed reservations get a
configurable label on the Chatwoot contact.

When a reservation is (or becomes) "confirmada", the Chatwoot contact gets
the label configured in `Configuracion.etiqueta_confirmada`. The Chatwoot
labels API REPLACES the whole label list, so existing labels are read and
merged first.

Fail-soft by design (L6): without a configured Chatwoot token, or on any API
error, the reservation save must never fail — the incident is only logged.
"""

import logging

from app.chatwoot import ChatwootClient
from app.config import Settings
from app.models import Reserva

logger = logging.getLogger("losco.etiquetas")


def _configurado(settings: Settings) -> bool:
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


def etiquetar_confirmada(
    settings: Settings, reserva: Reserva, etiqueta: str, transport=None
) -> bool:
    """Add the configured confirmed-reservation label to the reserva's
    contact in Chatwoot. Returns True when the label ended up on the contact;
    False otherwise (not confirmada / unconfigured / API failure). Never
    raises.

    `transport` is an httpx transport override for tests."""
    if reserva.estado != "confirmada":
        return False
    if not etiqueta:
        logger.info("etiqueta vacía: no se etiquetó la reserva %s", reserva.id)
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
        if etiqueta in actuales:
            return True  # idempotent: already labeled
        chatwoot.agregar_labels_contacto(contacto["id"], actuales + [etiqueta])
        return True
    except Exception as exc:  # transport/HTTP errors must not break the save
        logger.warning("etiquetar en Chatwoot falló para la reserva %s: %s", reserva.id, exc)
        return False


def migrar_etiqueta(
    settings: Settings, vieja: str, nueva: str, transport=None
) -> dict:
    """Swap `vieja` for `nueva` on every contact that has `vieja`, keeping
    all other labels. Returns {"total": N, "migrados": [contact_ids],
    "fallas": [{"contacto_id": ..., "motivo": ...}]}.

    Never raises: per-contact HTTP failures are recorded in `fallas` and the
    migration continues; global failures return an empty result set.
    """
    if not vieja or not nueva:
        return {"total": 0, "migrados": [], "fallas": []}
    if not _configurado(settings):
        logger.info("Chatwoot sin configurar: no se migró etiqueta %s -> %s", vieja, nueva)
        return {"total": 0, "migrados": [], "fallas": []}

    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
        contactos = chatwoot.contactos_con_etiqueta(vieja)
    except Exception as exc:
        logger.warning("listar contactos con etiqueta %s falló: %s", vieja, exc)
        return {"total": 0, "migrados": [], "fallas": []}

    migrados: list[int] = []
    fallas: list[dict] = []
    for contacto in contactos:
        contacto_id = contacto.get("id")
        try:
            actuales = chatwoot.listar_labels_contacto(contacto_id)
            nuevas = [label for label in actuales if label != vieja]
            if nueva not in nuevas:
                nuevas.append(nueva)
            chatwoot.agregar_labels_contacto(contacto_id, nuevas)
            migrados.append(contacto_id)
        except Exception as exc:
            logger.warning(
                "migrar etiqueta para contacto %s falló (%s -> %s): %s",
                contacto_id,
                vieja,
                nueva,
                exc,
            )
            fallas.append({"contacto_id": contacto_id, "motivo": str(exc)})

    return {"total": len(contactos), "migrados": migrados, "fallas": fallas}

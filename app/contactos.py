"""Contact phone sync in Chatwoot (S11/L46).

When the agent registers a reservation from the Chatwoot tab for a contact
without a phone (Instagram chats arrive without one), the number they
asked for in the chat is saved on the Chatwoot contact TOO — but only when
the contact has no phone: an existing number is never overwritten.

This keeps one single Chatwoot contact per phone, so the
reserva-confirmada label lands on the chat being managed and the reminder
engine finds the same contact when it writes by WhatsApp.

Fail-soft by design (L6, mirrors app/etiquetas.py): without a configured
Chatwoot token, or on any API error, the reservation save must never
fail — the incident is only logged.
"""

import logging

from app.chatwoot import ChatwootClient
from app.config import Settings

logger = logging.getLogger("losco.contactos")


def _configurado(settings: Settings) -> bool:
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


def sincronizar_telefono_contacto(
    settings: Settings, contacto_id: int | None, telefono: str, transport=None
) -> bool:
    """Write `telefono` on the Chatwoot contact, only if it has none.

    Returns True when the contact ended up with a phone, False otherwise
    (no contact id / unconfigured / already had one / any failure).
    Never raises.

    `transport` is an httpx transport override for tests."""
    if not contacto_id:
        return False
    if not _configurado(settings):
        logger.info(
            "Chatwoot sin configurar: no se sincronizó el teléfono del contacto %s",
            contacto_id,
        )
        return False
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
            transport=transport,
        )
        contacto = chatwoot.obtener_contacto(contacto_id)
        if contacto is None:
            logger.info(
                "contacto %s no encontrado en Chatwoot: no se sincronizó el teléfono",
                contacto_id,
            )
            return False
        if (contacto.get("phone_number") or "").strip():
            logger.info(
                "el contacto %s ya tiene teléfono (%s): no se sobreescribe",
                contacto_id,
                contacto["phone_number"],
            )
            return False
        chatwoot.actualizar_telefono_contacto(contacto_id, telefono)
        logger.info("teléfono %s guardado en el contacto %s", telefono, contacto_id)
        return True
    except Exception as exc:  # fail-soft (L6): never break the save
        logger.warning(
            "sincronizar teléfono del contacto %s falló: %s", contacto_id, exc
        )
        return False

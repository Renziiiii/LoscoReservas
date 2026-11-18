"""Chatwoot webhook receiver (T3, S6/D3, tags-estados-sync.md).

Chatwoot (Webhook Management → account-level webhook with URL
https://reservas.loscobar.com.ar/api/webhooks/chatwoot?token=...) notifies
this app of `conversation_created` and `contact_updated` so labels flow to
chats created outside the app too (by agents, other integrations or any
channel). See docs/webhook-chatwoot.md (T7).

Two synced behaviors, both powered by the T2 service:
- conversation_created: a new conversation inherits ALL of its contact's
  current labels (S6/D2).
- contact_updated: the contact gained/changed labels → propagate them to
  every conversation it already has (S5 variant).

Auth: shared secret token (query `?token=` or `X-Chatwoot-Token` header) via
LOSCO_CHATWOOT_WEBHOOK_TOKEN. Fail-closed: unset/incorrect token answers 404
(not 401/403) so the endpoint's existence is not even revealed.

Fail-soft: valid-token events NEVER return 5xx — any sync error is logged;
Chatwoot's own webhook delivery must not break on our internals.
"""

import hmac
import logging

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from app.chatwoot import ChatwootClient
from app.config import Settings
from app.etiquetas import (
    sincronizar_conversacion_nueva,
    sincronizar_contacto_todas_las_conversaciones,
)

logger = logging.getLogger("losco.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _autorizado(settings: Settings, token: str | None) -> bool:
    """Shared-secret check, fail-closed (empty configured token => 404).
    Constant-time comparison so timing cannot leak the token."""
    esperado = settings.chatwoot_webhook_token
    if not esperado or not token:
        return False
    return hmac.compare_digest(esperado, token)


def _contacto_de_conversacion(settings: Settings, conversacion: dict) -> int | None:
    """The contact id of a webhook conversation payload. Chatwoot nests it
    in `meta.sender` = {type: "contact", id: N}; when the payload lacks it,
    the conversation is fetched from the API (read-only, single call)."""
    sender = ((conversacion or {}).get("meta") or {}).get("sender") or {}
    if sender.get("id"):
        return sender["id"]
    conv_id = (conversacion or {}).get("id")
    if not conv_id:
        return None
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url,
            settings.chatwoot_api_token,
            settings.chatwoot_account_id,
        )
        data = chatwoot.obtener_conversacion(conv_id)
        sender = ((data or {}).get("meta") or {}).get("sender") or {}
        return sender.get("id")
    except Exception as exc:
        logger.warning("conversación %s no consultada: %s", conv_id, exc)
        return None


@router.post("/chatwoot", include_in_schema=False)
def webhook_chatwoot(
    request: Request,
    payload: dict,
    token: str | None = Query(default=None),
    x_chatwoot_token: str | None = Header(default=None),
):
    settings: Settings = request.app.state.settings
    entregado = token or x_chatwoot_token
    if not _autorizado(settings, entregado):
        return JSONResponse({"detail": "not found"}, status_code=404)

    event = (payload or {}).get("event") or ""
    try:
        if event == "conversation_created":
            conversacion = payload.get("conversation") or {}
            contacto_id = _contacto_de_conversacion(settings, conversacion)
            sincronizar_conversacion_nueva(
                settings, conversacion.get("id"), contacto_id=contacto_id,
            )
        elif event == "contact_updated":
            contacto_id = payload.get("id") or (payload.get("contact") or {}).get("id")
            if contacto_id:
                sincronizar_contacto_todas_las_conversaciones(settings, contacto_id)
        # other events: ignored on purpose (no API traffic)
    except Exception as exc:  # fail-soft (L6): a valid webhook never 500s
        logger.warning("webhook %s falló: %s", event or "?", exc)
    return {"received": True}

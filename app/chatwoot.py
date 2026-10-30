"""Chatwoot API client (agent token, API v1).

Multi-channel reality (L7): Chatwoot serves WhatsApp, Instagram and Messenger.
Meta's rules differ per channel:
- WhatsApp: outside the 24h customer-service window, only approved templates
  (UTILITY for reservation confirmations) may be sent.
- Instagram/Messenger: free text inside the 24h window; there are no approved
  templates for business-initiated messages, so outside the window we cannot
  send anything.

The exact template_params shape is documented at developers.chatwoot.com
(create new message); it must be verified live against the real server in T7.
"""

from datetime import UTC, datetime, timedelta

import httpx

VENTANA_HORAS = 24


class NoHayConversacion(Exception):
    """The phone matches no contact / no open conversation in Chatwoot."""


class FueraDeVentana(Exception):
    """Channel cannot receive business-initiated free text or templates."""


class ChatwootClient:
    def __init__(self, base_url: str, api_token: str, account_id: int, transport=None):
        self._account_id = account_id
        self._http = httpx.Client(
            base_url=base_url,
            headers={"api_access_token": api_token},
            transport=transport,
            timeout=15,
        )

    # --- lookups ---

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def buscar_contacto(self, telefono: str) -> dict | None:
        data = self._get(f"/api/v1/accounts/{self._account_id}/contacts/search", {"q": telefono})
        payload = data.get("payload") or []
        return payload[0] if payload else None

    def conversaciones(self, contacto_id: int) -> list[dict]:
        data = self._get(f"/api/v1/accounts/{self._account_id}/contacts/{contacto_id}/conversations")
        return data.get("payload") or []

    def listar_inboxes(self) -> list[dict]:
        """All inboxes of the account (used to auto-detect the WhatsApp one)."""
        data = self._get(f"/api/v1/accounts/{self._account_id}/inboxes")
        return data.get("payload") or []

    def listar_plantillas(self, inbox_id: int) -> list[dict]:
        """Cached WhatsApp templates of one inbox (L20: PR #15277).
        422 if the inbox is not a WhatsApp (native or Twilio) inbox."""
        data = self._get(f"/api/v1/accounts/{self._account_id}/inboxes/{inbox_id}/message_templates")
        return data.get("payload") or []

    def plantillas_whatsapp(self) -> list[dict]:
        """Templates of the first inbox that answers, preferring native
        WhatsApp (Channel::Whatsapp), then Twilio, then anything else:
        non-WhatsApp inboxes answer 422 and are skipped."""

        def prioridad(inbox: dict) -> int:
            channel = inbox.get("channel_type") or ""
            if channel.endswith("Whatsapp"):
                return 0
            if channel.endswith("Twilio"):
                return 1
            return 2

        for inbox in sorted(self.listar_inboxes(), key=prioridad):
            try:
                return self.listar_plantillas(inbox["id"])
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 422:
                    continue  # not a WhatsApp inbox: try the next one
                raise
        return []

    def conversacion_abierta(self, telefono: str) -> dict:
        """Open conversation for a phone, or raises NoHayConversacion."""
        contacto = self.buscar_contacto(telefono)
        if contacto is None:
            raise NoHayConversacion(f"sin contacto en Chatwoot para {telefono}")
        abiertas = [
            c for c in self.conversaciones(contacto["id"]) if c.get("status") == "open"
        ]
        if not abiertas:
            raise NoHayConversacion(f"sin conversación abierta para {telefono}")
        # most recently active open conversation wins
        return max(abiertas, key=lambda c: c.get("last_non_activity_message_at") or 0)

    # --- channel awareness ---

    @staticmethod
    def _canal(conversacion: dict) -> str:
        return (conversacion.get("meta") or {}).get("channel") or ""

    @staticmethod
    def _ultima_actividad(conversacion: dict) -> datetime | None:
        ts = (conversacion.get("meta") or {}).get("last_non_activity_message_at")
        return datetime.fromtimestamp(ts, tz=UTC) if ts else None

    def ventana_vencida(self, conversacion: dict, ahora: datetime | None = None) -> bool:
        ultima = self._ultima_actividad(conversacion)
        if ultima is None:
            return True
        ahora = ahora or datetime.now(UTC)
        return ahora - ultima > timedelta(hours=VENTANA_HORAS)

    def puede_plantilla(self, conversacion: dict) -> bool:
        """Only WhatsApp supports approved templates."""
        return "Whatsapp" in self._canal(conversacion)

    # --- contact labels (T14/S10) ---

    def listar_labels_contacto(self, contacto_id: int) -> list[str]:
        """Labels currently on a contact. NOTE: the POST endpoint OVERWRITES
        the whole list, so callers must merge with this before assigning."""
        data = self._get(f"/api/v1/accounts/{self._account_id}/contacts/{contacto_id}/labels")
        return list(data.get("payload") or [])

    def agregar_labels_contacto(self, contacto_id: int, labels: list[str]) -> list[str]:
        """Set the contact's full label list (Chatwoot semantics: replace)."""
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/contacts/{contacto_id}/labels",
            json={"labels": labels},
        )
        response.raise_for_status()
        return list(response.json().get("payload") or [])

    # --- sending ---

    def enviar_mensaje(
        self,
        conversacion: dict,
        texto: str,
        plantilla: str | None = None,
        plantilla_params: dict | None = None,
    ) -> dict:
        """Send one message, channel-aware.

        - outside the window on WhatsApp → template (plantilla required)
        - inside the window (any channel) → free text
        - outside the window on IG/Messenger → FueraDeVentana (nothing sent)
        """
        if plantilla and self.puede_plantilla(conversacion) and self.ventana_vencida(conversacion):
            content_attributes = {
                "template_params": {
                    "name": plantilla,
                    "category": "UTILITY",
                    "processed_params": plantilla_params or {},
                }
            }
        elif not self.ventana_vencida(conversacion):
            content_attributes = {}
        else:
            canal = self._canal(conversacion) or "canal desconocido"
            raise FueraDeVentana(
                f"ventana de 24hs vencida en {canal}: no se puede iniciar conversación"
            )

        body = {
            "content": texto,
            "message_type": "outgoing",
            "private": False,
            "content_attributes": content_attributes,
        }
        conversation_id = conversacion["id"]
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages",
            json=body,
        )
        response.raise_for_status()
        return response.json()

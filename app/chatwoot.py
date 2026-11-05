"""Chatwoot API client (agent token, API v1).

Multi-channel reality (L7/L39): Chatwoot serves WhatsApp, Instagram and
Messenger, and the send rules differ per channel:
- WhatsApp: ALWAYS the approved UTILITY template selected in /ajustes, sent
  verbatim as a fixed message — no processed_params (L39: the app only
  supports 0-variable templates, and Meta charges a utility template the
  same as a session message, so free text has no advantage).
- Instagram/Messenger: free text inside the 24h window; there are no approved
  templates for business-initiated messages, so outside the window we cannot
  send anything.

The template send shape is version-specific and was verified live against
production (L42): Chatwoot 4.17's MessageBuilder reads `template_params`
as a TOP-LEVEL body key (a newer documented shape nests it inside
content_attributes — 4.17 silently ignores that), and Meta requires the
template's own `language` (a missing language.code is rejected).
"""

import re
from datetime import UTC, datetime, timedelta

import httpx

VENTANA_HORAS = 24


class NoHayConversacion(Exception):
    """The phone matches no contact / no open conversation in Chatwoot."""


class FueraDeVentana(Exception):
    """Channel cannot receive business-initiated free text or templates."""


class PlantillaNoDisponible(Exception):
    """The configured WhatsApp template is not among the inbox's approved
    cached templates (missing, rejected by Meta, or not synced) — nothing
    is sent and the failure is recorded per reservation (L42)."""


def variables_plantilla(plantilla: dict) -> int:
    """Number of {{N}} placeholders in the BODY of a cached WhatsApp
    template (L39). The app sends templates verbatim as fixed messages, so
    only 0-variable templates are compatible; /ajustes warns on the rest."""
    for componente in plantilla.get("components") or []:
        if (componente.get("type") or "").upper() == "BODY":
            return len(re.findall(r"\{\{\d+\}\}", str(componente.get("text") or "")))
    return 0


class ChatwootClient:
    def __init__(self, base_url: str, api_token: str, account_id: int, transport=None):
        self._account_id = account_id
        self._http = httpx.Client(
            base_url=base_url,
            headers={
                "api_access_token": api_token,
                # Bearer is supported from Chatwoot v4.19; sending both
                # eliminates the auth-scheme variable (L22).
                "Authorization": f"Bearer {api_token}",
            },
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

    def listar_labels_cuenta(self) -> list[dict]:
        """All labels defined at account level (L24: selector in /ajustes).
        Returned shape: {id, title, description?, color}."""
        data = self._get(f"/api/v1/accounts/{self._account_id}/labels")
        return list(data.get("payload") or [])

    def contactos_con_etiqueta(self, titulo: str) -> list[dict]:
        """Contacts that currently have the account label `titulo`.

        Uses the contacts filter endpoint. The exact filter semantics are
        unverified live (see L20/L22), so every returned contact is
        re-verified client-side against its own `labels` list.
        Pages through 15-contact pages up to a 50-page safety cap.
        """
        contactos: list[dict] = []
        page = 1
        path = f"/api/v1/accounts/{self._account_id}/contacts/filter"
        while page <= 50:
            response = self._http.post(
                path,
                params={"page": page},
                json={
                    "payload": [
                        {
                            "attribute_key": "labels",
                            "filter_operator": "equal_to",
                            "values": [titulo],
                        }
                    ]
                },
            )
            response.raise_for_status()
            data = response.json()
            payload = list(data.get("payload") or [])
            contactos.extend(
                [c for c in payload if titulo in (c.get("labels") or [])]
            )
            if len(payload) < 15:
                break
            page += 1
        return contactos

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

    def plantilla_aprobada(self, nombre: str) -> dict | None:
        """The inbox's approved cached WhatsApp template with this exact
        name (L42). Its `language` is required to build a valid template
        send — Meta rejects a non-string template.language.code, and
        Chatwoot's own matcher expects the exact language too."""
        for plantilla in self.plantillas_whatsapp():
            if (
                plantilla.get("name") == nombre
                and (plantilla.get("status") or "").lower() == "approved"
            ):
                return plantilla
        return None

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
        # most recently active open conversation wins (top-level field, L38-4)
        return max(abiertas, key=lambda c: self._ts_ultima_actividad(c) or 0)

    # --- channel awareness ---

    @staticmethod
    def _canal(conversacion: dict) -> str:
        return (conversacion.get("meta") or {}).get("channel") or ""

    @staticmethod
    def _ts_ultima_actividad(conversacion: dict) -> int | None:
        """Last activity timestamp. Chatwoot 4.17.x exposes it as a TOP-LEVEL
        `last_activity_at`; newer versions may put it in
        meta.last_non_activity_message_at, kept as fallback (L38-4: the meta
        field is absent in 4.17 payloads, which made every conversation look
        window-expired)."""
        ts = conversacion.get("last_activity_at")
        if ts is None:
            ts = (conversacion.get("meta") or {}).get("last_non_activity_message_at")
        return ts

    @classmethod
    def _ultima_actividad(cls, conversacion: dict) -> datetime | None:
        ts = cls._ts_ultima_actividad(conversacion)
        return datetime.fromtimestamp(ts, tz=UTC) if ts else None

    def ventana_vencida(self, conversacion: dict, ahora: datetime | None = None) -> bool:
        """Whether the 24h customer-service window is over. Only consulted
        for IG/Messenger free text (L39: WhatsApp always sends the template,
        regardless of the window). NOTE: last_activity_at counts outgoing
        activity too, so this is an approximation for those channels."""
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

    # --- conversation labels (T16/S10/L27) ---

    def listar_labels_conversacion(self, conversation_id: int) -> list[str]:
        """Labels currently on a conversation. Like contacts, the POST
        endpoint OVERWRITES the whole list, so callers must merge first."""
        data = self._get(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/labels"
        )
        return list(data.get("payload") or [])

    def agregar_labels_conversacion(self, conversation_id: int, labels: list[str]) -> list[str]:
        """Set the conversation's full label list (Chatwoot semantics: replace)."""
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/labels",
            json={"labels": labels},
        )
        response.raise_for_status()
        return list(response.json().get("payload") or [])

    def conversaciones_con_etiqueta(self, titulo: str) -> list[dict]:
        """Open-ish conversations that currently have the account label `titulo`.

        Uses the conversations index with a labels[] filter. The exact filter
        semantics are unverified live (see L20/L22), so every returned
        conversation is re-verified client-side against its own `labels` list.
        Pages while a page returns a non-empty payload, up to a 50-page safety cap.
        """
        conversaciones: list[dict] = []
        seen: set[int] = set()
        page = 1
        path = f"/api/v1/accounts/{self._account_id}/conversations"
        while page <= 50:
            data = self._get(
                path,
                params={"status": "all", "labels[]": titulo, "page": page},
            )
            payload = list(data.get("payload") or [])
            if not payload:
                break
            for c in payload:
                cid = c.get("id")
                if cid is None or cid in seen:
                    continue
                seen.add(cid)
                if titulo in (c.get("labels") or []):
                    conversaciones.append(c)
            page += 1
        return conversaciones

    # --- sending ---

    def enviar_mensaje(
        self,
        conversacion: dict,
        texto: str,
        plantilla: str | None = None,
        plantilla_params: dict | None = None,
    ) -> dict:
        """Send one message, channel-aware.

        - WhatsApp → ALWAYS the template (L39), verbatim as a fixed message:
          processed_params stays empty because only 0-variable templates are
          supported (the /ajustes selector warns about the rest).
          L42: `template_params` goes TOP-LEVEL in the body — Chatwoot 4.17
          reads params[:template_params] (stored in additional_attributes,
          the only place the WhatsApp sender looks); nested inside
          content_attributes it is silently ignored and the message falls
          back to free text (which fails outright once the 24h window
          expires). The template's own `language` must be included (Meta
          rejects a missing language.code); a template absent from the
          inbox's approved list raises PlantillaNoDisponible, sends nothing.
        - IG/Messenger inside the 24h window → free text
        - IG/Messenger outside the window → FueraDeVentana (nothing sent)
        """
        body = {
            "content": texto,
            "message_type": "outgoing",
            "private": False,
        }
        if plantilla and self.puede_plantilla(conversacion):
            aprobada = self.plantilla_aprobada(plantilla)
            if aprobada is None or not aprobada.get("language"):
                raise PlantillaNoDisponible(
                    f"la plantilla {plantilla!r} no figura entre las aprobadas "
                    "del inbox de WhatsApp (¿nombre correcto? ¿aprobada por "
                    "Meta? ¿sincronizada en Chatwoot?)"
                )
            body["template_params"] = {
                "name": plantilla,
                "category": "UTILITY",
                "language": aprobada["language"],
                "processed_params": plantilla_params or {},
            }
        elif self.ventana_vencida(conversacion):
            canal = self._canal(conversacion) or "canal desconocido"
            raise FueraDeVentana(
                f"ventana de 24hs vencida en {canal}: no se puede iniciar conversación"
            )
        conversation_id = conversacion["id"]
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages",
            json=body,
        )
        response.raise_for_status()
        return response.json()

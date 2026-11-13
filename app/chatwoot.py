"""Chatwoot API client (agent token, API v1).

Automated messaging is ALWAYS WhatsApp (S12/L47): the reminder jobs send
the approved UTILITY template selected in /ajustes to the phone stored on
the reserva, no matter which channel managed the reservation — Instagram
and Messenger reject business-initiated free text outside their 24h
window, and the contact's most-active conversation may well be an IG chat.
`asegurar_conversacion_whatsapp` resolves (creating when missing) the
contact, its WhatsApp contact_inbox and the conversation that carries the
phone, so `enviar_mensaje` can send the template on it. Free text by
IG/Messenger no longer exists in the automated path.

The template send shape is version-specific and was verified live against
production (L42): Chatwoot 4.17's MessageBuilder reads `template_params`
as a TOP-LEVEL body key (a newer documented shape nests it inside
content_attributes — 4.17 silently ignores that), and Meta requires the
template's own `language` (a missing language.code is rejected).
"""

import re

import httpx


class NoHayConversacion(Exception):
    """No WhatsApp inbox / contact / conversation could be resolved for the
    reserva's phone in Chatwoot."""


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


def cuerpo_plantilla(plantilla: dict) -> str:
    """The fixed BODY text of a cached WhatsApp template — the exact message
    the client receives (L45: Chatwoot's bubble must show the same text, not
    a personalized summary, to avoid confusion about what was delivered)."""
    for componente in plantilla.get("components") or []:
        if (componente.get("type") or "").upper() == "BODY":
            return str(componente.get("text") or "")
    return ""


def _prioridad_inbox_whatsapp(inbox: dict) -> int:
    """Sort priority for finding the account's WhatsApp inbox: native
    WhatsApp first, then Twilio, then anything else."""
    channel = inbox.get("channel_type") or ""
    if channel.endswith("Whatsapp"):
        return 0
    if channel.endswith("Twilio"):
        return 1
    return 2


def e164(telefono: str) -> str:
    r"""Best-effort E.164 for a STORED phone (L51): Chatwoot 4.17 accepts
    only the /\+[1-9]\d{1,14}/ pattern, and rows saved before the E.164
    validation may have lost the leading '+'. Prepends it only when the
    digits already carry a country code (>= 11 digits); shorter numbers
    stay untouched so Chatwoot rejects them cleanly instead of creating a
    wrong-country contact."""
    tel = (telefono or "").strip()
    if tel.startswith("+"):
        return tel
    if len(re.sub(r"\D", "", tel)) >= 11:
        return f"+{tel}"
    return tel


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

    def obtener_contacto(self, contacto_id: int) -> dict | None:
        """One account contact by id. NOTE: the show endpoint returns the
        contact DIRECTLY in `payload` (unlike create, which nests it in
        payload.contact)."""
        data = self._get(f"/api/v1/accounts/{self._account_id}/contacts/{contacto_id}")
        return data.get("payload") or None

    def actualizar_telefono_contacto(self, contacto_id: int, telefono: str) -> dict:
        """Set the contact's phone. Callers must only fill EMPTY phones
        (S11/L46): an existing number is never overwritten."""
        response = self._http.patch(
            f"/api/v1/accounts/{self._account_id}/contacts/{contacto_id}",
            json={"phone_number": telefono},
        )
        response.raise_for_status()
        return response.json().get("payload") or {}

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
        for inbox in sorted(self.listar_inboxes(), key=_prioridad_inbox_whatsapp):
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
        """Open conversation for a phone, or raises NoHayConversacion.

        NOT used by the automated sends (those always go through
        asegurar_conversacion_whatsapp, S12/L47); etiquetar still uses it
        to pick the chat being managed.
        """
        contacto = self.buscar_contacto(telefono)
        if contacto is None:
            raise NoHayConversacion(f"sin contacto en Chatwoot para {telefono}")
        abiertas = [
            c for c in self.conversaciones(contacto["id"]) if c.get("status") == "open"
        ]
        if not abiertas:
            raise NoHayConversacion(f"sin conversación abierta para {telefono}")

        def ts(c: dict) -> int:
            # most recently active open conversation wins (top-level field,
            # with the old meta field as fallback — L38-4)
            return c.get("last_activity_at") or (c.get("meta") or {}).get(
                "last_non_activity_message_at"
            ) or 0

        return max(abiertas, key=ts)

    # --- WhatsApp conversation assurance (S12/L47) ---

    def asegurar_conversacion_whatsapp(self, telefono: str, nombre: str) -> dict:
        """The WhatsApp conversation for a phone, creating what's missing.

        The automated sends always go by WhatsApp template to the
        reserva's phone, no matter which channel managed the reservation:
        this resolves (creating when needed) the contact, its WhatsApp
        contact_inbox and the conversation that carries the phone, and
        reopens it when resolved. Verified 4.17 facts (L47):
        - POST /contacts {name, phone_number, inbox_id} creates the contact
          AND its WhatsApp contact_inbox (source_id = phone digits without
          '+'); it is NOT an upsert.
        - POST /conversations {inbox_id, contact_id, source_id} resolves
          the contact_inbox (first_or_create) and, because every inbox in
          the account has lock_to_single_conversation=TRUE, RETURNS the
          existing conversation instead of duplicating; creates one when
          none exists.
        - toggle_status {status: "open"} reopens a resolved conversation;
          its response is a short payload (NOT the conversation), so the
          already-known conversation id keeps being used.
        """
        inbox_whatsapp = next(
            (
                inbox
                for inbox in sorted(self.listar_inboxes(), key=_prioridad_inbox_whatsapp)
                if (inbox.get("channel_type") or "").endswith(("Whatsapp", "Twilio"))
            ),
            None,
        )
        if inbox_whatsapp is None:
            raise NoHayConversacion("la cuenta de Chatwoot no tiene inbox de WhatsApp")
        wa_inbox_id = inbox_whatsapp["id"]

        # L51: Chatwoot only accepts E.164 phones; legacy rows may lack the '+'.
        telefono = e164(telefono)

        contacto = self.buscar_contacto(telefono)
        if contacto is None:
            response = self._http.post(
                f"/api/v1/accounts/{self._account_id}/contacts",
                json={"name": nombre, "phone_number": telefono, "inbox_id": wa_inbox_id},
            )
            response.raise_for_status()
            payload = response.json().get("payload") or {}
            contacto = payload.get("contact") or payload
        contact_id = contacto["id"]

        # WhatsApp source_id convention: digits without the leading '+'.
        source_id = telefono.lstrip("+")
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations",
            json={
                "inbox_id": wa_inbox_id,
                "contact_id": contact_id,
                "source_id": source_id,
            },
        )
        response.raise_for_status()
        conversacion = response.json()
        if conversacion.get("status") == "resolved":
            response = self._http.post(
                f"/api/v1/accounts/{self._account_id}/conversations/{conversacion['id']}/toggle_status",
                json={"status": "open"},
            )
            response.raise_for_status()
        return conversacion

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

    def enviar_mensaje(self, conversacion: dict, plantilla: str) -> dict:
        """Send the selected WhatsApp template on the conversation (S12).

        `plantilla` is required: the automated path ALWAYS sends the
        template, verbatim as a fixed message — processed_params stays
        empty because only 0-variable templates are supported (L39; the
        /ajustes selector warns about the rest). L42:
        `template_params` goes TOP-LEVEL in the body — Chatwoot 4.17 reads
        params[:template_params] (stored in additional_attributes, the only
        place the WhatsApp sender looks); nested inside content_attributes
        it is silently ignored and the message falls back to free text
        (which fails outright once the 24h window expires). The template's
        own `language` must be included (Meta rejects a missing
        language.code); a template absent from the inbox's approved list
        raises PlantillaNoDisponible, sends nothing. L45: the bubble
        `content` is the template's own BODY text — exactly what the client
        receives.
        """
        aprobada = self.plantilla_aprobada(plantilla)
        if aprobada is None or not aprobada.get("language"):
            raise PlantillaNoDisponible(
                f"la plantilla {plantilla!r} no figura entre las aprobadas "
                "del inbox de WhatsApp (¿nombre correcto? ¿aprobada por "
                "Meta? ¿sincronizada en Chatwoot?)"
            )
        body = {
            # L45: show the client exactly what they got — the template body.
            "content": cuerpo_plantilla(aprobada),
            "message_type": "outgoing",
            "private": False,
            "template_params": {
                "name": plantilla,
                "category": "UTILITY",
                "language": aprobada["language"],
                "processed_params": {},
            },
        }
        conversation_id = conversacion["id"]
        response = self._http.post(
            f"/api/v1/accounts/{self._account_id}/conversations/{conversation_id}/messages",
            json=body,
        )
        response.raise_for_status()
        return response.json()

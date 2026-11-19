"""T5/T22 messaging tests (S3/S8/S12) — always-WhatsApp reminder engine.

The ChatwootClient is exercised against a fake HTTP transport
(httpx.MockTransport), so the real conversation-assurance + template-send
logic runs; only Chatwoot itself is faked. Dates are fixed (no clock
dependence).

S12/L47: automated reminders ALWAYS go as the selected WhatsApp template
to the phone stored on the reserva — no matter which channel managed it.
The fake simulates the verified Chatwoot 4.17 facts: POST /contacts also
creates the WhatsApp contact_inbox, POST /conversations returns the
contact_inbox's existing conversation (lock_to_single_conversation) or
creates one, and toggle_status answers with a short payload (not the full
conversation)."""

import json
from datetime import UTC, date, datetime

import httpx

from app.chatwoot import ChatwootClient
from app.mensajeria import (
    enviar_confirmaciones,
    enviar_recordatorios_invitados,
    reservas_para_confirmar,
    reservas_para_recordar_invitados,
)
from app.models import Configuracion, EstadoReserva, Reserva

HOY = date(2026, 3, 20)  # Friday


def crear(client, **overrides):
    payload = {
        "nombre": "Juan Pérez",
        "telefono": "+5491111111111",
        "fecha": "2026-03-22",  # HOY + 2 → due for confirmation
        "hora": "21:00",
        "personas": 4,
        # tags-panel-v2 S2: the trigger tag (default "Reserva confirmada")
        # maps to the seeded `confirmada` estado — pipeline tests send with it.
        "estado": "confirmada",
    }
    payload.update(overrides)
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class ChatwootFake:
    """httpx.MockTransport-backed Chatwoot: canned contacts/inboxes and a
    conversation store keyed by WhatsApp source_id (phone digits without
    '+', the contact_inbox convention L47)."""

    def __init__(self, contact_by_phone=None, conversations=None, contact_conversations=None,
                 inboxes=None, plantillas=None, message_fails=None):
        self.requests = []
        self.contact_by_phone = contact_by_phone or {}
        # WhatsApp conversations by source_id: POST /conversations returns
        # the existing one (lock_to_single_conversation) or creates one.
        self.conversations = conversations or {}
        # what GET /contacts/{id}/conversations would list — the engine
        # must NEVER consult it (S12: it would pick the IG chat)
        self.contact_conversations = contact_conversations or []
        self.inboxes = inboxes or [{"id": 2, "channel_type": "Channel::Whatsapp"}]
        self.plantillas = PLANTILLAS_INBOX if plantillas is None else plantillas
        self.message_fails = message_fails or set()  # conversation ids → 500
        self.next_conversation_id = 500

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.read()))
            path = request.url.path
            method = request.method
            if path.endswith("/contacts/search"):
                phone = request.url.params.get("q", "")
                contact = self.contact_by_phone.get(phone)
                return httpx.Response(200, json={"payload": [contact] if contact else []})
            if path.endswith("/contacts") and method == "POST":
                # creates the contact AND its WhatsApp contact_inbox (L47)
                body = json.loads(request.content)
                contact = {
                    "id": 901,
                    "name": body["name"],
                    "phone_number": body["phone_number"],
                }
                self.contact_by_phone[body["phone_number"]] = contact
                return httpx.Response(200, json={"payload": {"contact": contact}})
            if path.endswith("/conversations") and method == "POST":
                body = json.loads(request.content)
                source_id = body["source_id"]
                conv = self.conversations.get(source_id)
                if conv is None:
                    conv = {"id": self.next_conversation_id, "status": "open"}
                    self.conversations[source_id] = conv
                    self.next_conversation_id += 1
                return httpx.Response(200, json=conv)
            if path.endswith("/conversations") and method == "GET":
                # the contact's conversation list (any channel) — S12: the
                # automated path must never fetch this
                return httpx.Response(200, json={"payload": self.contact_conversations})
            if path.endswith("/toggle_status") and method == "POST":
                conv_id = int(path.split("/conversations/")[1].split("/")[0])
                for conv in self.conversations.values():
                    if conv["id"] == conv_id:
                        conv["status"] = "open"
                # short payload, NOT the full conversation (L47)
                return httpx.Response(200, json={"payload": {
                    "success": True, "conversation_id": conv_id, "current_status": "open",
                }})
            if path.endswith("/inboxes") and "/accounts/" in path:
                return httpx.Response(200, json={"payload": self.inboxes})
            if "/message_templates" in path:
                return httpx.Response(200, json={"payload": self.plantillas})
            if path.endswith("/messages") and method == "POST":
                conv_id = int(path.split("/conversations/")[1].split("/")[0])
                if conv_id in self.message_fails:
                    return httpx.Response(500, json={"error": "boom"})
                return httpx.Response(200, json={"id": 999})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        return ChatwootClient(
            base_url="http://chatwoot.test",
            api_token="token-x",
            account_id=1,
            transport=self.transport,
        )


CONTACT = {"id": 77, "phone_number": "+5491111111111", "name": "Juan Pérez"}
CONTACTO2 = {"id": 78, "phone_number": "+5491155555555", "name": "Ana Ruiz"}

# Approved cached templates the fake WhatsApp inbox serves (L42: the send
# resolves the template's language from this list).
PLANTILLAS_INBOX = [
    {
        "name": "reserva_confirmacion",
        "status": "APPROVED",
        "category": "UTILITY",
        "language": "es_AR",
        "components": [{"type": "BODY", "text": "Hola! Te escribimos por tu reserva"}],
    },
    {
        "name": "invitados_confirmacion",
        "status": "APPROVED",
        "category": "UTILITY",
        "language": "es_AR",
        "components": [{"type": "BODY", "text": "Confirmas la cantidad de invitados?"}],
    },
]


def whatsapp_conversation(conv_id=55, status="open"):
    return {"id": conv_id, "status": status, "meta": {"channel": "Channel::Whatsapp"}}


def _posted_bodies(fake, suffix="/messages"):
    return [
        json.loads(body)
        for method, path, body in fake.requests
        if method == "POST" and path.endswith(suffix)
    ]


def _message_paths(fake):
    return [
        path
        for method, path, _ in fake.requests
        if method == "POST" and path.endswith("/messages")
    ]


# --- selection (pure query rules) ---


def test_seleccion_confirmacion_toma_solo_fecha_mas_dos(client):
    crear(client)  # 2026-03-22 = HOY + 2
    crear(client, telefono="+5491122222222", fecha="2026-03-20")  # today
    crear(client, telefono="+5491133333333", fecha="2026-03-23")  # HOY + 3
    session = client.app.state.sessionmaker()
    selected = reservas_para_confirmar(session, HOY)
    assert [r.telefono for r in selected] == ["+5491111111111"]
    session.close()


def test_seleccion_excluye_canceladas_y_sin_recordatorio(client):
    crear(client, estado="cancelada")
    crear(client, telefono="+5491144444444", fecha="2026-03-22", enviar_recordatorio=False)
    session = client.app.state.sessionmaker()
    assert reservas_para_confirmar(session, HOY) == []
    session.close()


def test_seleccion_invitados_toma_solo_fecha_de_hoy(client):
    crear(client, fecha="2026-03-20")  # today → guests reminder
    crear(client, telefono="+5491122222222", fecha="2026-03-22")
    session = client.app.state.sessionmaker()
    selected = reservas_para_recordar_invitados(session, HOY)
    assert [r.telefono for r in selected] == ["+5491111111111"]
    session.close()


# --- sending: always the WhatsApp template on the WA conversation (S12) ---


def test_envia_plantilla_a_la_conversacion_de_whatsapp(client):
    """Existing contact + open WA conversation: the selected template is
    sent verbatim (L39/L42/L45) and no contact is created."""
    crear(client)
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation()},
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    bodies = _posted_bodies(fake)
    assert len(bodies) == 1
    template_params = bodies[0]["template_params"]  # TOP-LEVEL key (L42)
    assert template_params["name"] == "reserva_confirmacion"
    assert template_params["category"] == "UTILITY"
    assert template_params["language"] == "es_AR"  # Meta rejects a missing language (L42)
    assert template_params["processed_params"] == {}  # fixed message (L39)
    assert "template_params" not in (bodies[0].get("content_attributes") or {})
    # L45: the Chatwoot bubble shows the template's own body — the exact
    # message the client receives — not a personalized summary.
    assert bodies[0]["content"] == "Hola! Te escribimos por tu reserva"
    assert "Juan Pérez" not in bodies[0]["content"]
    assert report["enviados"] == [1]
    # the contact already existed: nothing was created (L47)
    assert _posted_bodies(fake, "/contacts") == []


def test_conversacion_resuelta_se_reabre_antes_de_enviar(client):
    """L47: a resolved WA conversation is reopened with toggle_status
    {status: "open"} exactly once, and the template goes to the SAME
    conversation id (no refetch — the toggle response is not the
    conversation)."""
    crear(client)
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation(status="resolved")},
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    toggles = _posted_bodies(fake, "/toggle_status")
    assert toggles == [{"status": "open"}]
    # the template landed on the reopened conversation (55), not another
    assert _message_paths(fake) == ["/api/v1/accounts/1/conversations/55/messages"]
    assert _posted_bodies(fake)[0]["template_params"]["name"] == "reserva_confirmacion"
    assert report["enviados"] == [1]


def test_contacto_inexistente_se_crea_y_recibe_plantilla(client):
    """L47: no contact → POST /contacts {name, phone_number, inbox_id}
    (which also creates the WhatsApp contact_inbox), then the conversation
    is resolved by source_id = phone digits without '+', then the template."""
    crear(client)
    fake = ChatwootFake()  # no contact for the phone
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    contacts_posts = _posted_bodies(fake, "/contacts")
    assert contacts_posts == [{
        "name": "Juan Pérez",
        "phone_number": "+5491111111111",
        "inbox_id": 2,
    }]
    conversations_posts = _posted_bodies(fake, "/conversations")
    assert len(conversations_posts) == 1
    assert conversations_posts[0]["inbox_id"] == 2
    assert conversations_posts[0]["contact_id"] == 901
    assert conversations_posts[0]["source_id"] == "5491111111111"  # digits, no '+'
    assert len(_posted_bodies(fake)) == 1  # template sent
    assert report["enviados"] == [1]


def test_gestionada_por_instagram_envia_por_whatsapp(client):
    """S12: a reservation managed over IG still gets the WhatsApp template
    on its phone — the engine never consults the contact's conversation
    list (that's what used to pick the IG chat, which Meta rejects outside
    the 24h window)."""
    crear(client)
    ig = {"id": 66, "status": "open", "meta": {"channel": "Channel::Instagram"}}
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation()},
        contact_conversations=[ig],  # the IG chat exists but must be ignored
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    # the contact's conversation list was never fetched
    paths = [path for _, path, _ in fake.requests]
    assert "/api/v1/accounts/1/contacts/77/conversations" not in paths
    # the template went to the WhatsApp conversation (55), not the IG one
    assert _message_paths(fake) == ["/api/v1/accounts/1/conversations/55/messages"]
    bodies = _posted_bodies(fake)
    assert bodies[0]["template_params"]["name"] == "reserva_confirmacion"
    assert bodies[0]["content"] == "Hola! Te escribimos por tu reserva"
    assert report["enviados"] == [1]


def test_sin_inbox_de_whatsapp_registra_falla_sin_enviar(client):
    """The automated send needs a WhatsApp inbox; an account without one
    records the failure and sends nothing."""
    crear(client)
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        inboxes=[
            {"id": 3, "channel_type": "Channel::Instagram"},
            {"id": 4, "channel_type": "Channel::FacebookPage"},
        ],
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert _posted_bodies(fake) == []
    assert report["enviados"] == []
    assert report["fallas"][0]["reserva_id"] == 1
    assert "whatsapp" in report["fallas"][0]["motivo"].lower()


def test_plantilla_no_aprobada_registra_falla_sin_enviar(client):
    """L42: a template missing from the inbox's approved list must not be
    sent (Meta would reject it) — the failure is recorded per reservation
    and the reserva stays unmarked."""
    crear(client)
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation()},
        plantillas=[],  # nothing approved in the inbox
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert _posted_bodies(fake) == []
    assert report["enviados"] == []
    assert report["fallas"][0]["reserva_id"] == 1
    assert "reserva_confirmacion" in report["fallas"][0]["motivo"]
    assert "aprobadas" in report["fallas"][0]["motivo"]
    with client.app.state.sessionmaker() as session:
        assert session.get(Reserva, 1).confirmacion_enviada_at is None


def test_falla_de_envio_no_marca_ni_frena_las_demas(client):
    """One HTTP failure mid-batch: that reserva is not marked, the rest of
    the batch still gets its messages."""
    crear(client, telefono="+5491111111111")
    crear(client, telefono="+5491155555555", nombre="Ana Ruiz", fecha="2026-03-22")
    fake = ChatwootFake(
        contact_by_phone={
            "+5491111111111": CONTACT,
            "+5491155555555": CONTACTO2,
        },
        conversations={
            "5491111111111": whatsapp_conversation(),  # its send 500s
            "5491155555555": whatsapp_conversation(conv_id=56),
        },
        message_fails={55},
    )
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert report["enviados"] == [2]  # the batch continued past the failure
    assert len(report["fallas"]) == 1
    assert report["fallas"][0]["reserva_id"] == 1
    assert "500" in report["fallas"][0]["motivo"]
    with client.app.state.sessionmaker() as session:
        assert session.get(Reserva, 1).confirmacion_enviada_at is None  # not marked
        assert session.get(Reserva, 2).confirmacion_enviada_at is not None


def test_envio_es_idempotente_entre_corridas(client):
    crear(client)
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation()},
    )
    chatwoot = fake.client()
    session = client.app.state.sessionmaker()
    enviar_confirmaciones(
        session=session, chatwoot=chatwoot, hoy=HOY, template_confirmacion="reserva_confirmacion"
    )
    enviar_confirmaciones(
        session=session, chatwoot=chatwoot, hoy=HOY, template_confirmacion="reserva_confirmacion"
    )
    assert len(_posted_bodies(fake)) == 1


def test_recordatorio_de_invitados_envia_plantilla(client):
    crear(client, fecha="2026-03-20")
    fake = ChatwootFake(
        contact_by_phone={"+5491111111111": CONTACT},
        conversations={"5491111111111": whatsapp_conversation()},
    )
    report = enviar_recordatorios_invitados(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_invitados="invitados_confirmacion",
    )
    bodies = _posted_bodies(fake)
    assert len(bodies) == 1
    assert bodies[0]["template_params"]["name"] == "invitados_confirmacion"
    # L45: WhatsApp send displays the template's own body — the exact
    # message the client receives — not a personalized summary.
    assert bodies[0]["content"] == "Confirmas la cantidad de invitados?"
    assert report["enviados"] == [1]


def test_numero_legacy_sin_mas_se_normaliza_a_e164(client):
    """L51: rows saved before the E.164 validation may lack the '+';
    the ensure path prepends it (Chatwoot only accepts +[1-9]...) so the
    contact is created with the same format as every other contact."""
    fake = ChatwootFake(contact_by_phone={})  # legacy row -> no contact yet
    chatwoot = fake.client()
    chatwoot.asegurar_conversacion_whatsapp("5493210987654", "Renzi")
    posts = [
        json.loads(body)
        for method, path, body in fake.requests
        if method == "POST" and path.endswith("/contacts")
    ]
    assert posts and posts[0]["phone_number"] == "+5493210987654"


# --- T6 (tags-estados-sync): el chat creado/resuelto por la automatización
# --- recibe las etiquetas del contacto (S6/D2) ---

from app.config import Settings as AppSettings
from app.main import create_app
from tests.conftest import _make_settings


def _chatwoot_settings(tmp_path) -> AppSettings:
    return AppSettings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        chatwoot_base_url="http://chatwoot.test",
        chatwoot_api_token="token-x",
        chatwoot_account_id=1,
        _env_file=None,
    )


def _seed_reserva_confirmacion(session):
    # tags-panel-v2 S2: the trigger tag maps to `confirmada`; these tests
    # boot without lifespan, so the estado row and the single config row
    # (which carries tag_disparadora) are seeded here.
    session.add(Configuracion(id=1))
    session.add(EstadoReserva(nombre="Confirmada", slug="confirmada",
                              etiqueta_chatwoot="Reserva confirmada"))
    session.add(Reserva(
        nombre="Juan Pérez", telefono="+5491111111111",
        fecha=date(2026, 3, 22), hora=datetime.strptime("21:00", "%H:%M").time(),
        personas=4,
        estado="confirmada",
    ))
    session.commit()


def test_envio_automatizado_sincroniza_etiquetas_del_contacto(tmp_path, monkeypatch):
    """S6/D2: la conversación que asegurar resuelve (nueva o reabierta) se
    sincroniza con las etiquetas del contacto — la disonancia original del
    feature: el chat de WSP nuevo quedaba sin la tag reserva-confirmada."""
    fake = ChatwootFake(conversations={"5491111111111": whatsapp_conversation(4242)})
    llamadas = []

    def spy(settings_arg, conv_id, contacto_id=None, telefono=None, transport=None):
        llamadas.append((conv_id, telefono))
        return True

    monkeypatch.setattr("app.mensajeria.sincronizar_conversacion_nueva", spy)
    app = create_app(_make_settings(tmp_path))
    from app.db import Base as _Base
    from app.db import Base
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as session:
        _seed_reserva_confirmacion(session)
        report = enviar_confirmaciones(
            session, fake.client(), HOY, "reserva_confirmacion",
            settings=_chatwoot_settings(tmp_path),
        )
    assert report["enviados"] == [1]
    assert llamadas == [(4242, "+5491111111111")]


def test_sin_settings_no_sincroniza(tmp_path, monkeypatch):
    """Compat: llamadas sin `settings` no sincronizan (camino heredado)."""
    fake = ChatwootFake(conversations={"5491111111111": whatsapp_conversation(4242)})
    llamadas = []
    monkeypatch.setattr(
        "app.mensajeria.sincronizar_conversacion_nueva",
        lambda *a, **k: llamadas.append(k) or True,
    )
    app = create_app(_make_settings(tmp_path))
    from app.db import Base
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as session:
        _seed_reserva_confirmacion(session)
        report = enviar_confirmaciones(session, fake.client(), HOY, "reserva_confirmacion")
    assert report["enviados"] == [1]
    assert llamadas == []

"""T5 messaging tests (S3/S8) — channel-aware reminder engine.

The ChatwootClient is exercised against a fake HTTP transport
(httpx.MockTransport), so the real template-vs-freetext decision logic runs;
only Chatwoot itself is faked. Dates are fixed (no clock dependence).
"""

import json
from datetime import UTC, date, datetime, timedelta

import httpx

from app.chatwoot import ChatwootClient
from app.mensajeria import (
    enviar_confirmaciones,
    enviar_recordatorios_invitados,
    reservas_para_confirmar,
    reservas_para_recordar_invitados,
)

HOY = date(2026, 3, 20)  # Friday


def crear(client, **overrides):
    payload = {
        "nombre": "Juan Pérez",
        "telefono": "+5491111111111",
        "fecha": "2026-03-22",  # HOY + 2 → due for confirmation
        "hora": "21:00",
        "personas": 4,
    }
    payload.update(overrides)
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class ChatwootFake:
    """httpx.MockTransport-backed Chatwoot: canned contacts/conversations."""

    def __init__(self, contact_by_phone=None, conversations=None, post_fails=False):
        self.requests = []
        self.contact_by_phone = contact_by_phone or {}
        self.conversations = conversations or []
        self.post_fails = post_fails

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.read()))
            path = request.url.path
            if path.endswith("/contacts/search"):
                phone = request.url.params.get("q", "")
                contact = self.contact_by_phone.get(phone)
                if contact is None:
                    return httpx.Response(200, json={"payload": []})
                return httpx.Response(200, json={"payload": [contact]})
            if "/conversations" in path and request.method == "GET":
                return httpx.Response(200, json={"payload": self.conversations})
            if path.endswith("/messages") and request.method == "POST":
                if self.post_fails:
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


def whatsapp_conversation(last_activity_hours_ago):
    ts = datetime.now(UTC) - timedelta(hours=last_activity_hours_ago)
    return {
        "id": 55,
        "status": "open",
        "meta": {"channel": "Channel::Whatsapp", "last_non_activity_message_at": int(ts.timestamp())},
    }


def _posted_bodies(fake):
    return [
        json.loads(body)
        for method, path, body in fake.requests
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


# --- sending: channel-aware decisions ---


def test_whatsapp_fuera_de_ventana_envia_plantilla(client):
    crear(client)
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[whatsapp_conversation(72)])
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    bodies = _posted_bodies(fake)
    assert len(bodies) == 1
    template_params = bodies[0]["content_attributes"]["template_params"]
    assert template_params["name"] == "reserva_confirmacion"
    assert template_params["category"] == "UTILITY"
    assert report["enviados"] == [1]


def test_whatsapp_dentro_de_ventana_envia_texto_libre(client):
    crear(client)
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[whatsapp_conversation(1)])
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    bodies = _posted_bodies(fake)
    assert len(bodies) == 1
    assert "template_params" not in bodies[0]["content_attributes"]
    assert "Juan Pérez" in bodies[0]["content"]
    assert report["enviados"] == [1]


def test_instagram_dentro_de_ventana_envia_texto_libre(client):
    crear(client)
    ig = {
        "id": 66,
        "status": "open",
        "meta": {"channel": "Channel::Instagram", "last_non_activity_message_at": int(
            (datetime.now(UTC) - timedelta(hours=2)).timestamp())},
    }
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[ig])
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert _posted_bodies(fake) and "template_params" not in _posted_bodies(fake)[0]["content_attributes"]
    assert report["enviados"] == [1]


def test_instagram_con_ventana_vencida_no_envia_nada(client):
    crear(client)
    ig = {
        "id": 66,
        "status": "open",
        "meta": {"channel": "Channel::Instagram", "last_non_activity_message_at": int(
            (datetime.now(UTC) - timedelta(hours=30)).timestamp())},
    }
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[ig])
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert _posted_bodies(fake) == []
    assert report["enviados"] == []
    assert report["fallas"][0]["reserva_id"] == 1
    assert "ventana" in report["fallas"][0]["motivo"].lower()


def test_contacto_inexistente_registra_falla(client):
    crear(client)
    fake = ChatwootFake(contact_by_phone={}, conversations=[])
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    assert _posted_bodies(fake) == []
    assert report["fallas"][0]["reserva_id"] == 1


def test_falla_de_envio_no_marca_ni_frena_las_demas(client):
    crear(client, telefono="+5491111111111")
    crear(client, telefono="+5491155555555", nombre="Ana Ruiz", fecha="2026-03-22")
    contactos = {
        "+5491111111111": CONTACT,
        "+5491155555555": {"id": 78, "phone_number": "+5491155555555", "name": "Ana Ruiz"},
    }
    fake = ChatwootFake(contact_by_phone=contactos, conversations=[], post_fails=True)
    report = enviar_confirmaciones(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_confirmacion="reserva_confirmacion",
    )
    # both fail (post_fails), neither marked sent, engine did not crash
    assert sorted(f["reserva_id"] for f in report["fallas"]) == [1, 2]
    assert report["enviados"] == []


def test_envio_es_idempotente_entre_corridas(client):
    crear(client)
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[whatsapp_conversation(1)])
    chatwoot = fake.client()
    session = client.app.state.sessionmaker()
    enviar_confirmaciones(
        session=session, chatwoot=chatwoot, hoy=HOY, template_confirmacion="reserva_confirmacion"
    )
    enviar_confirmaciones(
        session=session, chatwoot=chatwoot, hoy=HOY, template_confirmacion="reserva_confirmacion"
    )
    assert len(_posted_bodies(fake)) == 1


def test_recordatorio_de_invitados_envia_por_canal_de_hoy(client):
    crear(client, fecha="2026-03-20")
    fake = ChatwootFake(contact_by_phone={"+5491111111111": CONTACT}, conversations=[whatsapp_conversation(1)])
    report = enviar_recordatorios_invitados(
        session=client.app.state.sessionmaker(),
        chatwoot=fake.client(),
        hoy=HOY,
        template_invitados="invitados_confirmacion",
    )
    bodies = _posted_bodies(fake)
    assert len(bodies) == 1
    assert "HOY" in bodies[0]["content"]
    assert report["enviados"] == [1]

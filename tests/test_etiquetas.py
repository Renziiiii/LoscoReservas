"""T14 tests (S10/L21): reservas confirmadas get the "Reserva confirmada"
label on their Chatwoot contact. Fail-soft: without a Chatwoot token (L6) or
on any API error, saving the reservation must still succeed."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.etiquetas import ETIQUETA_CONFIRMADA, etiquetar_confirmada
from app.main import create_app
from app.models import Reserva

TEST_USERS = "valen:testpass123"
SECRET = "test-secret-0123456789abcdef0123456789abcdef"


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        database_path=tmp_path / "reservas.db",
        users=TEST_USERS,
        jwt_secret=SECRET,
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class ChatwootLabelsFake:
    """MockTransport-backed Chatwoot with canned contact + contact labels.

    The real API OVERWRITES the whole label list on POST, so the client must
    merge: the fake asserts the merge by returning the previously set list.
    """

    def __init__(self, contact=None, labels=None, post_fails=False):
        self.requests = []
        self.contact = contact
        self.labels = labels if labels is not None else ["vip"]
        self.post_fails = post_fails

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.read()))
            path = request.url.path
            if path.endswith("/contacts/search"):
                if self.contact is None:
                    return httpx.Response(200, json={"payload": []})
                return httpx.Response(200, json={"payload": [self.contact]})
            if "/labels" in path and request.method == "GET":
                return httpx.Response(200, json={"payload": self.labels})
            if "/labels" in path and request.method == "POST":
                if self.post_fails:
                    return httpx.Response(500, json={})
                import json as _json

                self.labels = _json.loads(request.content)["labels"]
                return httpx.Response(200, json={"payload": self.labels})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        from app.chatwoot import ChatwootClient

        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


CONTACT = {"id": 77, "phone_number": "+5491111111111", "name": "Juan Pérez"}


def _reserva(estado="confirmada"):
    return Reserva(
        nombre="Juan Pérez",
        telefono="+5491111111111",
        fecha="2026-12-24",
        hora="21:00",
        personas=4,
        estado=estado,
    )


def _chatwoot_settings(tmp_path):
    return _settings(
        tmp_path,
        chatwoot_base_url="http://chatwoot.test",
        chatwoot_api_token="token-x",
        chatwoot_account_id=1,
    )


# --- unit: the labeling service ---


def test_no_etiqueta_si_estado_no_es_confirmada(tmp_path):
    fake = ChatwootLabelsFake(contact=CONTACT)
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(estado="pendiente")) is False
    assert fake.requests == []


def test_no_etiqueta_si_chatwoot_no_configurado(tmp_path):
    """L6: without a token nothing explodes and nothing is sent."""
    assert etiquetar_confirmada(_settings(tmp_path), _reserva()) is False


def test_etiqueta_merges_con_labels_existentes(tmp_path):
    """The API overwrites the list, so existing labels must be preserved."""
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip", "cumple"])
    ok = etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), transport=fake.transport)
    assert ok is True
    posts = [r for r in fake.requests if r[0] == "POST"]
    assert len(posts) == 1
    import json

    body = json.loads(posts[0][2])
    assert body["labels"] == ["vip", "cumple", ETIQUETA_CONFIRMADA]


def test_etiqueta_no_re_postea_si_ya_la_tiene(tmp_path):
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip", ETIQUETA_CONFIRMADA])
    ok = etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), transport=fake.transport)
    assert ok is True
    assert not [r for r in fake.requests if r[0] == "POST"]


def test_etiqueta_sin_contacto_en_chatwoot_es_fail_soft(tmp_path):
    fake = ChatwootLabelsFake(contact=None)
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), transport=fake.transport) is False
    assert not [r for r in fake.requests if r[0] == "POST"]


def test_etiqueta_error_http_es_fail_soft(tmp_path):
    fake = ChatwootLabelsFake(contact=CONTACT, post_fails=True)
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), transport=fake.transport) is False


# --- integration: every write path triggers it ---

MONKEYPATCH_TARGETS = {
    "dashboard": "app.dash",
    "iframe": "app.iframe",
    "api": "app.reservas",
}


@pytest.fixture()
def etiqueta_spy(monkeypatch):
    """Replaces the labeling service with a spy recording calls per module."""
    calls = []

    def spy(settings, reserva):
        calls.append((reserva.telefono, reserva.estado))
        return True

    for target in ("app.dash", "app.iframe", "app.reservas"):
        monkeypatch.setattr(f"{target}.etiquetar_confirmada", spy)
    return calls


def _login(client):
    response = client.post(
        "/api/auth/login", data={"username": "valen", "password": TEST_USERS.split(":")[1]}
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def _login_page(client):
    response = client.post(
        "/login", data={"username": "valen", "password": TEST_USERS.split(":")[1]},
        follow_redirects=False,
    )
    assert response.status_code == 303


BASE_PAYLOAD = {
    "nombre": "Juan Pérez",
    "telefono": "+5491111111111",
    "fecha": "2026-12-24",
    "hora": "21:00",
    "personas": 4,
    "estado": "pendiente",
}


def test_dashboard_confirmar_reserva_etiqueta(tmp_path, etiqueta_spy):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _login_page(client)
        created = client.post("/reservas", data=BASE_PAYLOAD, follow_redirects=False)
        assert created.status_code == 303
        # the hook fires on every save; the estado filter lives inside the
        # service (pinned by the unit tests above)
        assert etiqueta_spy == [("+5491111111111", "pendiente")]

        response = client.post(
            "/reservas/1", data=dict(BASE_PAYLOAD, estado="confirmada"), follow_redirects=False
        )
        assert response.status_code == 303
        assert etiqueta_spy[-1] == ("+5491111111111", "confirmada")


def test_iframe_confirmar_reserva_etiqueta(tmp_path, etiqueta_spy):
    app = create_app(_settings(tmp_path, iframe_key="embedkey"))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json",
            params={"key": "embedkey"},
            json=dict(BASE_PAYLOAD, estado="confirmada"),
        )
        assert created.status_code == 201, created.text
        assert etiqueta_spy == [("+5491111111111", "confirmada")]


def test_api_confirmar_reserva_etiqueta(tmp_path, etiqueta_spy):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = client.post("/api/reservas", json=dict(BASE_PAYLOAD, estado="confirmada"))
        assert created.status_code == 201, created.text
        assert etiqueta_spy == [("+5491111111111", "confirmada")]

        etiqueta_spy.clear()
        patched = client.patch("/api/reservas/1", json={"estado": "pendiente"})
        assert patched.status_code == 200
        assert etiqueta_spy[-1] == ("+5491111111111", "pendiente")  # service filters

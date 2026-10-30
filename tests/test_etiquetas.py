"""T14 tests (S10/L21): reservas confirmadas get the configured
confirmed-reservation label on their Chatwoot contact. Fail-soft: without a
Chatwoot token (L6) or on any API error, saving the reservation must still
succeed."""

import httpx
import json
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.ajustes import obtener_configuracion
from app.etiquetas import etiquetar_confirmada
from app.main import create_app
from app.models import Configuracion, Reserva

TEST_USERS = "valen:testpass123"
SECRET = "test-secret-0123456789abcdef0123456789abcdef"
DEFAULT_ETIQUETA = "Reserva confirmada"


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

    def __init__(
        self,
        contact=None,
        labels=None,
        conversation=None,
        conversation_labels=None,
        post_fails=False,
        post_conversation_fails=False,
    ):
        self.requests = []
        self.contact = contact
        self.labels = labels if labels is not None else ["vip"]
        self.conversation = conversation
        self.conversation_labels = conversation_labels if conversation_labels is not None else []
        self.post_fails = post_fails
        self.post_conversation_fails = post_conversation_fails

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.read()))
            path = request.url.path
            if path.endswith("/contacts/search"):
                if self.contact is None:
                    return httpx.Response(200, json={"payload": []})
                return httpx.Response(200, json={"payload": [self.contact]})
            if "/contacts/" in path and "/conversations" in path and request.method == "GET":
                payload = [self.conversation] if self.conversation is not None else []
                return httpx.Response(200, json={"payload": payload})
            if "/contacts/" in path and "/labels" in path:
                if request.method == "GET":
                    return httpx.Response(200, json={"payload": self.labels})
                if request.method == "POST":
                    if self.post_fails:
                        return httpx.Response(500, json={})
                    self.labels = json.loads(request.content)["labels"]
                    return httpx.Response(200, json={"payload": self.labels})
            if "/conversations/" in path and "/labels" in path:
                if request.method == "GET":
                    return httpx.Response(200, json={"payload": self.conversation_labels})
                if request.method == "POST":
                    if self.post_conversation_fails:
                        return httpx.Response(500, json={})
                    self.conversation_labels = json.loads(request.content)["labels"]
                    return httpx.Response(200, json={"payload": self.conversation_labels})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        from app.chatwoot import ChatwootClient

        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


CONTACT = {"id": 77, "phone_number": "+5491111111111", "name": "Juan Pérez"}
CONVERSATION = {
    "id": 88,
    "status": "open",
    "last_non_activity_message_at": 1700000000,
}


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
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(estado="pendiente"), DEFAULT_ETIQUETA) is False
    assert fake.requests == []


def test_no_etiqueta_si_chatwoot_no_configurado(tmp_path):
    """L6: without a token nothing explodes and nothing is sent."""
    assert etiquetar_confirmada(_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA) is False


def test_etiqueta_merges_con_labels_existentes(tmp_path):
    """The API overwrites the list, so existing labels must be preserved."""
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip", "cumple"])
    ok = etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport)
    assert ok is True
    posts = [r for r in fake.requests if r[0] == "POST"]
    assert len(posts) == 1
    import json

    body = json.loads(posts[0][2])
    assert body["labels"] == ["vip", "cumple", DEFAULT_ETIQUETA]


def test_etiqueta_personalizada_merges_con_labels_existentes(tmp_path):
    """L24: the label name comes from configuration, not a hard-coded constant."""
    etiqueta = "Mesa VIP"
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip"])
    ok = etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), etiqueta, transport=fake.transport)
    assert ok is True
    posts = [r for r in fake.requests if r[0] == "POST"]
    assert len(posts) == 1
    import json

    assert etiqueta in json.loads(posts[0][2])["labels"]


def test_etiqueta_no_re_postea_si_ya_la_tiene(tmp_path):
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip", DEFAULT_ETIQUETA])
    ok = etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport)
    assert ok is True
    assert not [r for r in fake.requests if r[0] == "POST"]


def test_etiqueta_sin_contacto_en_chatwoot_es_fail_soft(tmp_path):
    fake = ChatwootLabelsFake(contact=None)
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport) is False
    assert not [r for r in fake.requests if r[0] == "POST"]


def test_etiqueta_error_http_es_fail_soft(tmp_path):
    fake = ChatwootLabelsFake(contact=CONTACT, post_fails=True)
    assert etiquetar_confirmada(_chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport) is False


def test_etiqueta_tambien_aplica_a_conversacion_abierta(tmp_path):
    """L27: the confirmed label must also reach the open conversation."""
    fake = ChatwootLabelsFake(
        contact=CONTACT,
        labels=["vip"],
        conversation=CONVERSATION,
        conversation_labels=["pendiente"],
    )
    ok = etiquetar_confirmada(
        _chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport
    )
    assert ok is True
    conv_posts = [r for r in fake.requests if r[0] == "POST" and "/conversations/" in r[1]]
    assert len(conv_posts) == 1
    assert DEFAULT_ETIQUETA in json.loads(conv_posts[0][2])["labels"]


def test_etiqueta_conversacion_idempotente(tmp_path):
    """If the conversation already has the label, do not POST again."""
    fake = ChatwootLabelsFake(
        contact=CONTACT,
        labels=["vip", DEFAULT_ETIQUETA],
        conversation=CONVERSATION,
        conversation_labels=[DEFAULT_ETIQUETA],
    )
    ok = etiquetar_confirmada(
        _chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport
    )
    assert ok is True
    assert not [r for r in fake.requests if r[0] == "POST" and "/conversations/" in r[1]]


def test_etiqueta_sin_conversacion_abierta_pero_contacto_ok(tmp_path):
    """No open conversation is a soft skip; the contact label still counts."""
    fake = ChatwootLabelsFake(contact=CONTACT, labels=["vip"], conversation=None)
    ok = etiquetar_confirmada(
        _chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport
    )
    assert ok is True
    contact_posts = [r for r in fake.requests if r[0] == "POST" and "/contacts/" in r[1]]
    assert len(contact_posts) == 1


def test_etiqueta_conversacion_error_http_es_fail_soft(tmp_path):
    """A conversation-label failure must not undo the contact label."""
    fake = ChatwootLabelsFake(
        contact=CONTACT,
        labels=["vip"],
        conversation=CONVERSATION,
        conversation_labels=["pendiente"],
        post_conversation_fails=True,
    )
    ok = etiquetar_confirmada(
        _chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport
    )
    assert ok is True


def test_etiqueta_retorna_true_si_solo_conversacion_succeeds(tmp_path):
    """Conversation labeling can succeed even when contact labeling fails."""
    fake = ChatwootLabelsFake(
        contact=CONTACT,
        labels=["vieja"],
        conversation=CONVERSATION,
        conversation_labels=["pendiente"],
        post_fails=True,
    )
    ok = etiquetar_confirmada(
        _chatwoot_settings(tmp_path), _reserva(), DEFAULT_ETIQUETA, transport=fake.transport
    )
    assert ok is True
    conv_posts = [r for r in fake.requests if r[0] == "POST" and "/conversations/" in r[1]]
    assert len(conv_posts) == 1


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

    def spy(settings, reserva, etiqueta):
        calls.append((reserva.telefono, reserva.estado, etiqueta))
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
        assert etiqueta_spy == [("+5491111111111", "pendiente", DEFAULT_ETIQUETA)]

        response = client.post(
            "/reservas/1", data=dict(BASE_PAYLOAD, estado="confirmada"), follow_redirects=False
        )
        assert response.status_code == 303
        assert etiqueta_spy[-1] == ("+5491111111111", "confirmada", DEFAULT_ETIQUETA)


def test_iframe_confirmar_reserva_etiqueta(tmp_path, etiqueta_spy):
    app = create_app(_settings(tmp_path, iframe_key="embedkey"))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json",
            params={"key": "embedkey"},
            json=dict(BASE_PAYLOAD, estado="confirmada"),
        )
        assert created.status_code == 201, created.text
        assert etiqueta_spy == [("+5491111111111", "confirmada", DEFAULT_ETIQUETA)]


def test_api_confirmar_reserva_etiqueta(tmp_path, etiqueta_spy):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = client.post("/api/reservas", json=dict(BASE_PAYLOAD, estado="confirmada"))
        assert created.status_code == 201, created.text
        assert etiqueta_spy == [("+5491111111111", "confirmada", DEFAULT_ETIQUETA)]

        etiqueta_spy.clear()
        patched = client.patch("/api/reservas/1", json={"estado": "pendiente"})
        assert patched.status_code == 200
        assert etiqueta_spy[-1] == ("+5491111111111", "pendiente", DEFAULT_ETIQUETA)  # service filters


def test_api_usa_etiqueta_configurada_en_db(tmp_path, etiqueta_spy):
    """L24: write paths must read the label from DB configuration."""
    etiqueta_custom = "Confirmada Losco"
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        with client.app.state.sessionmaker() as session:
            config = obtener_configuracion(session, app.state.settings)
            config.etiqueta_confirmada = etiqueta_custom
            session.commit()
        _login(client)
        created = client.post("/api/reservas", json=dict(BASE_PAYLOAD, estado="confirmada"))
        assert created.status_code == 201, created.text
        assert etiqueta_spy == [("+5491111111111", "confirmada", etiqueta_custom)]


# --- T16: conversation labels API (S10/L27) ---


class ChatwootConversacionesFake:
    """MockTransport-backed Chatwoot for conversation label endpoints."""

    def __init__(self, conversaciones=None):
        self.requests = []
        self.conversaciones = conversaciones or []
        self.labels_por_conversacion: dict[int, list[str]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.url.query.decode()))
            path = request.url.path
            if path.endswith("/conversations") and request.method == "GET":
                label = request.url.params.get("labels[]")
                page = request.url.params.get("page")
                if page != "1":
                    return httpx.Response(200, json={"payload": []})
                coinciden = [
                    c for c in self.conversaciones if label in (c.get("labels") or [])
                ]
                return httpx.Response(200, json={"payload": coinciden})
            if "/conversations/" in path and "/labels" in path and request.method == "GET":
                conv_id = int(path.split("/conversations/")[1].split("/")[0])
                return httpx.Response(
                    200, json={"payload": self.labels_por_conversacion.get(conv_id, [])}
                )
            if "/conversations/" in path and "/labels" in path and request.method == "POST":
                conv_id = int(path.split("/conversations/")[1].split("/")[0])
                labels = json.loads(request.content)["labels"]
                self.labels_por_conversacion[conv_id] = labels
                return httpx.Response(200, json={"payload": labels})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        from app.chatwoot import ChatwootClient

        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


def test_listar_labels_conversacion():
    fake = ChatwootConversacionesFake()
    fake.labels_por_conversacion[5] = ["vip", DEFAULT_ETIQUETA]
    assert fake.client().listar_labels_conversacion(5) == ["vip", DEFAULT_ETIQUETA]


def test_agregar_labels_conversacion_reemplaza_toda_la_lista():
    fake = ChatwootConversacionesFake()
    fake.labels_por_conversacion[5] = ["vip"]
    result = fake.client().agregar_labels_conversacion(5, ["vip", DEFAULT_ETIQUETA])
    assert result == ["vip", DEFAULT_ETIQUETA]
    assert fake.labels_por_conversacion[5] == ["vip", DEFAULT_ETIQUETA]


def test_conversaciones_con_etiqueta_verifica_lado_cliente():
    """The labels[] filter shape is unverified live: drop API noise."""
    fake = ChatwootConversacionesFake(
        conversaciones=[
            {"id": 1, "labels": [DEFAULT_ETIQUETA]},
            {"id": 2, "labels": ["vip"]},  # API noise: no tiene la etiqueta
        ]
    )
    convs = fake.client().conversaciones_con_etiqueta(DEFAULT_ETIQUETA)
    assert [c["id"] for c in convs] == [1]


def test_conversaciones_con_etiqueta_para_en_payload_vacio():
    """Pagination loops while a page is non-empty (safety cap)."""
    fake = ChatwootConversacionesFake(conversaciones=[{"id": 10, "labels": [DEFAULT_ETIQUETA]}])
    convs = fake.client().conversaciones_con_etiqueta(DEFAULT_ETIQUETA)
    assert [c["id"] for c in convs] == [10]
    pages = {query for (method, path, query) in fake.requests if path.endswith("/conversations")}
    assert any("page=1" in query for query in pages)

"""Tests del motor de sincronización de etiquetas (T2, S5/D1/D2,
odd/tasks/tags-estados-sync.md) + API de labels de conversación (T16) +
integración de los caminos de escritura.

Contrato nuevo (reemplaza a etiquetar_confirmada):
- `sincronizar_estado_reserva`: al cambiar el estado de una reserva, la
  etiqueta del estado ANTERIOR se RETIRA (D1) y la del estado NUEVO se
  aplica, en el CONTACTO y en TODAS sus conversaciones existentes (no solo
  la abierta más reciente: incluye resueltas y otros canales, S5).
- `sincronizar_conversacion_nueva`: una conversación recién creada recibe
  TODAS las etiquetas actuales del contacto (D2/S6), mapeadas o añadidas
  por agentes.

Fail-soft (L6): sin Chatwoot configurado, sin contacto, o ante cualquier
error de API, nada explota y el guardado de la reserva sigue.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.etiquetas import (
    sincronizar_conversacion_nueva,
    sincronizar_estado_reserva,
)
from app.main import create_app

TEST_USERS = "valen:testpass123"
SECRET = "test-secret-0123456789abcdef0123456789abcdef"
ETIQUETA_CONFIRMA = "reserva-confirmada"
ETIQUETA_CANCELA = "reserva-cancelada"

CONTACT = {"id": 77, "phone_number": "+5491111111111", "name": "Juan Pérez"}


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        database_path=tmp_path / "reservas.db",
        users=TEST_USERS,
        jwt_secret=SECRET,
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _chatwoot_settings(tmp_path) -> Settings:
    return _settings(
        tmp_path,
        chatwoot_base_url="http://chatwoot.test",
        chatwoot_api_token="token-x",
        chatwoot_account_id=1,
    )


class ChatwootSyncFake:
    """MockTransport-backed Chatwoot: contacto + labels de contacto +
    N conversaciones (cualquier estado) con sus propias labels. POST
   Reemplaza la lista completa (semántica real de Chatwoot) y la guarda."""

    def __init__(self, contact=CONTACT, contact_labels=None, conversations=None,
                 contact_post_fails=False, conversation_post_fail_id=None):
        self.requests = []
        self.contact = contact
        self.contact_labels = list(contact_labels or [])
        self.conversations = [dict(c) for c in (conversations or [])]
        self.conv_labels = {
            c["id"]: list(c.get("labels") or []) for c in self.conversations
        }
        self.contact_post_fails = contact_post_fails
        self.conversation_post_fail_id = conversation_post_fail_id

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith("/contacts/search"):
                payload = [self.contact] if self.contact else []
                return httpx.Response(200, json={"payload": payload})
            if "/contacts/" in path and "/conversations" not in path and "/labels" not in path and request.method == "GET":
                # show endpoint: the contact DIRECTLY in payload (or empty when missing)
                return httpx.Response(200, json={"payload": self.contact or {}})
            if "/contacts/" in path and "/conversations" in path and request.method == "GET":
                return httpx.Response(200, json={"payload": self.conversations})
            if "/contacts/" in path and path.endswith("/labels"):
                if request.method == "GET":
                    return httpx.Response(200, json={"payload": self.contact_labels})
                if self.contact_post_fails:
                    return httpx.Response(500, json={})
                self.contact_labels = json.loads(request.content)["labels"]
                return httpx.Response(200, json={"payload": self.contact_labels})
            if "/conversations/" in path and path.endswith("/labels"):
                conv_id = int(path.split("/conversations/")[1].split("/")[0])
                if request.method == "GET":
                    return httpx.Response(
                        200, json={"payload": self.conv_labels.get(conv_id, [])}
                    )
                if conv_id == self.conversation_post_fail_id:
                    return httpx.Response(500, json={})
                self.conv_labels[conv_id] = json.loads(request.content)["labels"]
                return httpx.Response(200, json={"payload": self.conv_labels[conv_id]})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)


# mapa estado -> etiqueta de Chatwoot (como lo arma mapa_etiquetas(session))
MAPA = {
    "pendiente": None,
    "confirmada": ETIQUETA_CONFIRMA,
    "cancelada": ETIQUETA_CANCELA,
}

CONVS = [
    {"id": 88, "status": "open", "labels": ["vip"]},          # IG abierta
    {"id": 99, "status": "resolved", "labels": []},           # WSP cerrada
    {"id": 101, "status": "pending", "labels": ["vip"]},      # otro canal
]


# --- S5/D1: sincronizar_estado_reserva ---

def test_sync_agrega_al_contacto_y_a_todas_las_conversaciones(tmp_path):
    fake = ChatwootSyncFake(contact_labels=["vip"], conversations=CONVS)
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        MAPA, "pendiente", "confirmada", transport=fake.transport,
    )
    assert ok is True
    assert ETIQUETA_CONFIRMA in fake.contact_labels
    assert all(ETIQUETA_CONFIRMA in fake.conv_labels[cid] for cid in (88, 99, 101))


def test_sync_reemplaza_quita_la_del_estado_anterior(tmp_path):
    """D1: confirmada → cancelada retira la etiqueta vieja y aplica la nueva,
    en el contacto y en TODAS las conversaciones."""
    fake = ChatwootSyncFake(
        contact_labels=["vip", ETIQUETA_CONFIRMA],
        conversations=[dict(c, labels=list(c["labels"]) + ([ETIQUETA_CONFIRMA] if c["id"] != 99 else [])) for c in CONVS],
    )
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        MAPA, "confirmada", "cancelada", transport=fake.transport,
    )
    assert ok is True
    assert ETIQUETA_CONFIRMA not in fake.contact_labels
    assert ETIQUETA_CANCELA in fake.contact_labels
    assert "vip" in fake.contact_labels  # conserva el resto
    for cid in (88, 99, 101):
        assert ETIQUETA_CONFIRMA not in fake.conv_labels[cid]
        assert ETIQUETA_CANCELA in fake.conv_labels[cid]


def test_sync_idempotente_no_repostea(tmp_path):
    """Estado igual y etiquetas ya puestas → cero POST."""
    _labels = ["vip", ETIQUETA_CONFIRMA]
    fake = ChatwootSyncFake(
        contact_labels=_labels,
        conversations=[dict(c, labels=list(_labels)) for c in CONVS],
    )
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        MAPA, "confirmada", "confirmada", transport=fake.transport,
    )
    posts = [r for r in fake.requests if r[0] == "POST"]
    assert posts == []
    assert ok is True


def test_sync_sin_etiquetas_mapeadas_no_llama_la_api(tmp_path):
    """Ninguna de las dos etiquetas existe → ni siquiera consulta Chatwoot."""
    fake = ChatwootSyncFake()
    mapa = {"pendiente": None, "confirmada": None, "cancelada": None}
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        mapa, "pendiente", "confirmada", transport=fake.transport,
    )
    assert fake.requests == []
    assert ok is True


def test_sync_fail_soft_sin_chatwoot(tmp_path):
    """L6: sin credenciales no explota ni llama nada."""
    assert sincronizar_estado_reserva(
        _settings(tmp_path), CONTACT["phone_number"], MAPA,
        "pendiente", "confirmada",
    ) is False


def test_sync_fail_soft_sin_contacto(tmp_path):
    fake = ChatwootSyncFake(contact=None)
    assert sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        MAPA, "pendiente", "confirmada", transport=fake.transport,
    ) is False


def test_sync_falla_de_una_conversacion_no_bloquean_las_demas(tmp_path):
    """Per-item failures are logged and the rest keep synchronizing."""
    fake = ChatwootSyncFake(
        conversations=CONVS, conversation_post_fail_id=88,
    )
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), CONTACT["phone_number"],
        MAPA, "pendiente", "confirmada", transport=fake.transport,
    )
    assert ok is True
    assert ETIQUETA_CONFIRMA not in fake.conv_labels[88]
    assert ETIQUETA_CONFIRMA in fake.conv_labels[99]
    assert ETIQUETA_CONFIRMA in fake.conv_labels[101]
    assert ETIQUETA_CONFIRMA in fake.contact_labels


def test_sync_usa_contacto_id_directo(tmp_path):
    """When the caller already knows the Chatwoot contact id, skip search."""
    fake = ChatwootSyncFake(contact_labels=[], conversations=CONVS)
    ok = sincronizar_estado_reserva(
        _chatwoot_settings(tmp_path), "otro-telefono-distinto",
        MAPA, "pendiente", "confirmada", contacto_id=77, transport=fake.transport,
    )
    assert ok is True
    paths = [r[1] for r in fake.requests]
    assert not any(p.endswith("/contacts/search") for p in paths)


# --- S6/D2: sincronizar_conversacion_nueva ---

def test_conv_nueva_copia_todas_las_etiquetas_del_contacto(tmp_path):
    fake = ChatwootSyncFake(contact_labels=["vip", ETIQUETA_CONFIRMA])
    ok = sincronizar_conversacion_nueva(
        _chatwoot_settings(tmp_path), 99, contacto_id=77, transport=fake.transport,
    )
    assert ok is True
    assert fake.conv_labels[99] == ["vip", ETIQUETA_CONFIRMA]


def test_conv_nueva_idempotente(tmp_path):
    fake = ChatwootSyncFake(
        contact_labels=["vip"],
        conversations=[{"id": 99, "status": "open", "labels": ["vip", "extra"]}],
    )
    ok = sincronizar_conversacion_nueva(
        _chatwoot_settings(tmp_path), 99, contacto_id=77, transport=fake.transport,
    )
    assert ok is False  # nada faltaba; no POST
    assert fake.conv_labels[99] == ["vip", "extra"]


def test_conv_nueva_resuelve_contacto_por_telefono(tmp_path):
    fake = ChatwootSyncFake(contact_labels=["vip"])
    ok = sincronizar_conversacion_nueva(
        _chatwoot_settings(tmp_path), 99,
        telefono=CONTACT["phone_number"], transport=fake.transport,
    )
    assert ok is True
    assert "vip" in fake.conv_labels[99]


def test_conv_nueva_fail_soft(tmp_path):
    assert sincronizar_conversacion_nueva(_settings(tmp_path), 99) is False
    fake = ChatwootSyncFake(contact=None)
    assert sincronizar_conversacion_nueva(
        _chatwoot_settings(tmp_path), 99,
        telefono=CONTACT["phone_number"], transport=fake.transport,
    ) is False


# --- integración: los caminos de escritura capturan estado_anterior ---

MONKEYPATCH_TARGETS = ("app.dash", "app.iframe", "app.reservas")


@pytest.fixture()
def sync_spy(monkeypatch):
    calls = []

    def spy(settings, telefono, mapa, estado_anterior, estado_nuevo, contacto_id=None, transport=None):
        calls.append((telefono, estado_anterior, estado_nuevo))
        return True

    for target in MONKEYPATCH_TARGETS:
        monkeypatch.setattr(f"{target}.sincronizar_estado_reserva", spy)
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


def test_dashboard_crear_y_confirmar_sync(tmp_path, sync_spy):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _login_page(client)
        created = client.post("/reservas", data=BASE_PAYLOAD, follow_redirects=False)
        assert created.status_code == 303
        # alta directa: no hay estado anterior (None) — nada que quitar
        assert sync_spy == [("+5491111111111", None, "pendiente")]
        response = client.post(
            "/reservas/1", data=dict(BASE_PAYLOAD, estado="confirmada"), follow_redirects=False
        )
        assert response.status_code == 303
        assert sync_spy[-1] == ("+5491111111111", "pendiente", "confirmada")


def test_iframe_crear_sync(tmp_path, sync_spy):
    app = create_app(_settings(tmp_path, iframe_key="embedkey"))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json",
            params={"key": "embedkey"},
            json=dict(BASE_PAYLOAD, estado="confirmada"),
        )
        assert created.status_code == 201, created.text
        assert sync_spy == [("+5491111111111", None, "confirmada")]  # alta directa


def test_api_patch_estado_sync(tmp_path, sync_spy):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = client.post("/api/reservas", json=BASE_PAYLOAD)
        assert created.status_code == 201, created.text
        sync_spy.clear()
        patched = client.patch("/api/reservas/1", json={"estado": "confirmada"})
        assert patched.status_code == 200
        assert sync_spy == [("+5491111111111", "pendiente", "confirmada")]
        sync_spy.clear()
        # sin cambio de estado: el sync corre igual (idempotente adentro)
        patched = client.patch("/api/reservas/1", json={"personas": 6})
        assert patched.status_code == 200
        assert sync_spy == [("+5491111111111", "confirmada", "confirmada")]

"""S11 (L46) tests: the Chatwoot contact receives the phone the agent
annotated in the iframe — only when it has none, never overwriting an
existing one, and fail-soft (a Chatwoot failure never breaks the save)."""

import json

import httpx

from app.chatwoot import ChatwootClient
from app.config import Settings
from app.contactos import sincronizar_telefono_contacto

SECRET = "test-secret-0123456789abcdef0123456789abcdef"

CONTACTO_ID = 77


class ChatwootContactosFake:
    """MockTransport-backed Chatwoot for the contact phone sync."""

    def __init__(self, contact=None, update_fails=False):
        self.requests = []
        self.contact = contact
        self.update_fails = update_fails

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path, request.read()))
            path = request.url.path
            if request.method == "GET" and path.endswith(f"/contacts/{CONTACTO_ID}"):
                if self.contact is None:
                    return httpx.Response(404, json={})
                return httpx.Response(200, json={"payload": self.contact})
            if request.method == "PATCH" and path.endswith(f"/contacts/{CONTACTO_ID}"):
                if self.update_fails:
                    return httpx.Response(500, json={})
                self.contact = {**self.contact, **json.loads(request.content)}
                return httpx.Response(200, json={"payload": self.contact})
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


CONTACTO_SIN_TELEFONO = {"id": CONTACTO_ID, "name": "Camila", "phone_number": ""}
CONTACTO_CON_TELEFONO = {
    "id": CONTACTO_ID,
    "name": "Juan",
    "phone_number": "+5491111111111",
}


def _settings(chatwoot=True) -> Settings:
    if chatwoot:
        return Settings(
            users="valen:testpass123",
            jwt_secret=SECRET,
            _env_file=None,
            chatwoot_base_url="http://chatwoot.test",
            chatwoot_api_token="token-x",
            chatwoot_account_id=1,
        )
    return Settings(users="valen:testpass123", jwt_secret=SECRET, _env_file=None)


def _patches(fake):
    return [r for r in fake.requests if r[0] == "PATCH"]


def test_contacto_sin_telefono_recibe_el_anotado():
    """The whole point of S11: the Instagram contact has no phone, the agent
    annotates the one the client gave in the chat, and the contact ends up
    with it — so labels and WhatsApp sends resolve to this same contact."""
    fake = ChatwootContactosFake(contact=CONTACTO_SIN_TELEFONO)
    ok = sincronizar_telefono_contacto(
        _settings(), CONTACTO_ID, "+543513708732", transport=fake.transport
    )
    assert ok is True
    patches = _patches(fake)
    assert len(patches) == 1
    assert json.loads(patches[0][2]) == {"phone_number": "+543513708732"}


def test_contacto_con_telefono_no_se_pisa():
    """Never overwrite: a contact that already has a phone keeps it."""
    fake = ChatwootContactosFake(contact=CONTACTO_CON_TELEFONO)
    ok = sincronizar_telefono_contacto(
        _settings(), CONTACTO_ID, "+543513708732", transport=fake.transport
    )
    assert ok is False
    assert _patches(fake) == []


def test_sin_contacto_id_no_llama_a_chatwoot():
    fake = ChatwootContactosFake(contact=CONTACTO_SIN_TELEFONO)
    ok = sincronizar_telefono_contacto(
        _settings(), None, "+543513708732", transport=fake.transport
    )
    assert ok is False
    assert fake.requests == []


def test_chatwoot_sin_configurar_degrada_sin_llamar():
    fake = ChatwootContactosFake(contact=CONTACTO_SIN_TELEFONO)
    ok = sincronizar_telefono_contacto(
        _settings(chatwoot=False), CONTACTO_ID, "+543513708732",
        transport=fake.transport,
    )
    assert ok is False
    assert fake.requests == []


def test_contacto_inexistente_no_falla():
    fake = ChatwootContactosFake(contact=None)
    ok = sincronizar_telefono_contacto(
        _settings(), CONTACTO_ID, "+543513708732", transport=fake.transport
    )
    assert ok is False
    assert _patches(fake) == []


def test_error_de_chatwoot_es_fail_soft():
    """L6 pattern: the reservation save must never break on a Chatwoot
    failure — the sync only reports False."""
    fake = ChatwootContactosFake(contact=CONTACTO_SIN_TELEFONO, update_fails=True)
    ok = sincronizar_telefono_contacto(
        _settings(), CONTACTO_ID, "+543513708732", transport=fake.transport
    )
    assert ok is False

"""Webhook de Chatwoot (T3, S6/D3, odd/tasks/tags-estados-sync.md).

Contrato:
- POST /api/webhooks/chatwoot?token=... (o header X-Chatwoot-Token);
- token compartido via LOSCO_CHATWOOT_WEBHOOK_TOKEN; sin token configurado
  o token incorrecto → 404 fail-closed (no revela la existencia del endpoint);
- `conversation_created` → sincronizar_conversacion_nueva (D2);
- `contact_updated` → el contacto cambió (agente le sumó etiquetas): se
  propagan sus etiquetas actuales a odas sus conversaciones;
- siempre responde 200 si el token es válido (fail-soft, nunca 500).
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN = "whsec-test-token"
SECRET = "test-secret-0123456789abcdef0123456789abcdef"


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret=SECRET,
        chatwoot_webhook_token=TOKEN,
        chatwoot_base_url="http://chatwoot.test",
        chatwoot_api_token="token-x",
        chatwoot_account_id=1,
        iframe_key="k123",
        _env_file=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _client(tmp_path, **overrides):
    return TestClient(create_app(_settings(tmp_path, **overrides)))


def _post(client, payload, token=TOKEN):
    return client.post("/api/webhooks/chatwoot", params={"token": token}, json=payload)


def test_sin_token_configurado_fail_closed(tmp_path):
    client = _client(tmp_path, chatwoot_webhook_token="")
    assert client.post(
        "/api/webhooks/chatwoot", params={"token": "lo-que-sea"}, json={}
    ).status_code == 404


def test_token_incorrecto_fail_closed(tmp_path):
    client = _client(tmp_path)
    assert _post(client, {}, token="wrong").status_code == 404
    assert _post(client, {}, token="").status_code == 404


def test_token_via_header(tmp_path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/webhooks/chatwoot", json={"event": "conversation_updated"},
        headers={"X-Chatwoot-Token": TOKEN},
    )
    assert resp.status_code == 200


def test_conversation_created_sincroniza_conversacion(tmp_path):
    """S6/D2: conversación creada → labels del contacto a la conversación."""
    client = _client(tmp_path)
    with patch("app.webhooks.sincronizar_conversacion_nueva") as sync:
        sync.return_value = True
        resp = _post(client, {
            "event": "conversation_created",
            "conversation": {"id": 555, "meta": {"sender": {"id": 77}}},
        })
    assert resp.status_code == 200
    assert sync.called
    kwargs = sync.call_args.kwargs
    assert sync.call_args.args[1] == 555  # conversacion_id
    assert kwargs.get("contacto_id") == 77


def test_conversation_created_resuelve_contacto_por_id(tmp_path):
    """Payload sin meta.sender: se pregunta a Chatwoot por la conversación."""
    client = _client(tmp_path)
    with patch("app.webhooks.sincronizar_conversacion_nueva") as sync, \
         patch("app.webhooks.ChatwootClient") as client_cls:
        instance = client_cls.return_value
        instance.obtener_conversacion.return_value = {
            "id": 555, "meta": {"sender": {"id": 90}},
        }
        _post(client, {"event": "conversation_created", "conversation": {"id": 555}})
    assert sync.called
    assert sync.call_args.kwargs.get("contacto_id") == 90


def test_contact_updated_propaga_a_todas_las_conversaciones(tmp_path):
    """Los agentes pueden sumar etiquetas al contacto: se propagan a todos sus chats."""
    client = _client(tmp_path)
    with patch("app.webhooks.sincronizar_contacto_todas_las_conversaciones") as sync:
        resp = _post(client, {"event": "contact_updated", "id": 77})
    assert resp.status_code == 200
    assert sync.called
    assert sync.call_args.args[1] == 77


def test_evento_desconocido_ignora_sin_golpear_chatwoot(tmp_path):
    client = _client(tmp_path)
    with patch("app.webhooks.sincronizar_conversacion_nueva") as sync_conv, \
         patch("app.webhooks.sincronizar_contacto_todas_las_conversaciones") as sync_contact:
        resp = _post(client, {"event": "message_created", "id": 1})
    assert resp.status_code == 200
    assert not sync_conv.called
    assert not sync_contact.called


def test_sync_fail_soft_no_genera_500(tmp_path):
    client = _client(tmp_path)
    with patch("app.webhooks.sincronizar_conversacion_nueva", side_effect=RuntimeError("boom")):
        assert _post(client, {
            "event": "conversation_created",
            "conversation": {"id": 555, "meta": {"sender": {"id": 77}}},
        }).status_code == 200

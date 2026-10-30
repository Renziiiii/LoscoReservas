"""T11 tests (L19): messaging hours and WhatsApp templates are configured in
the admin panel and persisted in the DB — not via Docker env vars.

Covers: seeding from env defaults on first boot, the /ajustes page (auth,
valid save, invalid hour rejected without persisting), and the scheduler
reading/re-programming jobs from the DB configuration."""

import json
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.etiquetas import migrar_etiqueta
from app.main import create_app
from app.models import Base, Configuracion, Reserva
from app.scheduler import build_scheduler, reprogramar

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


def _login_cookie(client: TestClient) -> TestClient:
    """Login as the seeded test user (session cookie, like a browser)."""
    response = client.post(
        "/login", data={"username": "valen", "password": "testpass123"}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


FORM_DATA = {
    "hora_confirmacion": "10:30",
    "hora_invitados": "18:45",
    "plantilla_confirmacion": "conf_losco",
    "plantilla_invitados": "invitados_losco",
    "etiqueta_confirmada": "Reserva confirmada",
}


# --- page auth ---


def test_anonymous_ajustes_page_redirects_to_login(anon_client):
    response = anon_client.get("/ajustes", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- seeding from env defaults (first boot only) ---


def test_config_seeds_from_env_defaults_on_first_boot(tmp_path):
    app = create_app(
        _settings(
            tmp_path,
            hora_confirmacion="08:05",
            hora_invitados="17:55",
            template_confirmacion="sembrada_conf",
            template_invitados="sembrada_inv",
        )
    )
    with TestClient(app) as client:
        _login_cookie(client)
        response = client.get("/ajustes")
        assert response.status_code == 200
        for expected in ("08:05", "17:55", "sembrada_conf", "sembrada_inv"):
            assert expected in response.text


# --- GET /ajustes ---


def test_ajustes_page_renders_seeded_defaults(client):
    response = client.get("/ajustes")
    assert response.status_code == 200
    for expected in ("09:00", "17:15", "reserva_confirmacion", "invitados_confirmacion"):
        assert expected in response.text


# --- POST /ajustes ---


def test_post_ajustes_persists_values(client):
    response = client.post("/ajustes", data=FORM_DATA, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ajustes"

    follow = client.get("/ajustes")
    for expected in FORM_DATA.values():
        assert expected in follow.text

    with client.app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.hora_confirmacion == "10:30"
        assert config.hora_invitados == "18:45"
        assert config.plantilla_confirmacion == "conf_losco"
        assert config.plantilla_invitados == "invitados_losco"


def test_post_ajustes_bad_hour_rejected_without_persisting(client):
    client.get("/ajustes")  # seed row with defaults
    for bad in ("9", "25:00", "09:99", "mañana", ""):
        data = dict(FORM_DATA, hora_confirmacion=bad)
        response = client.post("/ajustes", data=data)
        assert response.status_code == 200, f"hora {bad!r} should re-render the form"
        assert "hora" in response.text.lower()
    with client.app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.hora_confirmacion == "09:00"  # unchanged


def test_post_ajustes_empty_template_rejected_without_persisting(client):
    client.get("/ajustes")
    data = dict(FORM_DATA, plantilla_confirmacion="")
    response = client.post("/ajustes", data=data)
    assert response.status_code == 200
    with client.app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.plantilla_confirmacion == "reserva_confirmacion"


# --- scheduler reads hours/templates from the DB ---


def _seeded_app(tmp_path, config_kwargs=None):
    settings = _settings(tmp_path)
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as session:
        session.add(Configuracion(id=1, **(config_kwargs or {})))
        session.commit()
    return app, settings


def test_build_scheduler_registers_jobs_with_db_hours(tmp_path):
    app, settings = _seeded_app(
        tmp_path,
        {"hora_confirmacion": "11:07", "hora_invitados": "19:44"},
    )
    scheduler = build_scheduler(app, settings)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"confirmaciones_t48h", "recordatorio_invitados"}
    assert str(jobs["confirmaciones_t48h"].trigger) == "cron[hour='11', minute='7']"
    assert str(jobs["recordatorio_invitados"].trigger) == "cron[hour='19', minute='44']"


def test_reprogramar_updates_triggers_after_config_change(tmp_path):
    app, settings = _seeded_app(tmp_path, {"hora_confirmacion": "09:00", "hora_invitados": "17:15"})
    scheduler = build_scheduler(app, settings)
    with app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        config.hora_confirmacion = "12:00"
        config.hora_invitados = "16:30"
        session.commit()

    reprogramar(scheduler, app, settings)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert str(jobs["confirmaciones_t48h"].trigger) == "cron[hour='12', minute='0']"
    assert str(jobs["recordatorio_invitados"].trigger) == "cron[hour='16', minute='30']"


def test_job_uses_db_template_at_run_time(tmp_path, monkeypatch):
    """The confirmation job must send the template configured in the DB, not
    an env var, even if the env default differs."""
    app, settings = _seeded_app(tmp_path, {"plantilla_confirmacion": "plantilla_db"})
    dos_dias = date.today() + timedelta(days=2)
    with app.state.sessionmaker() as session:
        session.add(
            Reserva(
                nombre="Valen",
                telefono="+5491100000000",
                fecha=dos_dias,
                hora=datetime.strptime("21:00", "%H:%M").time(),
                personas=4,
            )
        )
        session.commit()

    captured = {}

    class FakeChatwootClient:
        def __init__(self, base_url, api_token, account_id, transport=None):
            pass

        def conversacion_abierta(self, telefono):
            return {
                "id": 7,
                "meta": {
                    "channel": "Channel::Whatsapp",
                    # far past => 24h window expired => template path
                    "last_non_activity_message_at": 1,
                },
            }

        def ventana_vencida(self, conversacion, ahora=None):
            return True

        def enviar_mensaje(self, conversacion, texto, plantilla=None, plantilla_params=None):
            captured["plantilla"] = plantilla
            return {}

    monkeypatch.setattr("app.scheduler.ChatwootClient", FakeChatwootClient)
    from app.scheduler import _job_confirmaciones

    _job_confirmaciones(app)()
    assert captured["plantilla"] == "plantilla_db"


# --- T13: listar plantillas de WhatsApp desde Chatwoot (L19/L20) ---

import httpx

from app.chatwoot import ChatwootClient


class ChatwootPlantillasFake:
    """MockTransport-backed Chatwoot with canned inboxes + templates."""

    def __init__(self, inboxes=None, plantillas_por_inbox=None):
        self.requests = []
        self.inboxes = inboxes or []
        self.plantillas_por_inbox = plantillas_por_inbox or {}

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith("/inboxes") and "/accounts/" in path:
                return httpx.Response(200, json={"payload": self.inboxes})
            if "/message_templates" in path:
                inbox_id = int(path.split("/")[-2])
                if inbox_id in self.plantillas_por_inbox:
                    return httpx.Response(
                        200, json={"payload": self.plantillas_por_inbox[inbox_id]}
                    )
                return httpx.Response(422, json={})  # not a WhatsApp inbox
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


PLANTILLAS = [
    {"name": "reserva_confirmacion", "status": "approved", "category": "UTILITY", "language": "es"},
    {"name": "invitados_confirmacion", "status": "approved", "category": "UTILITY", "language": "es"},
]


def test_plantillas_whatsapp_prefiere_inbox_whatsapp_nativo():
    fake = ChatwootPlantillasFake(
        inboxes=[
            {"id": 2, "channel_type": "Channel::Twilio"},
            {"id": 5, "channel_type": "Channel::Whatsapp"},
        ],
        plantillas_por_inbox={5: PLANTILLAS, 2: [{"name": "de_twilio"}]},
    )
    plantillas = fake.client().plantillas_whatsapp()
    assert [p["name"] for p in plantillas] == [
        "reserva_confirmacion", "invitados_confirmacion",
    ]
    # only the native WhatsApp inbox was queried (priority short-circuit)
    template_requests = [p for m, p in fake.requests if "message_templates" in p]
    assert template_requests == ["/api/v1/accounts/1/inboxes/5/message_templates"]


def test_plantillas_whatsapp_salta_inboxes_que_no_son_de_whatsapp():
    fake = ChatwootPlantillasFake(
        inboxes=[
            {"id": 1, "channel_type": "Channel::WebWidget"},
            {"id": 2, "channel_type": "Channel::Twilio"},
        ],
        plantillas_por_inbox={2: PLANTILLAS},  # inbox 1 -> 422
    )
    plantillas = fake.client().plantillas_whatsapp()
    assert [p["name"] for p in plantillas] == ["reserva_confirmacion", "invitados_confirmacion"]


def test_plantillas_whatsapp_vacio_si_no_hay_inboxes():
    fake = ChatwootPlantillasFake(inboxes=[])
    assert fake.client().plantillas_whatsapp() == []


def _app_configurado(tmp_path, monkeypatch, plantillas=None, error=None):
    captured = {}

    class FakeChatwootClient:
        def __init__(self, base_url, api_token, account_id):
            captured["creds"] = (base_url, api_token, account_id)

        def plantillas_whatsapp(self):
            if error is not None:
                raise error
            return plantillas if plantillas is not None else PLANTILLAS

    monkeypatch.setattr("app.dash.ChatwootClient", FakeChatwootClient)
    app = create_app(_settings(tmp_path, chatwoot_base_url="http://cw.test",
                               chatwoot_api_token="tok", chatwoot_account_id=3))
    return app, captured


def _cookie_login(client):
    response = client.post(
        "/login", data={"username": "valen", "password": "testpass123"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_plantillas_json_anonimo_redirige_a_login(anon_client):
    response = anon_client.get("/ajustes/plantillas.json", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_plantillas_json_sin_chatwoot_configurado_indica_motivo(client):
    response = client.get("/ajustes/plantillas.json")
    assert response.status_code == 200
    body = response.json()
    assert body["plantillas"] == []
    assert "no está configurado" in body["motivo"]


def test_plantillas_json_devuelve_plantillas_de_chatwoot(tmp_path, monkeypatch):
    app, captured = _app_configurado(tmp_path, monkeypatch)
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.get("/ajustes/plantillas.json")
        assert response.status_code == 200
        body = response.json()
        assert [p["name"] for p in body["plantillas"]] == [
            "reserva_confirmacion", "invitados_confirmacion",
        ]
        assert body["motivo"] is None
        assert captured["creds"] == ("http://cw.test", "tok", 3)


def test_plantillas_json_inbox_sin_plantillas_indica_motivo(tmp_path, monkeypatch):
    app, _ = _app_configurado(tmp_path, monkeypatch, plantillas=[])
    with TestClient(app) as client:
        _cookie_login(client)
        body = client.get("/ajustes/plantillas.json").json()
        assert body["plantillas"] == []
        assert "no tiene plantillas" in body["motivo"]


def test_plantillas_json_error_de_chatwoot_no_rompe_el_panel(tmp_path, monkeypatch):
    app, _ = _app_configurado(tmp_path, monkeypatch, error=RuntimeError("boom"))
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.get("/ajustes/plantillas.json")
        assert response.status_code == 200
        body = response.json()
        assert body["plantillas"] == []
        assert "no respondió" in body["motivo"]


# --- L22: selector real de plantillas + pista accionable para el 401 ---


def test_ajustes_page_contiene_selectores_de_plantillas(client):
    """L22: the datalist was not perceived as a selector — the page must ship
    a real <select> per template field (populated by JS from Chatwoot)."""
    response = client.get("/ajustes")
    assert response.status_code == 200
    for marker in ('id="sel-plantilla-conf"', 'id="sel-plantilla-inv"'):
        assert marker in response.text, marker


def test_plantillas_json_401_da_pista_de_credenciales(tmp_path, monkeypatch):
    """A 401 from Chatwoot must name the likely causes (token / account id),
    not just echo the HTTP error."""
    import httpx as _httpx

    request = _httpx.Request("GET", "http://cw.test/api/v1/accounts/1/inboxes")
    response = _httpx.Response(401, request=request)
    error = _httpx.HTTPStatusError(
        "Client error '401 Unauthorized' for url '...'", request=request, response=response
    )
    app, _ = _app_configurado(tmp_path, monkeypatch, error=error)
    with TestClient(app) as client:
        _cookie_login(client)
        body = client.get("/ajustes/plantillas.json").json()
        assert body["plantillas"] == []
        assert "401" in body["motivo"]
        assert "token" in body["motivo"].lower()
        assert "account id" in body["motivo"].lower()


def test_chatwoot_client_envia_ambos_headers_de_auth():
    """Classic api_access_token + Bearer (v4.19+): eliminates the auth-scheme
    variable when diagnosing a 401."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"payload": []})

    from app.chatwoot import ChatwootClient

    ChatwootClient(
        base_url="http://chatwoot.test", api_token="token-x", account_id=1,
        transport=httpx.MockTransport(handler),
    ).listar_inboxes()
    assert seen["headers"]["api_access_token"] == "token-x"
    assert seen["headers"]["authorization"] == "Bearer token-x"


# --- T15 (parte A): etiqueta de reserva confirmada configurable (L24) ---

from sqlalchemy import text


def test_ajustes_page_contiene_selector_de_etiqueta(client):
    response = client.get("/ajustes")
    assert response.status_code == 200
    for marker in ('id="sel-etiqueta-confirmada"', 'name="etiqueta_confirmada"'):
        assert marker in response.text, marker


def test_ajustes_muestra_etiqueta_por_defecto(client):
    response = client.get("/ajustes")
    assert "Reserva confirmada" in response.text


def test_post_ajustes_guarda_etiqueta(client):
    data = dict(FORM_DATA, etiqueta_confirmada="Mesa ya confirmada")
    response = client.post("/ajustes", data=data, follow_redirects=False)
    assert response.status_code == 303
    with client.app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.etiqueta_confirmada == "Mesa ya confirmada"


def test_post_ajustes_etiqueta_vacia_rechazada(client):
    client.get("/ajustes")
    data = dict(FORM_DATA, etiqueta_confirmada="")
    response = client.post("/ajustes", data=data)
    assert response.status_code == 200
    with client.app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.etiqueta_confirmada == "Reserva confirmada"


def test_columna_etiqueta_migrada_en_db_con_esquema_viejo(tmp_path):
    """Pins the deployed-server scenario: a DB created before T15 has a
    configuracion table WITHOUT the new column; the app must boot and work
    (SQLite create_all does not ALTER existing tables)."""
    db_path = tmp_path / "reservas.db"
    engine = create_app(_settings(tmp_path)).state.engine  # engine factory only
    from app.db import Base
    from app.models import Reserva  # register all models

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE configuracion ("
            "id INTEGER PRIMARY KEY, hora_confirmacion VARCHAR(5), "
            "hora_invitados VARCHAR(5), plantilla_confirmacion VARCHAR(120), "
            "plantilla_invitados VARCHAR(120))"
        ))
        conn.execute(text(
            "INSERT INTO configuracion (id, hora_confirmacion, hora_invitados, "
            "plantilla_confirmacion, plantilla_invitados) "
            "VALUES (1, '09:00', '17:15', 'reserva_confirmacion', 'invitados_confirmacion')"
        ))
    Base.metadata.create_all(engine)  # no-op for existing tables

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.get("/ajustes")
        assert response.status_code == 200
        assert "Reserva confirmada" in response.text  # default after migration


def _fake_dash_chatwoot(monkeypatch, labels=None, error=None):
    captured = {}

    class FakeChatwootClient:
        def __init__(self, base_url, api_token, account_id):
            captured["creds"] = (base_url, api_token, account_id)

        def listar_labels_cuenta(self):
            if error is not None:
                raise error
            return labels if labels is not None else [
                {"id": 1, "title": "Reserva confirmada", "color": "#12a594"},
                {"id": 2, "title": "VIP", "color": "#ffc53d"},
            ]

    monkeypatch.setattr("app.dash.ChatwootClient", FakeChatwootClient)
    return captured


def test_etiquetas_json_anonimo_redirige(anon_client):
    response = anon_client.get("/ajustes/etiquetas.json", follow_redirects=False)
    assert response.status_code == 303


def test_etiquetas_json_sin_chatwoot_configurado(client):
    body = client.get("/ajustes/etiquetas.json").json()
    assert body["etiquetas"] == []
    assert "no está configurado" in body["motivo"]


def test_etiquetas_json_devuelve_labels_de_la_cuenta(tmp_path, monkeypatch):
    captured = _fake_dash_chatwoot(monkeypatch)
    app = create_app(_settings(tmp_path, chatwoot_base_url="http://cw.test",
                               chatwoot_api_token="tok", chatwoot_account_id=3))
    with TestClient(app) as client:
        _cookie_login(client)
        body = client.get("/ajustes/etiquetas.json").json()
        assert [e["title"] for e in body["etiquetas"]] == ["Reserva confirmada", "VIP"]
        assert body["motivo"] is None
        assert captured["creds"] == ("http://cw.test", "tok", 3)


def test_etiquetas_json_sin_labels_indica_motivo(tmp_path, monkeypatch):
    _fake_dash_chatwoot(monkeypatch, labels=[])
    app = create_app(_settings(tmp_path, chatwoot_base_url="http://cw.test",
                               chatwoot_api_token="tok", chatwoot_account_id=3))
    with TestClient(app) as client:
        _cookie_login(client)
        body = client.get("/ajustes/etiquetas.json").json()
        assert body["etiquetas"] == []
        assert "no tiene etiquetas" in body["motivo"]


def test_etiquetas_json_401_da_pista(tmp_path, monkeypatch):
    import httpx as _httpx

    request = _httpx.Request("GET", "http://cw.test/api/v1/accounts/1/labels")
    response = _httpx.Response(401, request=request)
    _fake_dash_chatwoot(
        monkeypatch,
        error=_httpx.HTTPStatusError("401", request=request, response=response),
    )
    app = create_app(_settings(tmp_path, chatwoot_base_url="http://cw.test",
                               chatwoot_api_token="tok", chatwoot_account_id=3))
    with TestClient(app) as client:
        _cookie_login(client)
        body = client.get("/ajustes/etiquetas.json").json()
        assert body["etiquetas"] == []
        assert "401" in body["motivo"]
        assert "token" in body["motivo"].lower()


# --- ChatwootClient: labels de cuenta y contactos por etiqueta ---


class ChatwootLabelsCuentaFake:
    def __init__(self, labels=None, contactos_por_etiqueta=None):
        self.requests = []
        self.labels = labels or []
        self.contactos_por_etiqueta = contactos_por_etiqueta or {}

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith("/labels") and request.method == "GET":
                return httpx.Response(200, json={"payload": self.labels})
            if path.endswith("/contacts/filter"):
                body = json.loads(request.content)
                titulo = body["payload"][0]["values"][0]
                return httpx.Response(
                    200, json={"payload": self.contactos_por_etiqueta.get(titulo, [])}
                )
            return httpx.Response(404, json={})

        self.transport = httpx.MockTransport(handler)

    def client(self):
        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


def test_listar_labels_cuenta():
    fake = ChatwootLabelsCuentaFake(labels=[{"id": 1, "title": "VIP", "color": "#ffc53d"}])
    assert fake.client().listar_labels_cuenta() == [{"id": 1, "title": "VIP", "color": "#ffc53d"}]


def test_contactos_con_etiqueta_verifica_lado_cliente():
    """The filter API shape must be verified live: any contact it returns
    without really having the label is dropped client-side."""
    fake = ChatwootLabelsCuentaFake(
        contactos_por_etiqueta={
            "vieja": [
                {"id": 7, "labels": ["vieja", "vip"]},
                {"id": 8, "labels": ["vip"]},  # API noise: no tiene la etiqueta
            ]
        }
    )
    contactos = fake.client().contactos_con_etiqueta("vieja")
    assert [c["id"] for c in contactos] == [7]


# --- T15 (parte B): migración al cambiar la etiqueta (L24) ---


class ChatwootMigrarFake:
    """MockTransport-backed Chatwoot for migrar_etiqueta unit tests."""

    def __init__(self, contactos=None, conversaciones=None):
        self.requests = []
        self.contactos = contactos or []
        self.conversaciones = conversaciones or []
        self.labels_por_contacto = {
            c["id"]: list(c.get("labels", [])) for c in self.contactos
        }
        self.labels_por_conversacion = {
            c["id"]: list(c.get("labels", [])) for c in self.conversaciones
        }

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append((request.method, request.url.path))
            path = request.url.path
            if path.endswith("/contacts/filter") and request.method == "POST":
                body = json.loads(request.content)
                titulo = body["payload"][0]["values"][0]
                coinciden = [
                    c for c in self.contactos if titulo in (c.get("labels") or [])
                ]
                return httpx.Response(200, json={"payload": coinciden})
            if path.endswith("/conversations") and request.method == "GET":
                label = request.url.params.get("labels[]")
                if label is None:
                    return httpx.Response(200, json={"payload": []})
                coinciden = [
                    c for c in self.conversaciones if label in (c.get("labels") or [])
                ]
                return httpx.Response(200, json={"payload": coinciden})
            if "/contacts/" in path and "/labels" in path and request.method == "GET":
                contacto_id = int(path.split("/contacts/")[1].split("/")[0])
                return httpx.Response(
                    200, json={"payload": self.labels_por_contacto.get(contacto_id, [])}
                )
            if "/contacts/" in path and "/labels" in path and request.method == "POST":
                contacto_id = int(path.split("/contacts/")[1].split("/")[0])
                labels = json.loads(request.content)["labels"]
                self.labels_por_contacto[contacto_id] = labels
                return httpx.Response(200, json={"payload": labels})
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
        return ChatwootClient(
            base_url="http://chatwoot.test", api_token="token-x", account_id=1,
            transport=self.transport,
        )


def _migrar_settings(tmp_path):
    return _settings(
        tmp_path,
        chatwoot_base_url="http://chatwoot.test",
        chatwoot_api_token="token-x",
        chatwoot_account_id=1,
    )


def test_migrar_etiqueta_intercambia_preservando_resto(tmp_path):
    fake = ChatwootMigrarFake(
        contactos=[
            {"id": 1, "labels": ["vieja", "vip"]},
            {"id": 2, "labels": ["vieja"]},
        ]
    )
    resultado = migrar_etiqueta(
        _migrar_settings(tmp_path), "vieja", "nueva", transport=fake.transport
    )
    assert resultado["total_contactos"] == 2
    assert sorted(resultado["migrados_contactos"]) == [1, 2]
    assert resultado["total_conversaciones"] == 0
    assert resultado["migrados_conversaciones"] == []
    assert resultado["fallas"] == []
    assert fake.labels_por_contacto[1] == ["vip", "nueva"]
    assert fake.labels_por_contacto[2] == ["nueva"]
    assert "vieja" not in fake.labels_por_contacto[1]
    assert "vieja" not in fake.labels_por_contacto[2]


def test_migrar_etiqueta_no_duplica_si_nueva_ya_existe(tmp_path):
    fake = ChatwootMigrarFake(contactos=[{"id": 1, "labels": ["vieja", "nueva"]}])
    migrar_etiqueta(_migrar_settings(tmp_path), "vieja", "nueva", transport=fake.transport)
    assert fake.labels_por_contacto[1] == ["nueva"]


def test_migrar_etiqueta_registra_falla_sin_abortar(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contacts/filter"):
            body = json.loads(request.content)
            titulo = body["payload"][0]["values"][0]
            return httpx.Response(
                200, json={"payload": [{"id": 1, "labels": [titulo]}, {"id": 2, "labels": [titulo]}]}
            )
        if "/contacts/1/labels" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={"payload": ["vieja"]})
        if "/contacts/1/labels" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={"payload": ["nueva"]})
        # contact 2 fails on label read
        return httpx.Response(500, json={})

    transport = httpx.MockTransport(handler)
    resultado = migrar_etiqueta(
        _migrar_settings(tmp_path), "vieja", "nueva", transport=transport
    )
    assert resultado["total_contactos"] == 2
    assert resultado["migrados_contactos"] == [1]
    assert resultado["total_conversaciones"] == 0
    assert resultado["migrados_conversaciones"] == []
    assert len(resultado["fallas"]) == 1
    assert resultado["fallas"][0]["tipo"] == "contacto"
    assert resultado["fallas"][0]["id"] == 2


def test_migrar_etiqueta_intercambia_conversaciones_preservando_resto(tmp_path):
    fake = ChatwootMigrarFake(
        conversaciones=[
            {"id": 10, "labels": ["vieja", "pendiente"]},
            {"id": 11, "labels": ["vieja"]},
        ]
    )
    resultado = migrar_etiqueta(
        _migrar_settings(tmp_path), "vieja", "nueva", transport=fake.transport
    )
    assert resultado["total_contactos"] == 0
    assert resultado["migrados_contactos"] == []
    assert resultado["total_conversaciones"] == 2
    assert sorted(resultado["migrados_conversaciones"]) == [10, 11]
    assert resultado["fallas"] == []
    assert fake.labels_por_conversacion[10] == ["pendiente", "nueva"]
    assert fake.labels_por_conversacion[11] == ["nueva"]


def test_migrar_etiqueta_no_duplica_nueva_en_conversacion(tmp_path):
    fake = ChatwootMigrarFake(conversaciones=[{"id": 10, "labels": ["vieja", "nueva"]}])
    migrar_etiqueta(_migrar_settings(tmp_path), "vieja", "nueva", transport=fake.transport)
    assert fake.labels_por_conversacion[10] == ["nueva"]


def test_migrar_etiqueta_registra_falla_en_conversacion_sin_abortar(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/conversations") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "payload": [
                        {"id": 20, "labels": ["vieja"]},
                        {"id": 21, "labels": ["vieja"]},
                    ]
                },
            )
        if "/conversations/20/labels" in request.url.path and request.method == "GET":
            return httpx.Response(200, json={"payload": ["vieja"]})
        if "/conversations/20/labels" in request.url.path and request.method == "POST":
            return httpx.Response(200, json={"payload": ["nueva"]})
        # conversation 21 fails
        return httpx.Response(500, json={})

    transport = httpx.MockTransport(handler)
    resultado = migrar_etiqueta(
        _migrar_settings(tmp_path), "vieja", "nueva", transport=transport
    )
    assert resultado["total_contactos"] == 0
    assert resultado["migrados_contactos"] == []
    assert resultado["total_conversaciones"] == 2
    assert resultado["migrados_conversaciones"] == [20]
    assert len(resultado["fallas"]) == 1
    assert resultado["fallas"][0]["tipo"] == "conversacion"
    assert resultado["fallas"][0]["id"] == 21


def test_migrar_etiqueta_mezcla_contactos_y_conversaciones(tmp_path):
    fake = ChatwootMigrarFake(
        contactos=[{"id": 1, "labels": ["vieja"]}],
        conversaciones=[{"id": 10, "labels": ["vieja"]}],
    )
    resultado = migrar_etiqueta(
        _migrar_settings(tmp_path), "vieja", "nueva", transport=fake.transport
    )
    assert resultado["total_contactos"] == 1
    assert resultado["migrados_contactos"] == [1]
    assert resultado["total_conversaciones"] == 1
    assert resultado["migrados_conversaciones"] == [10]
    assert resultado["fallas"] == []


def _fake_chatwoot_con_contactos(monkeypatch, contactos, conversaciones=None):
    captured = {}

    class FakeChatwootClient:
        def __init__(self, base_url, api_token, account_id):
            captured["creds"] = (base_url, api_token, account_id)

        def contactos_con_etiqueta(self, titulo):
            return contactos

        def conversaciones_con_etiqueta(self, titulo):
            return conversaciones or []

    monkeypatch.setattr("app.dash.ChatwootClient", FakeChatwootClient)
    return captured


def test_post_ajustes_cambio_etiqueta_muestra_confirmacion(tmp_path, monkeypatch):
    _fake_chatwoot_con_contactos(
        monkeypatch,
        [{"id": 1, "labels": ["Reserva confirmada"]}, {"id": 2, "labels": ["Reserva confirmada"]}],
        conversaciones=[
            {"id": 10, "labels": ["Reserva confirmada"]},
            {"id": 11, "labels": ["Reserva confirmada"]},
            {"id": 12, "labels": ["Reserva confirmada"]},
        ],
    )
    app = create_app(
        _settings(
            tmp_path,
            chatwoot_base_url="http://cw.test",
            chatwoot_api_token="tok",
            chatwoot_account_id=3,
        )
    )
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.post(
            "/ajustes",
            data=dict(FORM_DATA, etiqueta_confirmada="Confirmada nueva"),
            follow_redirects=False,
        )
        assert response.status_code == 200
        text = response.text
        assert "2 contactos" in text
        assert "3 conversaciones" in text
        assert "Reserva confirmada" in text
        assert "Confirmada nueva" in text
        assert "migrar-etiqueta" in text

        with client.app.state.sessionmaker() as session:
            config = session.get(Configuracion, 1)
            assert config.etiqueta_confirmada == "Confirmada nueva"


def test_post_ajustes_cambio_etiqueta_cero_contactos_redirige(tmp_path, monkeypatch):
    _fake_chatwoot_con_contactos(monkeypatch, [])
    app = create_app(
        _settings(
            tmp_path,
            chatwoot_base_url="http://cw.test",
            chatwoot_api_token="tok",
            chatwoot_account_id=3,
        )
    )
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.post(
            "/ajustes",
            data=dict(FORM_DATA, etiqueta_confirmada="Otra etiqueta"),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ajustes"


def test_post_ajustes_cambio_etiqueta_chatwoot_no_configurado_redirige(client):
    response = client.post(
        "/ajustes",
        data=dict(FORM_DATA, etiqueta_confirmada="Etiqueta X"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ajustes"


def test_post_ajustes_migrar_etiqueta_realiza_migracion(tmp_path, monkeypatch):
    calls = []

    def fake_migrar(settings, vieja, nueva):
        calls.append((vieja, nueva))
        return {
            "total_contactos": 2,
            "migrados_contactos": [1, 2],
            "total_conversaciones": 3,
            "migrados_conversaciones": [10, 11, 12],
            "fallas": [],
        }

    monkeypatch.setattr("app.dash.migrar_etiqueta", fake_migrar)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.post(
            "/ajustes/migrar-etiqueta",
            data={"vieja": "Reserva confirmada", "nueva": "Confirmada nueva"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ajustes?migrados_contactos=2&migrados_conv=3&fallas=0"
        assert calls == [("Reserva confirmada", "Confirmada nueva")]


def test_get_ajustes_muestra_banner_de_migracion(client):
    response = client.get("/ajustes?migrados_contactos=2&migrados_conv=3&fallas=0")
    assert response.status_code == 200
    assert "Migración lista: 2 contactos y 3 conversaciones actualizados" in response.text


def test_get_ajustes_muestra_fallas_en_banner(client):
    response = client.get("/ajustes?migrados_contactos=2&migrados_conv=3&fallas=1")
    assert response.status_code == 200
    assert "Migración lista: 2 contactos y 3 conversaciones actualizados" in response.text
    assert "1 fallas" in response.text


def test_post_ajustes_migrar_etiqueta_datos_invalidos_redirige(tmp_path, monkeypatch):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.post(
            "/ajustes/migrar-etiqueta",
            data={"vieja": "", "nueva": "Confirmada nueva"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ajustes"

        response = client.post(
            "/ajustes/migrar-etiqueta",
            data={"vieja": "Misma", "nueva": "Misma"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/ajustes"


# --- L26: guardar ajustes con el scheduler ACTIVO no debe crashear ---


def test_post_ajustes_con_scheduler_activo_reprograma_sin_error(tmp_path):
    """Pins the production crash: reprogramar() received request.app.state
    instead of request.app — only reachable when scheduler_enabled=True."""
    app = create_app(_settings(tmp_path, scheduler_enabled=True))
    with TestClient(app) as client:
        _cookie_login(client)
        response = client.post("/ajustes", data=FORM_DATA, follow_redirects=False)
        assert response.status_code == 303, response.text
        # the live scheduler now runs at the newly saved hours
        jobs = {job.id: job for job in app.state.scheduler.get_jobs()}
        assert str(jobs["confirmaciones_t48h"].trigger) == "cron[hour='10', minute='30']"
        assert str(jobs["recordatorio_invitados"].trigger) == "cron[hour='18', minute='45']"

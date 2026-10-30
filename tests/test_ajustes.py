"""T11 tests (L19): messaging hours and WhatsApp templates are configured in
the admin panel and persisted in the DB — not via Docker env vars.

Covers: seeding from env defaults on first boot, the /ajustes page (auth,
valid save, invalid hour rejected without persisting), and the scheduler
reading/re-programming jobs from the DB configuration."""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
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

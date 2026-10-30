"""T6 iframe page tests (S4): per-phone reservations embeddable in Chatwoot.

The iframe lives inside Chatwoot's UI, so it cannot use our cookie login:
authorization is the embed key (iframe_key, fail-closed when unset).
"""

import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

EMBED_KEY = "embed-secret-123"


def _make_iframe_settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        iframe_key=EMBED_KEY,
        _env_file=None,
    )


def _login(client: TestClient):
    """The CRUD API is JWT-protected; authenticate the test client."""
    response = client.post(
        "/api/auth/login", data={"username": "valen", "password": "testpass123"}
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "nombre": "Test Guest",
        "telefono": "+54 9 11 5555-1234",
        "fecha": "2026-12-24",
        "hora": "21:00",
        "personas": 4,
    }
    payload.update(overrides)
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_iframe_shows_only_that_phones_reservas_no_login(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, nombre="Valen", telefono="+54 9 11 5555-1234")
        _create(client, nombre="Valen", telefono="+54 9 11 5555-1234", fecha="2026-12-31")
        _create(client, nombre="Otro", telefono="+54 9 11 4444-0000")

        response = client.get(
            "/iframe/reservas", params={"tel": "+5491155551234", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        body = response.text
        assert "24/12/2026" in body
        assert "31/12/2026" in body
        assert "4444" not in body  # the other phone's data never leaks
        assert "Otro" not in body


def test_iframe_matches_phone_by_digits_regardless_of_format(tmp_path):
    """Chatwoot passes E.164 (+54911...); stored phones may differ in punctuation."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, telefono="+54 9 11 5555-1234")
        for tel in ("5491155551234", "+5491155551234", "54 9 11 5555-1234"):
            response = client.get("/iframe/reservas", params={"tel": tel, "key": EMBED_KEY})
            assert response.status_code == 200
            assert "24/12/2026" in response.text


def test_iframe_wrong_or_missing_key_returns_403(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, telefono="+54 9 11 5555-1234")
        wrong = client.get("/iframe/reservas", params={"tel": "+5491155551234", "key": "nope"})
        missing = client.get("/iframe/reservas", params={"tel": "+5491155551234"})
        assert wrong.status_code == 403
        assert missing.status_code == 403


def test_iframe_fails_closed_when_key_unset(tmp_path):
    settings = _make_iframe_settings(tmp_path)
    settings = Settings(
        database_path=settings.database_path,
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        _env_file=None,
    )  # no iframe_key: default ""
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas", params={"tel": "+5491155551234", "key": ""}
        )
        assert response.status_code == 403


def test_iframe_unknown_phone_renders_empty_message(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/reservas", params={"tel": "+5491100000000", "key": EMBED_KEY})
        assert response.status_code == 200
        assert "Sin reservas" in response.text


def test_iframe_sends_csp_frame_ancestors_and_is_standalone(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, telefono="+54 9 11 5555-1234")
        response = client.get("/iframe/reservas", params={"tel": "+5491155551234", "key": EMBED_KEY})
        assert response.status_code == 200
        # embeddable: explicit frame-ancestors policy instead of browser defaults
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp
        # standalone page: no dashboard nav leaks into the iframe
        assert "Nueva reserva" not in response.text
        assert "Salir" not in response.text


def test_iframe_requires_tel_param(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/reservas", params={"key": EMBED_KEY})
        assert response.status_code in (400, 422)


def test_iframe_orders_by_fecha_then_hora(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, telefono="+54 9 11 5555-1234", fecha="2026-12-31", hora="20:00")
        _create(client, telefono="+54 9 11 5555-1234", fecha="2026-12-24", hora="22:00")
        _create(client, telefono="+54 9 11 5555-1234", fecha="2026-12-24", hora="20:00")
        response = client.get("/iframe/reservas", params={"tel": "+5491155551234", "key": EMBED_KEY})
        assert response.status_code == 200
        positions = [response.text.index(m) for m in ("24/12/2026", "20:00", "22:00", "31/12/2026")]
        assert positions == sorted(positions)


# --- Dashboard Apps wiring: Chatwoot sends contact context as a window
# event (appContext), NOT as URL params, so the configured URL is static
# and the page fetches data client-side (docs: How to use Dashboard Apps).


def test_iframe_app_page_is_static_shell_with_key(tmp_path):
    """The Dashboard App URL is configured once in Chatwoot: the page must
    render without knowing the phone and contain the appContext listener."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        assert "appContext" in response.text
        assert "reservas.json" in response.text
        # Chatwoot delivers the context as a JSON string; the page must parse
        # it AND be able to request it on demand (chatwoot-dashboard-app:fetch-info).
        assert "JSON.parse(event.data)" in response.text
        assert "chatwoot-dashboard-app:fetch-info" in response.text
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp


def test_iframe_app_page_requires_key(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app")
        assert response.status_code == 403


def test_iframe_json_returns_only_that_phones_reservas(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, nombre="Valen", telefono="+54 9 11 5555-1234")
        _create(client, nombre="Otro", telefono="+54 9 11 4444-0000")
        response = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["nombre"] == "Valen"
        assert data[0]["personas"] == 4


def test_iframe_json_wrong_key_returns_403(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": "nope"}
        )
        assert response.status_code == 403


def test_iframe_json_unknown_phone_returns_empty_list(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas.json", params={"tel": "+5491100000000", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        assert response.json() == []

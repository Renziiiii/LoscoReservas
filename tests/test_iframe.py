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


# --- T9: alta de reserva desde la pestaña del iframe (L15) ---


def _payload(**overrides) -> dict:
    payload = {
        "nombre": "Valen",
        "telefono": "+54 9 11 5555-1234",
        "fecha": "2026-12-24",
        "hora": "21:00",
        "personas": 4,
    }
    payload.update(overrides)
    return payload


def test_iframe_create_reserva_with_valid_key(tmp_path):
    """The Chatwoot tab can register a reservation: POST with the embed key
    creates it and it shows up in the same phone's list."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["nombre"] == "Valen"
        assert body["telefono"] == "+5491155551234"  # normalized like the CRUD API
        assert body["estado"] == "pendiente"

        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1


def test_iframe_create_with_wrong_key_returns_403_and_stores_nothing(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json", params={"key": "nope"}, json=_payload()
        )
        assert response.status_code == 403
        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json() == []


def test_iframe_create_rejects_invalid_payload_and_stores_nothing(tmp_path):
    """Same validation as the CRUD API: bad personas/fecha → 422, nothing stored."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        for bad in (_payload(personas=0), _payload(fecha="navidad"), _payload(nombre="")):
            response = client.post(
                "/iframe/reservas.json", params={"key": EMBED_KEY}, json=bad
            )
            assert response.status_code == 422, bad
        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json() == []


def test_iframe_app_page_contains_create_form(tmp_path):
    """Structural: the shell renders the register form (prefilled by JS)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        for marker in (
            'id="form-reserva"',
            'name="fecha"',
            'name="hora"',
            'name="personas"',
            'name="detalles"',
            'name="nombre"',
            'name="telefono"',
        ):
            assert marker in response.text, marker


# --- T10: CRUD completo en el iframe + tema dark Chatwoot (L16) ---


def test_iframe_json_list_includes_id(tmp_path):
    """The iframe list now exposes the reserva id so the UI can address rows."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234")
        response = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "id" in data[0]
        assert data[0]["id"] == created["id"]


def test_iframe_patch_updates_reserva_and_persists(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234", personas=4)
        reserva_id = created["id"]

        patch = client.patch(
            f"/iframe/reservas/{reserva_id}.json",
            params={"key": EMBED_KEY},
            json={"personas": 6, "detalles": "Mesa cerca de la ventana"},
        )
        assert patch.status_code == 200, patch.text
        body = patch.json()
        assert body["personas"] == 6
        assert body["detalles"] == "Mesa cerca de la ventana"
        assert body["id"] == reserva_id

        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.status_code == 200
        row = listed.json()[0]
        assert row["personas"] == 6
        assert row["detalles"] == "Mesa cerca de la ventana"
        assert row["nombre"] == created["nombre"]


def test_iframe_patch_wrong_key_forbidden_and_unchanged(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234", personas=4)
        reserva_id = created["id"]

        response = client.patch(
            f"/iframe/reservas/{reserva_id}.json",
            params={"key": "nope"},
            json={"personas": 6},
        )
        assert response.status_code == 403
        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json()[0]["personas"] == 4


def test_iframe_patch_invalid_payload_unprocessable_and_unchanged(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234", personas=4)
        reserva_id = created["id"]

        response = client.patch(
            f"/iframe/reservas/{reserva_id}.json",
            params={"key": EMBED_KEY},
            json={"personas": 0},
        )
        assert response.status_code == 422
        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json()[0]["personas"] == 4


def test_iframe_patch_unknown_id_returns_404(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.patch(
            "/iframe/reservas/9999.json",
            params={"key": EMBED_KEY},
            json={"personas": 6},
        )
        assert response.status_code == 404


def test_iframe_delete_removes_reserva(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234")
        reserva_id = created["id"]

        before = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert len(before.json()) == 1

        response = client.delete(
            f"/iframe/reservas/{reserva_id}.json", params={"key": EMBED_KEY}
        )
        assert response.status_code == 204

        after = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert after.json() == []


def test_iframe_delete_wrong_key_forbidden_and_keeps_reserva(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234")
        reserva_id = created["id"]

        response = client.delete(
            f"/iframe/reservas/{reserva_id}.json", params={"key": "nope"}
        )
        assert response.status_code == 403
        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert len(listed.json()) == 1


def test_iframe_delete_unknown_id_returns_404(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.delete(
            "/iframe/reservas/9999.json", params={"key": EMBED_KEY}
        )
        assert response.status_code == 404


def test_iframe_app_page_has_edit_delete_and_dark_theme(tmp_path):
    """Structural: rows carry edit/delete markers, edit button has its own id,
    and the page uses Chatwoot's real dark background."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        text = response.text
        assert "data-edit=" in text
        assert "data-delete=" in text
        assert 'id="f-submit-edit"' in text
        assert "#111113" in text


# --- T10 fixes: estado editable + bug de shadowing + delegación de eventos ---


def test_iframe_create_and_patch_allow_estado_change(tmp_path):
    """The tab must let the agent change pendiente/confirmada/cancelada."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json", params={"key": EMBED_KEY}, json=_payload()
        )
        assert created.status_code == 201
        assert created.json()["estado"] == "pendiente"

        rid = created.json()["id"]
        patched = client.patch(
            f"/iframe/reservas/{rid}.json",
            params={"key": EMBED_KEY},
            json={"estado": "confirmada"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["estado"] == "confirmada"

        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json()[0]["estado"] == "confirmada"

        # creating directly with an estado also works (full CRUD parity)
        otra = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(fecha="2026-12-31", estado="cancelada"),
        )
        assert otra.status_code == 201
        assert otra.json()["estado"] == "cancelada"


def test_iframe_form_has_estado_select(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        assert 'name="estado"' in response.text
        for estado in ("pendiente", "confirmada", "cancelada"):
            assert f'value="{estado}"' in response.text


def test_iframe_submit_handler_has_no_ok_shadowing(tmp_path):
    """Pins the L17 bug: destructuring {ok, status, data} shadowed the DOM
    element and crashed ok.style after a successful save."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        assert "respOk" in response.text
        assert "{ ok, status, data }" not in response.text


def test_iframe_row_actions_only_on_buttons(tmp_path):
    """Pins the delegation bug: <tr> carried data-edit/data-delete so the
    delete button opened edit mode and any cell click triggered edit."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        # delegation targets buttons only
        assert "button[data-edit], button[data-delete]" in response.text
        # row keeps a numeric id hook and the JSON payload, but no action attrs
        assert '<tr data-id="${r.id}"' in response.text
        assert 'data-reserva="${esc(JSON.stringify(r))}"' in response.text
        assert '<tr data-id="${r.id}" data-edit' not in response.text


# --- T12: opt-out de mensajería por reserva desde el iframe (L19) ---


def test_iframe_app_page_contains_recordatorio_checkbox(tmp_path):
    """Structural: the form exposes the 'send reminder' choice (default on)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        for marker in ('id="f-recordatorio"', 'name="enviar_recordatorio"'):
            assert marker in response.text, marker


def test_iframe_create_defaults_enviar_recordatorio_true(tmp_path):
    """Omitting the field keeps the reminder on (user: default sí)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json", params={"key": EMBED_KEY}, json=_payload()
        )
        assert response.status_code == 201, response.text
        assert response.json()["enviar_recordatorio"] is True


def test_iframe_create_respects_enviar_recordatorio_false(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(enviar_recordatorio=False),
        )
        assert response.status_code == 201, response.text
        assert response.json()["enviar_recordatorio"] is False

        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json()[0]["enviar_recordatorio"] is False


def test_iframe_json_list_includes_enviar_recordatorio(tmp_path):
    """The row data feeds the edit form, so the flag must be serialized."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, telefono="+54 9 11 5555-1234", enviar_recordatorio=False)
        response = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        assert response.json()[0]["enviar_recordatorio"] is False


def test_iframe_patch_enviar_recordatorio_and_persists(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        created = _create(client, telefono="+54 9 11 5555-1234")

        patch = client.patch(
            f"/iframe/reservas/{created['id']}.json",
            params={"key": EMBED_KEY},
            json={"enviar_recordatorio": False},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["enviar_recordatorio"] is False

        listed = client.get(
            "/iframe/reservas.json", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert listed.json()[0]["enviar_recordatorio"] is False

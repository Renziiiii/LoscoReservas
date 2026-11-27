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


def test_iframe_json_todas_devuelve_todo_el_calendario(tmp_path):
    """L61: el calendario del iframe muestra TODAS las reservas (el agente
    ve la disponibilidad), no sólo las del contacto."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, nombre="Valen", telefono="+54 9 11 5555-1234")
        _create(client, nombre="Otro", telefono="+54 9 11 4444-0000", personas=6)
        response = client.get(
            "/iframe/reservas.json",
            params={"tel": "5491155551234", "todas": "1", "key": EMBED_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert {r["nombre"] for r in data} == {"Valen", "Otro"}


def test_iframe_json_todas_sin_tel_devuelve_todo_el_calendario(tmp_path):
    """Contactos de Instagram sin telefono: el calendario muestra las
    reservas del bar sin depender del tel del contacto (tel opcional
    cuando todas=1)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _login(client)
        _create(client, nombre="Valen", telefono="+54 9 11 5555-1234")
        _create(client, nombre="Otro", telefono="+54 9 11 4444-0000", personas=6)
        response = client.get(
            "/iframe/reservas.json",
            params={"todas": "1", "key": EMBED_KEY},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert {r["nombre"] for r in data} == {"Valen", "Otro"}


def test_iframe_json_sin_tel_sin_todas_devuelve_422(tmp_path):
    """Sin `todas`, el tel sigue siendo obligatorio (el endpoint es por
    telefono; el calendario es el unico consumidor de todas=1)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
        )
        assert response.status_code == 422


def test_iframe_json_todas_wrong_key_returns_403(tmp_path):
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas.json",
            params={"tel": "5491155551234", "todas": "1", "key": "nope"},
        )
        assert response.status_code == 403


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


def test_iframe_app_page_supports_light_theme_with_auto_detection(tmp_path):
    """The tab ships a light palette next to the dark one: CSS picks it via
    prefers-color-scheme, and the JS applies Chatwoot's appContext theme
    (data-theme) when the payload includes it, so the iframe follows the
    agent's Chatwoot mode live."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        text = response.text
        assert "#111113" in text  # dark stays the default palette
        assert "prefers-color-scheme: light" in text
        assert '[data-theme="light"]' in text
        assert re.search(r"--bg:\s*#f4f4f5", text)
        assert "data.data.theme" in text
        assert "documentElement.dataset.theme" in text


def test_iframe_debug_page_supports_light_and_dark_theme(tmp_path):
    """The direct-link page (/iframe/reservas) carries both palettes too,
    selected purely by media query (it receives no appContext)."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get(
            "/iframe/reservas", params={"tel": "5491155551234", "key": EMBED_KEY}
        )
        assert response.status_code == 200
        text = response.text
        assert "#111113" in text
        assert "prefers-color-scheme: light" in text
        assert re.search(r"--bg:\s*#f4f4f5", text)


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


# --- T21: anotar el teléfono cuando el contacto no lo tiene (S11/L46) ---


def test_iframe_app_page_never_blocks_on_missing_phone(tmp_path):
    """The form must render even when the contact has no phone in Chatwoot:
    the agent annotates the number they asked for in the chat. The old
    blocking message is replaced by a hint, the phone input becomes
    required, and the appContext contact id travels with the form."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        text = response.text
        assert "El contacto no tiene teléfono cargado en Chatwoot." not in text
        assert 'id="sin-telefono-hint"' in text
        # L51: the hint teaches the expected international format
        assert "código de país" in text
        # L54: the example is generic, digits-only (the app adds the '+' and
        # swallows punctuation; a real client's number must not appear)
        assert "5491234567890" in text
        assert "351 260-2003" not in text
        # the phone cannot be derived from the contact: the form requires it
        assert 'id="f-telefono" name="telefono" type="tel" maxlength="32" required' in text
        # the appContext contact id enables the Chatwoot phone sync
        assert "contacto_chatwoot_id" in text
        assert "contactId = contact.id || null" in text


def test_iframe_create_passes_contact_id_to_sync(tmp_path, monkeypatch):
    """POST with contacto_chatwoot_id: the sync runs with the contact id and
    the NORMALIZED phone (same rule as the rest of the app)."""
    llamadas = []

    def espia(settings, contacto_id, telefono, transport=None):
        llamadas.append((contacto_id, telefono))
        return True

    monkeypatch.setattr("app.iframe.sincronizar_telefono_contacto", espia)
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        payload = _payload(telefono="+54 9 351 370-8732", contacto_chatwoot_id=1096)
        response = client.post(
            "/iframe/reservas.json", params={"key": EMBED_KEY}, json=payload
        )
        assert response.status_code == 201, response.text
    assert llamadas == [(1096, "+5493513708732")]


def test_iframe_create_without_contact_id_skips_sync(tmp_path, monkeypatch):
    llamadas = []

    def espia(settings, contacto_id, telefono, transport=None):
        llamadas.append(contacto_id)
        return False

    monkeypatch.setattr("app.iframe.sincronizar_telefono_contacto", espia)
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json", params={"key": EMBED_KEY}, json=_payload()
        )
        assert response.status_code == 201, response.text
    assert llamadas == []


def test_iframe_create_survives_sync_reporting_failure(tmp_path, monkeypatch):
    """The sync never raises by contract (fail-soft inside, pinned in
    test_contactos): when it reports False the save has already succeeded."""
    def espia(settings, contacto_id, telefono, transport=None):
        return False

    monkeypatch.setattr("app.iframe.sincronizar_telefono_contacto", espia)
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(contacto_chatwoot_id=1096),
        )
        assert response.status_code == 201, response.text
        assert response.json()["telefono"] == "+5491155551234"


def test_iframe_patch_with_new_phone_syncs_contact(tmp_path, monkeypatch):
    """Correcting the phone on an existing reserva syncs the contact too
    (same only-if-empty rule); patches that do not touch the phone don't."""
    llamadas = []

    def espia(settings, contacto_id, telefono, transport=None):
        llamadas.append((contacto_id, telefono))
        return True

    monkeypatch.setattr("app.iframe.sincronizar_telefono_contacto", espia)
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(contacto_chatwoot_id=1096),
        )
        assert created.status_code == 201, created.text
        rid = created.json()["id"]

        patch = client.patch(
            f"/iframe/reservas/{rid}.json",
            params={"key": EMBED_KEY},
            json={"telefono": "+54 9 351 764-4283", "contacto_chatwoot_id": 1206},
        )
        assert patch.status_code == 200, patch.text
    # the create synced 1096 with the original phone; the phone edit synced
    # 1206 with the NEW one
    assert llamadas == [(1096, "+5491155551234"), (1206, "+5493517644283")]


def test_iframe_patch_without_phone_change_skips_sync(tmp_path, monkeypatch):
    llamadas = []

    def espia(settings, contacto_id, telefono, transport=None):
        llamadas.append(contacto_id)
        return False

    monkeypatch.setattr("app.iframe.sincronizar_telefono_contacto", espia)
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        created = client.post(
            "/iframe/reservas.json",
            params={"key": EMBED_KEY},
            json=_payload(),
        )
        rid = created.json()["id"]
        patch = client.patch(
            f"/iframe/reservas/{rid}.json",
            params={"key": EMBED_KEY},
            json={"personas": 6, "contacto_chatwoot_id": 1096},
        )
        assert patch.status_code == 200, patch.text
    assert llamadas == []


def test_iframe_app_warns_on_argentine_number_without_9(tmp_path):
    """L53: a +54 number with exactly 12 digits looks like a mistyped
    mobile (the 9 is missing) — it would save fine and then silently
    never deliver on WhatsApp. The form must confirm before submitting."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        response = client.get("/iframe/app", params={"key": EMBED_KEY})
        assert response.status_code == 200
        text = response.text
        assert 'telDigits.startsWith("54") && telDigits.length === 12' in text
        assert "9 de celular" in text
        # L54: generic digits-only example, same convention as the hint
        assert "5491234567890" in text
        assert "351 260-2003" not in text
        # cancelling the dialog aborts the submit without disabling the button
        assert "!window.confirm" in text


# --- colores configurados en el render del iframe (tags-color) ---

from datetime import date as _date, time as _time

from app.models import EstadoReserva as _EstadoReserva, Reserva as _Reserva


def _crear_estado_colorado(app, slug, color):
    with app.state.sessionmaker() as session:
        session.add(_EstadoReserva(nombre=slug.capitalize(), slug=slug, color=color))
        session.commit()


def test_iframe_reservas_pinta_color_configurado(tmp_path):
    """S6: la página HTML del iframe usa el color configurado del estado
    (inline style), no el CSS fijo por clase."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        _crear_estado_colorado(app, "demorada", "#123456")
        with app.state.sessionmaker() as session:
            session.add(_Reserva(nombre="Nadie", telefono="+5491112223334",
                                 fecha=_date(2026, 3, 20), hora=_time(21, 0),
                                 personas=2, estado="demorada"))
            session.commit()
        resp = client.get("/iframe/reservas?tel=%2B5491112223334&key=" + EMBED_KEY)
        assert resp.status_code == 200
        assert "color:#123456" in resp.text or "color: #123456" in resp.text


def test_iframe_app_incluye_logica_de_colores(tmp_path):
    """S6: iframe_app.html carga /iframe/estados.json y aplica el color
    configurado inline sobre el badge del estado."""
    app = create_app(_make_iframe_settings(tmp_path))
    with TestClient(app) as client:
        html = client.get("/iframe/app?key=" + EMBED_KEY).text
        assert "estados.json" in html
        assert "estiloColorEstado" in html

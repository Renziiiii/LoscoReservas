"""T4 dashboard tests (S5: "Tiene que haber un dashbord donde pueda ver
todas las reservas, actualmente usan un google sheets").

Browser flows use the session cookie set by POST /login; the bearer-token
`client` also works because get_current_user accepts both.
"""

import pytest


@pytest.fixture()
def browser(anon_client):
    """A logged-in browser session (cookie auth)."""
    response = anon_client.post(
        "/login", data={"username": "valen", "password": "testpass123"}, follow_redirects=False
    )
    assert response.status_code == 303
    return anon_client


VALID_FORM = {
    "nombre": "Martín Sosa",
    "telefono": "+54 9 11 4444-5555",
    "fecha": "2026-03-20",
    "hora": "21:30",
    "personas": "3",
    "estado": "pendiente",
    "detalles": "Cumpleaños",
    "enviar_recordatorio": "on",
}


def test_index_anonymous_redirects_to_login(anon_client):
    response = anon_client.get("/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers["location"]


def test_login_page_renders(anon_client):
    response = anon_client.get("/login")
    assert response.status_code == 200
    assert "Usuario" in response.text


def test_login_bad_credentials_shows_error(anon_client):
    response = anon_client.post("/login", data={"username": "valen", "password": "nope"})
    assert response.status_code == 200
    assert "Credenciales inválidas" in response.text


def test_login_ok_sets_cookie_and_redirects(anon_client):
    response = anon_client.post(
        "/login", data={"username": "valen", "password": "testpass123"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "losco_session" in response.headers.get("set-cookie", "")


def test_dashboard_lists_reservas(browser):
    browser.post("/reservas", data=VALID_FORM)
    response = browser.get("/")
    assert response.status_code == 200
    assert "Martín Sosa" in response.text
    assert "+5491144445555" in response.text


def test_dashboard_filter_by_estado(browser):
    browser.post("/reservas", data=VALID_FORM)
    browser.post("/reservas", data={**VALID_FORM, "nombre": "Ana Ruiz", "estado": "confirmada"})
    page = browser.get("/", params={"estado": "confirmada"}).text
    assert "Ana Ruiz" in page
    assert "Martín Sosa" not in page


def test_create_via_form_redirects_and_persists(browser, client):
    response = browser.post("/reservas", data=VALID_FORM, follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/reservas").json()[0]["nombre"] == "Martín Sosa"


def test_create_via_form_with_validation_error_rerenders_and_stores_nothing(browser, client):
    response = browser.post("/reservas", data={**VALID_FORM, "nombre": ""})
    assert response.status_code == 200
    assert "error" in response.text.lower()
    assert client.get("/api/reservas").json() == []


def test_edit_page_prefills_fields(browser):
    browser.post("/reservas", data=VALID_FORM)
    page = browser.get("/reservas/1/editar").text
    assert "Martín Sosa" in page


def test_update_via_form(browser, client):
    browser.post("/reservas", data=VALID_FORM)
    response = browser.post(
        "/reservas/1", data={**VALID_FORM, "personas": "8"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert client.get("/api/reservas/1").json()["personas"] == 8


def test_delete_non_htmx_redirects_and_removes(browser, client):
    browser.post("/reservas", data=VALID_FORM)
    response = browser.post("/reservas/1/eliminar", follow_redirects=False)
    assert response.status_code == 303
    assert client.get("/api/reservas/1").status_code == 404


def test_delete_htmx_returns_empty_fragment(browser, client):
    browser.post("/reservas", data=VALID_FORM)
    response = browser.post(
        "/reservas/1/eliminar", headers={"HX-Request": "true"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert response.text.strip() == ""
    assert client.get("/api/reservas/1").status_code == 404


def test_brand_logo_links_back_home(browser):
    """L23: the brand is a link to / so every page (e.g. /ajustes) can
    navigate back, and it just reads 'Losco'."""
    response = browser.get("/")
    assert response.status_code == 200
    assert '<a class="brand" href="/">Losco</a>' in response.text

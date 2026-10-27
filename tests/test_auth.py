"""T3 auth tests (assumption A1: simple JWT login, users seeded from env).

Rules: valid login returns a bearer token; wrong credentials get 401; all
reservas endpoints reject anonymous access; /api/health stays public.
"""

def test_login_with_valid_credentials_returns_token(client):
    response = client.post(
        "/api/auth/login",
        data={"username": "valen", "password": "testpass123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_with_wrong_password_returns_401(client):
    response = client.post(
        "/api/auth/login",
        data={"username": "valen", "password": "otra-cosa"},
    )
    assert response.status_code == 401


def test_login_with_unknown_user_returns_401(client):
    response = client.post(
        "/api/auth/login",
        data={"username": "intruso", "password": "testpass123"},
    )
    assert response.status_code == 401


def test_reservas_endpoints_reject_anonymous_access(anon_client):
    assert anon_client.get("/api/reservas").status_code == 401
    payload = {
        "nombre": "X",
        "telefono": "+5491155551234",
        "fecha": "2026-03-20",
        "hora": "21:30",
        "personas": 2,
    }
    assert anon_client.post("/api/reservas", json=payload).status_code == 401
    assert anon_client.patch("/api/reservas/1", json={"personas": 3}).status_code == 401
    assert anon_client.delete("/api/reservas/1").status_code == 401


def test_health_stays_public(anon_client):
    assert anon_client.get("/api/health").status_code == 200


def test_me_returns_current_user(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "valen"


def test_me_rejects_garbage_token(anon_client):
    anon_client.headers["Authorization"] = "Bearer no-es-un-jwt"
    assert anon_client.get("/api/auth/me").status_code == 401

"""T2 CRUD tests (S1: "CRUD con API").

Each rule's observable effect is asserted: status codes, echoed bodies, and
that rejected input leaves the stored rows unchanged.
"""

VALID_PAYLOAD = {
    "nombre": "Valen",
    "telefono": "+54 9 11 5555-1234",
    "fecha": "2026-03-20",
    "hora": "21:30",
    "personas": 4,
    "detalles": "Mesa cerca de la barra",
}


def _count_rows(client):
    """Stored rows through the public interface (list endpoint)."""
    return len(client.get("/api/reservas").json())


# --- POST ---


def test_create_valid_reserva_returns_201_with_defaults(client):
    response = client.post("/api/reservas", json=VALID_PAYLOAD)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["nombre"] == "Valen"
    assert body["telefono"] == "+5491155551234"
    assert body["estado"] == "pendiente"  # default
    assert body["enviar_recordatorio"] is True  # default


def test_create_rejects_missing_nombre_and_stores_nothing(client):
    payload = dict(VALID_PAYLOAD)
    del payload["nombre"]
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 422
    assert _count_rows(client) == 0


def test_create_rejects_personas_below_one(client):
    payload = dict(VALID_PAYLOAD, personas=0)
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 422
    assert _count_rows(client) == 0


def test_create_rejects_telefono_without_digits(client):
    payload = dict(VALID_PAYLOAD, telefono="hola")
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 422
    assert _count_rows(client) == 0


def test_create_rejects_unknown_estado(client):
    payload = dict(VALID_PAYLOAD, estado="vuelta de tuerca")
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 422
    assert _count_rows(client) == 0


def test_create_rejects_invalid_date(client):
    payload = dict(VALID_PAYLOAD, fecha="20/03/2026")
    response = client.post("/api/reservas", json=payload)
    assert response.status_code == 422
    assert _count_rows(client) == 0


# --- GET list / detail ---


def test_list_empty_returns_empty_array(client):
    response = client.get("/api/reservas")
    assert response.status_code == 200
    assert response.json() == []


def test_list_ordered_by_fecha_then_hora(client):
    client.post("/api/reservas", json=dict(VALID_PAYLOAD, fecha="2026-03-21", hora="22:00"))
    client.post("/api/reservas", json=dict(VALID_PAYLOAD, fecha="2026-03-20", hora="23:00"))
    client.post("/api/reservas", json=dict(VALID_PAYLOAD, fecha="2026-03-20", hora="21:00"))
    body = client.get("/api/reservas").json()
    assert [(r["fecha"], r["hora"]) for r in body] == [
        ("2026-03-20", "21:00:00"),
        ("2026-03-20", "23:00:00"),
        ("2026-03-21", "22:00:00"),
    ]


def test_list_filters_by_estado_and_fecha(client):
    client.post("/api/reservas", json=dict(VALID_PAYLOAD, fecha="2026-03-20"))
    client.post("/api/reservas", json=dict(VALID_PAYLOAD, fecha="2026-03-21", estado="confirmada"))
    assert len(client.get("/api/reservas").json()) == 2
    only_confirmed = client.get("/api/reservas", params={"estado": "confirmada"}).json()
    assert [r["estado"] for r in only_confirmed] == ["confirmada"]
    only_friday = client.get("/api/reservas", params={"fecha": "2026-03-20"}).json()
    assert [r["fecha"] for r in only_friday] == ["2026-03-20"]


def test_get_by_id_returns_reserva(client):
    created = client.post("/api/reservas", json=VALID_PAYLOAD).json()
    response = client.get(f"/api/reservas/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_id_returns_404(client):
    assert client.get("/api/reservas/999").status_code == 404


# --- PATCH ---


def test_patch_updates_only_sent_fields(client):
    created = client.post("/api/reservas", json=VALID_PAYLOAD).json()
    response = client.patch(
        f"/api/reservas/{created['id']}", json={"personas": 6, "estado": "confirmada"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["personas"] == 6
    assert body["estado"] == "confirmada"
    assert body["nombre"] == created["nombre"]  # untouched
    assert body["telefono"] == created["telefono"]


def test_patch_rejects_invalid_value_and_keeps_original(client):
    created = client.post("/api/reservas", json=VALID_PAYLOAD).json()
    response = client.patch(f"/api/reservas/{created['id']}", json={"personas": -3})
    assert response.status_code == 422
    assert client.get(f"/api/reservas/{created['id']}").json()["personas"] == created["personas"]


def test_patch_missing_id_returns_404(client):
    assert client.patch("/api/reservas/999", json={"personas": 2}).status_code == 404


# --- DELETE ---


def test_delete_removes_reserva(client):
    created = client.post("/api/reservas", json=VALID_PAYLOAD).json()
    assert client.delete(f"/api/reservas/{created['id']}").status_code == 204
    assert client.get(f"/api/reservas/{created['id']}").status_code == 404
    assert _count_rows(client) == 0


def test_delete_missing_id_returns_404(client):
    assert client.delete("/api/reservas/999").status_code == 404

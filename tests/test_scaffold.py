"""T1 scaffold tests: the app boots, and the database matches the S7 field list.

S7 (verbatim from the user's note): "DB con: id,nombre,telefono,fecha, hora
personas, estado, detalles, enviar recordatorio,"
"""

import sqlite3

REQUIRED_COLUMNS = {
    "id",
    "nombre",
    "telefono",
    "fecha",
    "hora",
    "personas",
    "estado",
    "detalles",
    "enviar_recordatorio",
    "created_at",
    "updated_at",
}


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_reservas_table_has_all_required_columns(client, db_path):
    connection = sqlite3.connect(db_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(reservas)")}
    connection.close()
    assert REQUIRED_COLUMNS <= columns, f"missing columns: {REQUIRED_COLUMNS - columns}"


def test_database_uses_wal_mode(client, db_path):
    connection = sqlite3.connect(db_path)
    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    connection.close()
    assert mode == "wal"


def test_openapi_schema_is_served(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "LoscoReservas" in response.json()["info"]["title"]

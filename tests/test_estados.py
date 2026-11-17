"""Casos de prueba — T1, odd/tasks/tags-estados-sync.md

Cubre S3/S4 del doc de feature:
- tabla configurada de estados (semilla en arranque): pendiente, confirmada, cancelada;
- S4: cada estado expone `etiqueta_chatwoot` (mapping opcional) y `envia_recordatorio`;
- S3: validación dinámica de estado contra la tabla (ya no el Literal fijo de schemas);
- GET /api/estados (protegido) y GET /iframe/estados.json (embed key) para el front;
- regla S4 mensajería: reserva con estado sin `envia_recordatorio` no recibe envíos.
"""

from datetime import date, time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Configuracion, EstadoReserva
from tests.conftest import _make_settings

VALID = dict(nombre="A", telefono="+5493512602003", fecha="2026-03-21",
             hora="20:00", personas=2)


@pytest.fixture()
def app_ytablas(tmp_path):
    """App booteada (lifespan corrió: tabla de estados semillada) + TestClient."""
    app = create_app(_make_settings(tmp_path))
    with TestClient(app) as test_client:
        yield app, test_client


def _seed_estado(app, nombre, slug, envia_recordatorio=True, etiqueta=None):
    with app.state.sessionmaker() as session:
        session.add(EstadoReserva(nombre=nombre, slug=slug,
                                  envia_recordatorio=envia_recordatorio,
                                  etiqueta_chatwoot=etiqueta))
        session.commit()


def test_seeds_base_estados(client):
    """S3: la tabla de estados arranca semillada con los 3 estados base actuales."""
    resp = client.get("/api/estados")
    assert resp.status_code == 200
    estados = {row["slug"]: row for row in resp.json()}
    assert set(estados) == {"pendiente", "confirmada", "cancelada"}


def test_estados_expone_campos(client):
    """S4: cada estado expone etiqueta_chatwoot y envia_recordatorio."""
    estados = {row["slug"]: row for row in client.get("/api/estados").json()}
    assert estados["cancelada"]["envia_recordatorio"] is False
    assert estados["pendiente"]["envia_recordatorio"] is True
    assert estados["confirmada"]["envia_recordatorio"] is True
    assert estados["confirmada"]["etiqueta_chatwoot"] == "Reserva confirmada"
    assert estados["pendiente"]["etiqueta_chatwoot"] is None


def test_estados_requiere_auth(anon_client):
    assert anon_client.get("/api/estados").status_code in (401, 403)


def test_estado_desconocido_rechazado(client):
    resp = client.post("/api/reservas", json=dict(VALID, estado="vuelta de tuerca"))
    assert resp.status_code == 422


def test_estado_custom_en_tabla_pasa_validacion(client):
    """S3: un estado creado en la tabla pasa la validación de reserva — la
    fuente de verdad es la tabla, no el Literal estático de schemas.py."""
    _seed_estado(client.app, "Esperando", "esperando")
    resp = client.post("/api/reservas", json=dict(VALID, estado="esperando"))
    assert resp.status_code == 201
    assert resp.json()["estado"] == "esperando"


def test_iframe_estados(tmp_path):
    """S3: el calendario del iframe necesita la lista de estados con otra
    autorización (embed key, no JWT)."""
    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        iframe_key="k123",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as test_client:
        resp = test_client.get("/iframe/estados.json?key=k123")
        assert resp.status_code == 200
        assert {row["slug"] for row in resp.json()} >= {
            "pendiente", "confirmada", "cancelada"
        }


def test_iframe_estados_requiere_key(tmp_path):
    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        iframe_key="k123",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/iframe/estados.json").status_code == 403


def test_mensajeria_respeta_envia_recordatorio(app_ytablas):
    """S4: el motor de recordatorios ya no consulta `estado != "cancelada"`,
    ahora se fija en envia_recordatorio del estado de cada reserva."""
    app, test_client = app_ytablas
    _seed_estado(app, "Esperando", "esperando", envia_recordatorio=True)
    _seed_estado(app, "Aviso", "aviso", envia_recordatorio=False)
    with app.state.sessionmaker() as session:
        from app.models import Reserva
        reservas = [
            Reserva(nombre="SíEnvía", telefono="+5493512600001",
                    fecha=date(2026, 3, 23), hora=time(20, 0), personas=2,
                    estado="esperando"),
            Reserva(nombre="NoEnvía", telefono="+5493512600002",
                    fecha=date(2026, 3, 23), hora=time(21, 0), personas=2,
                    estado="aviso"),
        ]
        session.add_all(reservas)
        session.commit()
    from app.mensajeria import reservas_para_confirmar
    with app.state.sessionmaker() as session:
        elegidas = reservas_para_confirmar(session, date(2026, 3, 21))
    assert [r.nombre for r in elegidas] == ["SíEnvía"]


# --- T4: CRUD de estados + sección Tags en Ajustes ---

def test_crud_crear_estado(client):
    resp = client.post("/api/estados", json={"nombre": "Esperando"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "esperando"
    assert body["envia_recordatorio"] is True
    assert body["es_base"] is False
    assert body["etiqueta_chatwoot"] is None


def test_crud_crear_con_etiqueta_y_envio_false(client):
    resp = client.post(
        "/api/estados",
        json={"nombre": "Lista de espera", "etiqueta_chatwoot": "lista-espera",
              "envia_recordatorio": False},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["etiqueta_chatwoot"] == "lista-espera"
    assert body["envia_recordatorio"] is False


def test_crud_crear_rejecta_vacio_y_largo(client):
    assert client.post("/api/estados", json={"nombre": "   "}).status_code == 422
    assert client.post("/api/estados", json={"nombre": "x" * 61}).status_code == 422


def test_crud_crear_duplicado_mismo_slug(client):
    assert client.post("/api/estados", json={"nombre": "CanceladA"}).status_code == 409


def test_crud_editar_estado_mapeo(client):
    resp = client.patch(
        "/api/estados/cancelada",
        json={"etiqueta_chatwoot": "reserva-cancelada", "envia_recordatorio": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["etiqueta_chatwoot"] == "reserva-cancelada"
    assert body["envia_recordatorio"] is True


def test_crud_editar_no_permite_cambiar_slug(client):
    resp = client.patch("/api/estados/cancelada", json={"slug": "otra-cosa"})
    assert resp.status_code == 422


def test_crud_eliminar_estado_no_base(client):
    client.post("/api/estados", json={"nombre": "Temporal"})
    resp = client.delete("/api/estados/temporal")
    assert resp.status_code in (200, 204)
    slugs = {r["slug"] for r in client.get("/api/estados").json()}
    assert "temporal" not in slugs


def test_crud_eliminar_base_rechazada(client):
    resp = client.delete("/api/estados/confirmada")
    assert resp.status_code == 409
    assert "base" in resp.text


def test_crud_eliminar_con_reservas_rechazado(client):
    client.post("/api/estados", json={"nombre": "Temporal"})
    created = client.post("/api/reservas", json={
        "nombre": "Juan", "telefono": "+5493512602003",
        "fecha": "2026-03-21", "hora": "20:00", "personas": 2, "estado": "temporal",
    })
    assert created.status_code == 201
    resp = client.delete("/api/estados/temporal")
    assert resp.status_code == 409
    assert "reserva" in resp.text.lower()


def test_crud_requiere_auth(anon_client):
    assert anon_client.post("/api/estados", json={"nombre": "X"}).status_code in (401, 403)
    assert anon_client.delete("/api/estados/pendiente").status_code in (401, 403)


# --- T4: sección Tags en el dashboard ---

def _login_page(test_client):
    response = test_client.post(
        "/login",
        data={"username": "valen", "password": TEST_USERS_PASSWORD.split(":")[1]},
        follow_redirects=False,
    )
    assert response.status_code == 303


TEST_USERS_PASSWORD = "valen:testpass123"


def test_ajustes_tags_page_lista_estados(tmp_path):
    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users=TEST_USERS_PASSWORD,
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as test_client:
        _login_page(test_client)
        resp = test_client.get("/ajustes/tags")
    assert resp.status_code == 200
    for marker in ("Pendiente", "Confirmada", "Cancelada", "reserva"):
        assert marker in resp.text, marker


def test_etiqueta_confirmada_fuera_de_ajustes(client):
    """S-switch: the confirmed-label section leaves /ajustes (it now lives
    in the Tags page as `confirmada`.etiqueta_chatwoot)."""
    resp = client.get("/ajustes")
    assert 'name="etiqueta_confirmada"' not in resp.text


def test_post_ajustes_ignora_campo_etiqueta_viejo(client):
    """Legacy POSTs keep working: the removed field is ignored, the rest
    of the settings still persist."""
    app = client.app
    data = {
        "hora_confirmacion": "10:30", "hora_invitados": "18:45",
        "plantilla_confirmacion": "conf_losco",
        "plantilla_invitados": "invitados_losco",
        "etiqueta_confirmada": "No debería persistir",
    }
    resp = client.post("/ajustes", data=data, follow_redirects=False)
    assert resp.status_code == 303
    with app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.plantilla_confirmacion == "conf_losco"
        assert config.etiqueta_confirmada == "Reserva confirmada"

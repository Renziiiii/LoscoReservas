"""Casos de prueba — T1, odd/tasks/tags-estados-sync.md

Cubre S3/S4 del doc de feature:
- tabla configurada de estados (semilla en arranque): pendiente, confirmada, cancelada;
- S4: cada estado expone `etiqueta_chatwoot` (mapping opcional) y `envia_recordatorio`;
- S3: validación dinámica de estado contra la tabla (ya no el Literal fijo de schemas);
- GET /api/estados (protegido) y GET /iframe/estados.json (embed key) para el front;
- regla S4 mensajería: reserva con estado sin `envia_recordatorio` no recibe envíos.
"""

from datetime import date, time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Configuracion, EstadoReserva, Reserva

VALID = dict(nombre="A", telefono="+5493210987654", fecha="2026-03-21",
             hora="20:00", personas=2)


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


def test_mensajeria_gatea_por_tag_disparadora_no_por_flag(app_ytablas):
    """S2/S6 (tags-panel-v2): la regla vieja del flag `envia_recordatorio`
    quedó reemplazada — el envío lo decide la tag disparadora (la cubre
    tests/test_tags_panel_v2.py). Este caso queda como defensa: un estado
    sin la tag mapeada no envía aunque su flag sea True."""
    app, test_client = app_ytablas
    _seed_estado(app, "Esperando", "esperando", envia_recordatorio=True)
    with app.state.sessionmaker() as session:
        reservas = [
            Reserva(nombre="SíEnvía", telefono="+5493512600001",
                    fecha=date(2026, 3, 23), hora=time(20, 0), personas=2,
                    estado="confirmada"),
            Reserva(nombre="NoEnvía", telefono="+5493512600002",
                    fecha=date(2026, 3, 23), hora=time(21, 0), personas=2,
                    estado="esperando"),
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


def test_crud_eliminar_base_habilitada_sin_reservas(client):
    """S5/D2 (tags-panel-v2): los estados base son solo un template — todos
    eliminables cuando ninguna reserva los usa (el guard de reservas en uso
    queda pinneado por test_crud_eliminar_con_reservas_rechazado)."""
    resp = client.delete("/api/estados/confirmada")
    assert resp.status_code in (200, 204)
    slugs = {r["slug"] for r in client.get("/api/estados").json()}
    assert "confirmada" not in slugs


def test_crud_eliminar_con_reservas_rechazado(client):
    client.post("/api/estados", json={"nombre": "Temporal"})
    created = client.post("/api/reservas", json={
        "nombre": "Juan", "telefono": "+5493210987654",
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
        "tag_disparadora": "reserva-confirmada",
    }
    resp = client.post("/ajustes", data=data, follow_redirects=False)
    assert resp.status_code == 303
    with app.state.sessionmaker() as session:
        config = session.get(Configuracion, 1)
        assert config.plantilla_confirmacion == "conf_losco"
        assert config.etiqueta_confirmada == "Reserva confirmada"


# --- colores personalizables de los estados (tags-color) ---

import re as _re

_COLORES_BASE = {
    "pendiente": "#f0a500",
    "confirmada": "#00b877",
    "cancelada": "#e54666",
}


def test_seeds_base_tienen_color_fijo(client):
    """S1: los 3 estados base siembran con su color de siempre (ámbar,
    verde, rojo) — ya no dependen del CSS del cliente."""
    estados = {row["slug"]: row for row in client.get("/api/estados").json()}
    for slug, color in _COLORES_BASE.items():
        assert estados[slug]["color"] == color, slug


def test_backfill_colores_null(tmp_path):
    """S2: boot con datos existentes — base con color NULL recupera su color
    de siempre; custom NULL recibe el color de la paleta por hash (el mismo
    que el cliente ya mostraba). Idempotente y nunca pisa un color guardado."""
    from app.estados import backfill_colores
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app):
        with app.state.sessionmaker() as session:
            session.add(EstadoReserva(nombre="Demorada", slug="demorada"))
            session.commit()
        with app.state.sessionmaker() as session:
            session.query(EstadoReserva).update({"color": None})
            session.commit()

        backfill_colores(app.state.engine)
        with app.state.sessionmaker() as session:
            colores = {e.slug: e.color for e in session.query(EstadoReserva)}
        assert colores["pendiente"] == "#f0a500"
        assert colores["confirmada"] == "#00b877"
        assert colores["cancelada"] == "#e54666"
        assert colores["demorada"] == "#ec4899"  # paleta[hash('demorada')%6]

        # idempotente y no pisa colores guardados
        with app.state.sessionmaker() as session:
            session.query(EstadoReserva).filter_by(slug="pendiente").update({"color": "#123abc"})
            session.commit()
        backfill_colores(app.state.engine)
        with app.state.sessionmaker() as session:
            colores = {e.slug: e.color for e in session.query(EstadoReserva)}
        assert colores["pendiente"] == "#123abc"
        assert colores["demorada"] == "#ec4899"


def test_crud_crear_sin_color_asigna_de_la_paleta(client):
    """S3: crear un estado sin color le asigna uno de la paleta (hex válido).
    Dos estados nuevos no comparten color mientras queden libres."""
    uno = client.post("/api/estados", json={"nombre": "A Reconfirmar"})
    dos = client.post("/api/estados", json={"nombre": "En Sala"})
    assert uno.status_code == 201 and dos.status_code == 201
    for body in (uno.json(), dos.json()):
        assert body["color"] and _re.fullmatch(r"#[0-9a-f]{6}", body["color"])
    assert uno.json()["color"] != dos.json()["color"]


def test_crud_crear_con_color_explicito(client):
    resp = client.post("/api/estados", json={"nombre": "VIP", "color": "#AABBCC"})
    assert resp.status_code == 201
    assert resp.json()["color"] == "#aabbcc"  # normalizado a minúsculas


def test_crud_crear_color_invalido_rechazado(client):
    resp = client.post("/api/estados", json={"nombre": "Raro", "color": "javascript:alert(1)"})
    assert resp.status_code == 422


def test_crud_editar_color(client):
    resp = client.patch("/api/estados/cancelada", json={"color": "#0000ff"})
    assert resp.status_code == 200
    assert resp.json()["color"] == "#0000ff"
    resp2 = client.patch("/api/estados/cancelada", json={"color": "no-es-un-color"})
    assert resp2.status_code == 422

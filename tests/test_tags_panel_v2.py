"""Casos de prueba — T1, odd/tasks/tags-panel-v2.md

Cubre S2/S6 del doc de feature (D1: select como el de T15, gate LOCAL por
estado):
- `Configuracion.tag_disparadora` existe (migración idempotente + seed) y es
  editable por panel/form y JSON API;
- el motor de recordatorios SOLE envía a reservas cuyo estado tiene mapeada
  la tag disparadora (`EstadoReserva.etiqueta_chatwoot == tag_disparadora`);
- sin ningún estado mapeado a esa tag NO se envía nada;
- el viejo flag `envia_recordatorio` del estado queda FUERA de la decisión.
"""

import re
from datetime import date, time

from app.models import EstadoReserva, Reserva

HOY = date(2026, 3, 21)


def _mapear_estado(app, slug, etiqueta):
    """Mapea la etiqueta de Chatwoot sobre un estado base ya semillado."""
    with app.state.sessionmaker() as session:
        estado = session.query(EstadoReserva).filter_by(slug=slug).one()
        estado.etiqueta_chatwoot = etiqueta
        session.commit()


def _seed_estado(app, nombre, slug, etiqueta=None):
    with app.state.sessionmaker() as session:
        session.add(EstadoReserva(nombre=nombre, slug=slug,
                                  etiqueta_chatwoot=etiqueta))
        session.commit()


def _seed_reserva(app, nombre, estado, telefono="+5493512600001"):
    with app.state.sessionmaker() as session:
        session.add(Reserva(nombre=nombre, telefono=telefono,
                            fecha=date(2026, 3, 23), hora=time(20, 0),
                            personas=2, estado=estado))
        session.commit()


def _set_tag_disparadora(app, tag):
    with app.state.sessionmaker() as session:
        from app.models import Configuracion
        config = session.get(Configuracion, 1)
        config.tag_disparadora = tag
        session.commit()


def test_config_tag_disparadora_default(client):
    """S2: la config expone tag_disparadora semillada con el default de T15."""
    body = client.get("/api/ajustes").json()
    assert body["tag_disparadora"] == "Reserva confirmada"


def test_ajustes_post_persiste_tag_disparadora(client):
    """S2/D1: el form del panel persiste la tag disparadora elegida."""
    resp = client.post(
        "/ajustes",
        data={
            "hora_confirmacion": "09:00",
            "hora_invitados": "17:15",
            "plantilla_confirmacion": "reserva_confirmacion",
            "plantilla_invitados": "invitados_confirmacion",
            "tag_disparadora": "reserva-confirmada",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert client.get("/api/ajustes").json()["tag_disparadora"] == "reserva-confirmada"


def test_gate_solo_estado_mapeado_a_tag_disparadora(app_ytablas):
    """S2: el disparador es el ESTADO cuya etiqueta == tag_disparadora."""
    app, _ = app_ytablas
    _mapear_estado(app, "confirmada", "reserva-confirmada")
    _seed_estado(app, "Demorada", "demorada", etiqueta=None)
    _set_tag_disparadora(app, "reserva-confirmada")
    _seed_reserva(app, "SíEnvía", "confirmada")
    _seed_reserva(app, "NoEnvía", "demorada")
    from app.mensajeria import reservas_para_confirmar
    with app.state.sessionmaker() as session:
        elegidas = reservas_para_confirmar(session, HOY)
    assert [r.nombre for r in elegidas] == ["SíEnvía"]


def test_gate_sin_estado_mapeado_no_envia(app_ytablas):
    """S2: tag elegida sin estado mapeado → no se envía nada."""
    app, _ = app_ytablas
    _mapear_estado(app, "pendiente", None)
    _set_tag_disparadora(app, "reserva-confirmada")
    _seed_reserva(app, "Nadie", "pendiente")
    from app.mensajeria import reservas_para_confirmar
    with app.state.sessionmaker() as session:
        assert reservas_para_confirmar(session, HOY) == []


def test_gate_ignora_flag_envia_recordatorio(app_ytablas):
    """S6: el flag viejo por estado ya no decide; la tag disparadora sí."""
    app, _ = app_ytablas
    _mapear_estado(app, "confirmada", "reserva-confirmada")
    with app.state.sessionmaker() as session:
        estado = session.query(EstadoReserva).filter_by(slug="confirmada").one()
        estado.envia_recordatorio = False  # legacy: antes bloqueaba el envío
        session.commit()
    _set_tag_disparadora(app, "reserva-confirmada")
    _seed_reserva(app, "SíEnvíaIgual", "confirmada")
    from app.mensajeria import reservas_para_confirmar
    with app.state.sessionmaker() as session:
        elegidas = reservas_para_confirmar(session, HOY)
    assert [r.nombre for r in elegidas] == ["SíEnvíaIgual"]


# --- T2: rework de la página Tags (S3-S5, S7) ---


def test_guardar_batch_persiste_ediciones_y_nuevos(client):
    """S3: UN solo botón Guardar al final persiste TODAS las ediciones.
    S4: la etiqueta viaja como select (mismo campo etiqueta_<slug>)."""
    resp = client.post(
        "/ajustes/tags/guardar",
        data={
            "nombre_confirmada": "Confirmada nueva",
            "etiqueta_confirmada": "reserva-confirmada",
            "nombre_pendiente": "Pendiente",
            "etiqueta_pendiente": "",
            "nombre_cancelada": "Cancelada",
            "etiqueta_cancelada": "",
            "nuevo_nombre": ["Demorada", "Lista de espera"],
            "nuevo_etiqueta": ["demorada-tag", ""],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    estados = {row["slug"]: row for row in client.get("/api/estados").json()}
    assert estados["confirmada"]["nombre"] == "Confirmada nueva"
    assert estados["confirmada"]["etiqueta_chatwoot"] == "reserva-confirmada"
    assert estados["pendiente"]["etiqueta_chatwoot"] is None
    assert estados["demorada"]["etiqueta_chatwoot"] == "demorada-tag"
    assert "lista-de-espera" in estados


def test_guardar_batch_rechaza_nombre_vacio_sin_persistir(client):
    resp = client.post(
        "/ajustes/tags/guardar",
        data={"nombre_confirmada": "", "etiqueta_confirmada": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]
    estados = {row["slug"]: row for row in client.get("/api/estados").json()}
    assert estados["confirmada"]["nombre"] == "Confirmada"  # sin cambio


def test_eliminar_estado_base_habilitado(client):
    """S5/D2: los 3 base son solo un template — todos eliminables si
    ninguna reserva los usa."""
    resp = client.post("/ajustes/tags/cancelada/eliminar", follow_redirects=False)
    assert resp.status_code == 303
    slugs = [row["slug"] for row in client.get("/api/estados").json()]
    assert "cancelada" not in slugs


def test_eliminar_estado_base_en_uso_bloqueado(client):
    """S5: el guard de reservas en uso se conserva para los base también."""
    crear = client.post(
        "/api/reservas",
        json={"nombre": "B", "telefono": "+5493210987654",
              "fecha": "2026-03-21", "hora": "20:00", "personas": 2,
              "estado": "cancelada"},
    )
    assert crear.status_code in (200, 201)
    resp = client.post("/ajustes/tags/cancelada/eliminar", follow_redirects=False)
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]
    slugs = [row["slug"] for row in client.get("/api/estados").json()]
    assert "cancelada" in slugs


def test_default_de_reserva_cae_al_primer_estado_restante(client):
    """S5: con 'pendiente' eliminada, una reserva sin estado explícito no
    se rompe: cae al primer estado configurado (menor id)."""
    assert client.post("/ajustes/tags/pendiente/eliminar", follow_redirects=False).status_code == 303
    crear = client.post(
        "/api/reservas",
        json={"nombre": "C", "telefono": "+5493210987654",
              "fecha": "2026-03-21", "hora": "20:00", "personas": 2},
    )
    assert crear.status_code in (200, 201)
    assert crear.json()["estado"] == "confirmada"


def test_tags_page_un_form_un_boton_eliminar_por_tarjeta(client):
    """S3/S4/S5/S7: un solo form de guardado, select de etiquetas y botón
    eliminar también en las tarjetas base."""
    html = client.get("/ajustes/tags").text
    assert html.count('action="/ajustes/tags/guardar"') == 1
    assert 'formaction="/ajustes/tags/confirmada/eliminar"' in html
    assert 'formaction="/ajustes/tags/pendiente/eliminar"' in html
    # design-tokens S5: el select lleva utilities además del hook JS.
    assert re.search(r'<select[^>]*class="[^"]*sel-etiqueta', html)
    assert 'name="nombre_confirmada"' in html

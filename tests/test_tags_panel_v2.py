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

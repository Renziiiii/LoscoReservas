"""T5 scheduler wiring tests (S8): hour parsing and cron job configuration."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.scheduler import build_scheduler, parse_hora


def test_parse_hora():
    assert parse_hora("09:00") == (9, 0)
    assert parse_hora("17:15") == (17, 15)


def test_parse_hora_rejects_garbage():
    for bad in ("", "9", "25:00", "09:99", "mañana"):
        try:
            parse_hora(bad)
        except ValueError:
            continue
        raise AssertionError(f"parse_hora({bad!r}) should have raised")


def test_build_scheduler_registers_two_jobs_with_configured_times(tmp_path):
    """Hours now come from the DB configuration (L19), seeded from env."""
    from app.ajustes import obtener_configuracion
    from app.models import Base

    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        hora_confirmacion="09:00",
        hora_invitados="17:15",
        _env_file=None,
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessionmaker() as session:
        obtener_configuracion(session, settings)
    scheduler = build_scheduler(app, settings)
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {"confirmaciones_t48h", "recordatorio_invitados"}
    assert str(jobs["confirmaciones_t48h"].trigger) == "cron[hour='9', minute='0']"
    assert str(jobs["recordatorio_invitados"].trigger) == "cron[hour='17', minute='15']"
    # build_scheduler() only registers jobs; start()/shutdown() belong to the
    # app lifespan, so there is nothing to tear down here.


def test_app_boots_and_shuts_down_cleanly_with_scheduler_enabled(tmp_path):
    """Pins the nonlocal-scheduler regression: the lifespan must start and
    stop the scheduler without UnboundLocalError when scheduler_enabled=True."""
    settings = Settings(
        database_path=tmp_path / "reservas.db",
        users="valen:testpass123",
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        scheduler_enabled=True,
        _env_file=None,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

"""Daily cron wiring (S8: "Un cron que cada dia, verifica a un horario
especifico que corra un script para enviarle msg de wsp").

Two jobs, both idempotent (see app.mensajeria):
- confirmaciones (hora configurable en el panel, /ajustes): T-48h reservations.
- invitados (hora configurable en el panel, /ajustes): same-day guest-count
  confirmations, inside Valen's 17:00-18:00 window.

L19: hours and template names come from the DB (Configuracion row), edited
in the admin panel — never from env at run time. reprogramar() re-registers
the jobs so a panel save takes effect without a restart.

The scheduler runs in-process (APScheduler BackgroundScheduler) so the whole
app stays a single container on the 1-core deploy server.
"""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.ajustes import obtener_configuracion
from app.chatwoot import ChatwootClient
from app.config import Settings
from app.mensajeria import enviar_confirmaciones, enviar_recordatorios_invitados

logger = logging.getLogger("losco.scheduler")


def parse_hora(hhmm: str) -> tuple[int, int]:
    """"09:00" -> (9, 0). Raises ValueError on garbage."""
    hour_text, sep, minute_text = hhmm.strip().partition(":")
    if not sep or not hour_text or not minute_text:
        raise ValueError(f"hora mal formada (se espera HH:MM): {hhmm!r}")
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"hora fuera de rango: {hhmm}")
    return hour, minute


def _job_confirmaciones(app):
    def run():
        settings: Settings = app.state.settings
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url, settings.chatwoot_api_token, settings.chatwoot_account_id
        )
        with app.state.sessionmaker() as session:
            config = obtener_configuracion(session, settings)
            report = enviar_confirmaciones(
                session, chatwoot, date.today(), config.plantilla_confirmacion
            )
        logger.info("confirmaciones T-48h: %s", report)

    return run


def _job_invitados(app):
    def run():
        settings: Settings = app.state.settings
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url, settings.chatwoot_api_token, settings.chatwoot_account_id
        )
        with app.state.sessionmaker() as session:
            config = obtener_configuracion(session, settings)
            report = enviar_recordatorios_invitados(
                session, chatwoot, date.today(), config.plantilla_invitados
            )
        logger.info("recordatorios de invitados: %s", report)

    return run


JOB_CONFIRMACIONES = "confirmaciones_t48h"
JOB_INVITADOS = "recordatorio_invitados"


def reprogramar(scheduler: BackgroundScheduler, app, settings: Settings) -> None:
    """(Re)register both cron jobs with the hours configured in the DB.

    Safe to call on a fresh or running scheduler: jobs are replaced in place
    (replace_existing=True), so a /ajustes save takes effect immediately."""
    with app.state.sessionmaker() as session:
        config = obtener_configuracion(session, settings)
        hora_conf = parse_hora(config.hora_confirmacion)
        hora_inv = parse_hora(config.hora_invitados)
    scheduler.add_job(
        _job_confirmaciones(app),
        CronTrigger(hour=hora_conf[0], minute=hora_conf[1], timezone=settings.tz),
        id=JOB_CONFIRMACIONES,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_invitados(app),
        CronTrigger(hour=hora_inv[0], minute=hora_inv[1], timezone=settings.tz),
        id=JOB_INVITADOS,
        replace_existing=True,
    )


def build_scheduler(app, settings: Settings) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.tz)
    reprogramar(scheduler, app, settings)
    return scheduler

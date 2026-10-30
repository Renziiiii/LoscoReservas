"""Daily cron wiring (S8: "Un cron que cada dia, verifica a un horario
especifico que corra un script para enviarle msg de wsp").

Two jobs, both idempotent (see app.mensajeria):
- 09:00 (configurable): T-48h reservation confirmations.
- 17:15 (configurable): same-day guest-count confirmations, inside Valen's
  17:00-18:00 window.

The scheduler runs in-process (APScheduler BackgroundScheduler) so the whole
app stays a single container on the 1-core deploy server.
"""

import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
            report = enviar_confirmaciones(
                session, chatwoot, date.today(), settings.template_confirmacion
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
            report = enviar_recordatorios_invitados(
                session, chatwoot, date.today(), settings.template_invitados
            )
        logger.info("recordatorios de invitados: %s", report)

    return run


def build_scheduler(app, settings: Settings) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.tz)
    h_conf, m_conf = parse_hora(settings.hora_confirmacion)
    h_inv, m_inv = parse_hora(settings.hora_invitados)
    scheduler.add_job(
        _job_confirmaciones(app),
        CronTrigger(hour=h_conf, minute=m_conf, timezone=settings.tz),
        id="confirmaciones_t48h",
    )
    scheduler.add_job(
        _job_invitados(app),
        CronTrigger(hour=h_inv, minute=m_inv, timezone=settings.tz),
        id="recordatorio_invitados",
    )
    return scheduler

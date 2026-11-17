"""Embeddable per-phone reservations page (S4): shown inside Chatwoot.

Chatwoot's mechanism for custom apps in the conversation view is
"Dashboard Apps" (Settings → Integrations → Dashboard Apps, issue #1587 /
PR #4761): the configured URL is STATIC — Chatwoot does not template the
phone into it. Instead the page receives the conversation/contact context
as a window event ("appContext") and must fetch its own data.

So this module serves three endpoints, all authorized by the embed key
(iframe_key from env, fail-closed when unset — the iframe cannot use our
cookie login):

- GET /iframe/app?key=K      — static shell configured in Chatwoot; listens
  for the appContext event, extracts contact.phone_number and fetches data.
- GET /iframe/reservas.json  — JSON data for one phone (client-side fetch).
- GET /iframe/reservas       — human-viewable HTML page for the same data
  (direct link / debugging / manual use).

Phones are matched by digits so Chatwoot's E.164 format and our stored
punctuation agree. The exact appContext payload shape is verified live
against the real Chatwoot in T7 (see docs/iframe-chatwoot.md).
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ajustes import obtener_configuracion
from app.config import Settings
from app.contactos import sincronizar_telefono_contacto
from app.db import get_session
from app.etiquetas import etiquetar_confirmada
from app.models import Reserva
from app.schemas import ReservaCreateIframe, ReservaUpdateIframe

router = APIRouter()

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _digitos(telefono: str) -> str:
    return re.sub(r"\D", "", telefono or "")


def _frame_ancestors(settings: Settings) -> str:
    origins = " ".join(o.strip() for o in settings.iframe_origins.split(",") if o.strip())
    if not origins:
        origins = "'none'"
    return f"frame-ancestors {origins}"


def _autorizado(request: Request, key: str) -> bool:
    settings: Settings = request.app.state.settings
    return bool(settings.iframe_key) and key == settings.iframe_key


def _por_telefono(session: Session, tel: str) -> list[Reserva]:
    """Phone match by digits: Chatwoot sends E.164, stored phones keep
    their original punctuation. The table is small (one bar), so a
    Python-side filter over the ordered list is fine."""
    digitos = _digitos(tel)
    return [
        r
        for r in session.scalars(select(Reserva).order_by(Reserva.fecha, Reserva.hora))
        if _digitos(r.telefono) == digitos
    ]


@router.get("/iframe/app", response_class=HTMLResponse, include_in_schema=False)
def iframe_app(request: Request, key: str = Query(default="")):
    """Static shell configured as a Chatwoot Dashboard App URL."""
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return HTMLResponse("Forbidden", status_code=403, headers=headers)
    return _templates.TemplateResponse(
        request, "iframe_app.html", {}, headers=headers
    )


@router.get("/iframe/calendario", response_class=HTMLResponse, include_in_schema=False)
def iframe_calendario(request: Request, key: str = Query(default="")):
    """Weekly calendar view for the Chatwoot Dashboard App (spike of the
    client's request: dashboard calendar mirrored in the conversation view,
    with drag to reschedule). Serves the prebuilt Svelte bundle from
    app/static/calendar/iframe.html; the page reads the embed key from its
    own query string and the contact from the appContext window event."""
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return HTMLResponse("Forbidden", status_code=403, headers=headers)
    html_path = Path(__file__).parent / "static" / "calendar" / "iframe.html"
    if not html_path.exists():
        return HTMLResponse("calendario no construido", status_code=404, headers=headers)
    return HTMLResponse(html_path.read_text(encoding="utf-8"), headers=headers)


def _serialize(reserva: Reserva) -> dict:
    return {
        "id": reserva.id,
        "nombre": reserva.nombre,
        "telefono": reserva.telefono,
        "fecha": reserva.fecha.isoformat(),
        "hora": reserva.hora.strftime("%H:%M"),
        "personas": reserva.personas,
        "estado": reserva.estado,
        "detalles": reserva.detalles,
        "enviar_recordatorio": reserva.enviar_recordatorio,
    }


@router.post("/iframe/reservas.json", status_code=201, include_in_schema=False)
def iframe_reservas_create(
    request: Request,
    payload: ReservaCreateIframe,
    key: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Register a reservation from the Chatwoot tab (T9/L15, S11/L46).

    Same schema/validation as the CRUD API, authorized by the embed key
    instead of a JWT (the iframe cannot do cookie/token login). The form
    in /iframe/app prefills nombre/telefono from the appContext contact.
    When the contact has no phone (Instagram chats) the agent annotates
    the number they asked for; `contacto_chatwoot_id` then syncs that
    number to the Chatwoot contact (only when empty, fail-soft) BEFORE
    labeling, so the label lands on the chat being managed and messaging
    finds one single contact for the phone.
    """
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return JSONResponse({"detail": "forbidden"}, status_code=403, headers=headers)
    reserva = Reserva(**payload.model_dump(exclude={"contacto_chatwoot_id"}))
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    config = obtener_configuracion(session, settings)
    if payload.contacto_chatwoot_id:
        sincronizar_telefono_contacto(
            settings, payload.contacto_chatwoot_id, reserva.telefono
        )
    etiquetar_confirmada(settings, reserva, config.etiqueta_confirmada)
    return JSONResponse(_serialize(reserva), status_code=201, headers=headers)


@router.patch("/iframe/reservas/{reserva_id}.json", include_in_schema=False)
def iframe_reservas_patch(
    request: Request,
    reserva_id: int,
    payload: ReservaUpdateIframe,
    key: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Partial edit of a reservation from the Chatwoot tab (T10/L16).

    When the edit changes the phone, the Chatwoot contact sync runs with
    the new number (S11/L46: only if the contact has none, fail-soft).
    """
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return JSONResponse({"detail": "forbidden"}, status_code=403, headers=headers)
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return JSONResponse({"detail": "not found"}, status_code=404, headers=headers)
    datos = payload.model_dump(exclude_unset=True)
    contacto_chatwoot_id = datos.pop("contacto_chatwoot_id", None)
    for field, value in datos.items():
        setattr(reserva, field, value)
    session.commit()
    session.refresh(reserva)
    config = obtener_configuracion(session, settings)
    if "telefono" in datos and contacto_chatwoot_id:
        sincronizar_telefono_contacto(
            settings, contacto_chatwoot_id, reserva.telefono
        )
    etiquetar_confirmada(settings, reserva, config.etiqueta_confirmada)
    return JSONResponse(_serialize(reserva), status_code=200, headers=headers)


@router.delete("/iframe/reservas/{reserva_id}.json", include_in_schema=False)
def iframe_reservas_delete(
    request: Request,
    reserva_id: int,
    key: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Delete a reservation from the Chatwoot tab (T10/L16)."""
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return JSONResponse({"detail": "forbidden"}, status_code=403, headers=headers)
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return JSONResponse({"detail": "not found"}, status_code=404, headers=headers)
    session.delete(reserva)
    session.commit()
    return Response(status_code=204, headers=headers)


@router.get("/iframe/reservas.json", include_in_schema=False)
def iframe_reservas_json(
    request: Request,
    tel: str = Query(min_length=8),
    todas: bool = Query(default=False),
    key: str = Query(default=""),
    session: Session = Depends(get_session),
):
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return JSONResponse({"detail": "forbidden"}, status_code=403, headers=headers)
    if todas:
        # L61: the calendar shows every reservation so the agent sees the
        # bar's availability, not just this contact's bookings.
        reservas = list(session.scalars(select(Reserva).order_by(Reserva.fecha, Reserva.hora)))
    else:
        reservas = _por_telefono(session, tel)
    return JSONResponse(
        [_serialize(r) for r in reservas],
        headers=headers,
    )


@router.get("/iframe/reservas", response_class=HTMLResponse, include_in_schema=False)
def iframe_reservas(
    request: Request,
    tel: str = Query(min_length=8),
    key: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Human-viewable HTML page with the same data (direct link / debug)."""
    settings: Settings = request.app.state.settings
    headers = {"Content-Security-Policy": _frame_ancestors(settings)}
    if not _autorizado(request, key):
        return HTMLResponse("Forbidden", status_code=403, headers=headers)
    return _templates.TemplateResponse(
        request,
        "iframe.html",
        {"reservas": _por_telefono(session, tel)},
        headers=headers,
    )

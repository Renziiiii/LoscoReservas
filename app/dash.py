"""Admin dashboard: server-rendered Jinja2 + HTMX (S5).

Public router: login/logout. Protected router: everything else.
Forms work as plain HTTP (no JS needed); HTMX only enhances delete buttons
and swap-in-place behavior via the HX-Request header.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    create_access_token,
    get_current_user as _api_current_user,
    oauth2_scheme,
)
from app.db import get_session
from app.models import Reserva
from app.schemas import ReservaCreate

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SESSION_COOKIE = "losco_session"
ESTADOS = ["pendiente", "confirmada", "cancelada"]

def _page_user(
    request: Request, token: str | None = Depends(oauth2_scheme)
) -> str:
    """Page auth: like the API dependency, but redirects to /login instead of
    answering 401 JSON."""
    try:
        return _api_current_user(request, token)
    except HTTPException:
        raise HTTPException(status_code=303, headers={"Location": "/login"}) from None


public_router = APIRouter(include_in_schema=False)
router = APIRouter(include_in_schema=False, dependencies=[Depends(_page_user)])


def _not_found() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


async def _form_dict(request: Request) -> dict:
    """Parsed form body as a plain dict (login-independent create/update forms)."""
    return dict(await request.form())


# --- login / logout (browser session) ---


@public_router.get("/login")
def login_page(request: Request):
    if request.cookies.get(SESSION_COOKIE):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@public_router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    settings = request.app.state.settings
    if not authenticate(settings, username, password):
        return templates.TemplateResponse(request, "login.html", {"error": "Credenciales inválidas"})
    token = create_access_token(settings, username)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=settings.jwt_expire_minutes * 60
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- dashboard ---


def _query_reservas(session: Session, estado: str | None, fecha: str | None) -> list[Reserva]:
    query = select(Reserva).order_by(Reserva.fecha, Reserva.hora)
    if estado:
        query = query.where(Reserva.estado == estado)
    if fecha:
        query = query.where(Reserva.fecha == fecha)
    return list(session.scalars(query))


def _render_form(request: Request, reserva: Reserva | None, errors: dict | None = None, values: dict | None = None):
    return templates.TemplateResponse(
        request,
        "reserva_form.html",
        {"reserva": reserva, "errors": errors or {}, "values": values or {}, "estados": ESTADOS},
    )


@router.get("/")
def index(request: Request, estado: str = None, fecha: str = None, session: Session = Depends(get_session)):
    reservas = _query_reservas(session, estado, fecha)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"reservas": reservas, "estado": estado, "fecha": fecha, "estados": ESTADOS},
    )


@router.get("/nuevo")
def nueva_reserva(request: Request):
    return _render_form(request, reserva=None)


@router.get("/reservas/{reserva_id}/editar")
def editar_reserva(request: Request, reserva_id: int, session: Session = Depends(get_session)):
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return _not_found()
    return _render_form(request, reserva=reserva)


def _parse_form(form) -> tuple[ReservaCreate | None, dict, dict]:
    """Returns (parsed_payload_or_None, field_errors, raw_values)."""
    raw = {key: form.get(key) for key in ("nombre", "telefono", "fecha", "hora", "personas", "estado", "detalles")}
    raw["enviar_recordatorio"] = "enviar_recordatorio" in form
    try:
        return ReservaCreate(**raw), {}, raw
    except ValidationError as exc:
        errors = {str(err["loc"][0]): err["msg"] for err in exc.errors()}
        return None, errors, raw


@router.post("/reservas")
def crear_reserva(request: Request, form: dict = Depends(_form_dict), session: Session = Depends(get_session)):
    payload, errors, values = _parse_form(form)
    if payload is None:
        return _render_form(request, reserva=None, errors=errors, values=values)
    session.add(Reserva(**payload.model_dump()))
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/reservas/{reserva_id}")
def actualizar_reserva(
    request: Request,
    reserva_id: int,
    form: dict = Depends(_form_dict),
    session: Session = Depends(get_session),
):
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return _not_found()
    payload, errors, values = _parse_form(form)
    if payload is None:
        return _render_form(request, reserva=reserva, errors=errors, values=values)
    for field, value in payload.model_dump().items():
        setattr(reserva, field, value)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/reservas/{reserva_id}/eliminar")
def eliminar_reserva(
    request: Request, reserva_id: int, session: Session = Depends(get_session)
):
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return _not_found()
    session.delete(reserva)
    session.commit()
    if request.headers.get("HX-Request"):
        return Response(status_code=200)  # empty swap: row disappears
    return RedirectResponse("/", status_code=303)

"""Admin dashboard: server-rendered Jinja2 + HTMX (S5).

Public router: login/logout. Protected router: everything else.
Forms work as plain HTTP (no JS needed); HTMX only enhances delete buttons
and swap-in-place behavior via the HX-Request header.
"""

from pathlib import Path

import httpx
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
from app.ajustes import obtener_configuracion
from app.chatwoot import ChatwootClient
from app.db import get_session
from app.etiquetas import etiquetar_confirmada, migrar_etiqueta
from app.models import Reserva
from app.schemas import ReservaCreate
from app.scheduler import parse_hora, reprogramar

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
    reserva = Reserva(**payload.model_dump())
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    config = obtener_configuracion(session, request.app.state.settings)
    etiquetar_confirmada(request.app.state.settings, reserva, config.etiqueta_confirmada)
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
    config = obtener_configuracion(session, request.app.state.settings)
    etiquetar_confirmada(request.app.state.settings, reserva, config.etiqueta_confirmada)
    return RedirectResponse("/", status_code=303)


# --- ajustes de mensajería (L19: horas y plantillas en el panel, no env) ---

AJUSTES_CAMPOS = ("hora_confirmacion", "hora_invitados", "plantilla_confirmacion", "plantilla_invitados")


def _render_ajustes(
    request: Request,
    config,
    errors: dict | None = None,
    values: dict | None = None,
    migrados: int | None = None,
    fallas: int | None = None,
):
    return templates.TemplateResponse(
        request,
        "ajustes.html",
        {
            "config": config,
            "errors": errors or {},
            "values": values or {},
            "migrados": migrados,
            "fallas": fallas,
        },
    )


@router.get("/ajustes")
def ajustes_page(
    request: Request,
    migrados: int | None = None,
    fallas: int | None = None,
    session: Session = Depends(get_session),
):
    config = obtener_configuracion(session, request.app.state.settings)
    return _render_ajustes(request, config, migrados=migrados, fallas=fallas)


def _chatwoot_configurado(settings: Settings) -> bool:
    """True when the admin has supplied the three Chatwoot credentials."""
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


@router.get("/ajustes")
def ajustes_page(
    request: Request,
    migrados: int | None = None,
    fallas: int | None = None,
    session: Session = Depends(get_session),
):
    config = obtener_configuracion(session, request.app.state.settings)
    return templates.TemplateResponse(
        request,
        "ajustes.html",
        {
            "config": config,
            "errors": {},
            "values": {},
            "migrados": migrados,
            "fallas": fallas,
        },
    )


@router.post("/ajustes")
def ajustes_submit(
    request: Request,
    hora_confirmacion: str | None = Form(None),
    hora_invitados: str | None = Form(None),
    plantilla_confirmacion: str | None = Form(None),
    plantilla_invitados: str | None = Form(None),
    etiqueta_confirmada: str | None = Form(None),
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    values = {
        "hora_confirmacion": (hora_confirmacion or "").strip(),
        "hora_invitados": (hora_invitados or "").strip(),
        "plantilla_confirmacion": (plantilla_confirmacion or "").strip(),
        "plantilla_invitados": (plantilla_invitados or "").strip(),
        "etiqueta_confirmada": (etiqueta_confirmada or "").strip(),
    }
    errors: dict[str, str] = {}
    for campo in ("hora_confirmacion", "hora_invitados"):
        try:
            parse_hora(values[campo])
        except ValueError:
            errors[campo] = "Hora mal formada: usá HH:MM (ej. 09:00)."
    for campo in ("plantilla_confirmacion", "plantilla_invitados"):
        if not values[campo]:
            errors[campo] = "El nombre de la plantilla es obligatorio."
    if not values["etiqueta_confirmada"]:
        errors["etiqueta_confirmada"] = "El nombre de la etiqueta es obligatorio."
    if errors:
        config = obtener_configuracion(session, settings)
        return _render_ajustes(request, config, errors=errors, values=values)

    config = obtener_configuracion(session, settings)
    vieja = config.etiqueta_confirmada
    config.hora_confirmacion = values["hora_confirmacion"]
    config.hora_invitados = values["hora_invitados"]
    config.plantilla_confirmacion = values["plantilla_confirmacion"]
    config.plantilla_invitados = values["plantilla_invitados"]
    config.etiqueta_confirmada = values["etiqueta_confirmada"]
    session.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        reprogramar(scheduler, request.app.state, settings)

    nueva = values["etiqueta_confirmada"]
    if _chatwoot_configurado(settings) and vieja != nueva:
        try:
            chatwoot = ChatwootClient(
                settings.chatwoot_base_url,
                settings.chatwoot_api_token,
                settings.chatwoot_account_id,
            )
            total = len(chatwoot.contactos_con_etiqueta(vieja))
        except Exception:
            return RedirectResponse("/ajustes", status_code=303)
        if total > 0:
            return templates.TemplateResponse(
                request,
                "migrar_etiqueta.html",
                {"vieja": vieja, "nueva": nueva, "total": total},
            )

    return RedirectResponse("/ajustes", status_code=303)


@router.post("/ajustes/migrar-etiqueta")
def migrar_etiqueta_submit(
    request: Request,
    vieja: str | None = Form(None),
    nueva: str | None = Form(None),
):
    vieja = (vieja or "").strip()
    nueva = (nueva or "").strip()
    if not vieja or not nueva or vieja == nueva:
        return RedirectResponse("/ajustes", status_code=303)

    resultado = migrar_etiqueta(request.app.state.settings, vieja, nueva)
    return RedirectResponse(
        f"/ajustes?migrados={len(resultado['migrados'])}&fallas={len(resultado['fallas'])}",
        status_code=303,
    )


@router.get("/ajustes/plantillas.json")
def ajustes_plantillas(request: Request):
    """WhatsApp templates from the Chatwoot API (L20) for the /ajustes
    datalist. Fail-soft: never breaks the page, always returns a motivo."""
    settings = request.app.state.settings
    if not (
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    ):
        return {
            "plantillas": [],
            "motivo": "Chatwoot no está configurado (faltan URL, token o account id en el entorno). Se puede escribir el nombre de la plantilla a mano.",
        }
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url, settings.chatwoot_api_token, settings.chatwoot_account_id
        )
        plantillas = chatwoot.plantillas_whatsapp()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return {
                "plantillas": [],
                "motivo": (
                    "Chatwoot rechazó las credenciales (401): verificá el token de API "
                    "(Chatwoot → Perfil → Token de acceso) y el account id (el número "
                    "que aparece en la URL de Chatwoot después de /app/accounts/)."
                ),
            }
        return {
            "plantillas": [],
            "motivo": f"Chatwoot respondió con error {exc.response.status_code}: {exc}",
        }
    except Exception as exc:  # transport errors must not break the panel
        return {"plantillas": [], "motivo": f"Chatwoot no respondió: {exc}"}
    if not plantillas:
        return {
            "plantillas": [],
            "motivo": "El inbox de WhatsApp no tiene plantillas sincronizadas. Sincronizalas en Chatwoot (Configuración del inbox) o escribí el nombre a mano.",
        }
    return {
        "plantillas": [
            {
                "name": p.get("name"),
                "status": p.get("status"),
                "category": p.get("category"),
                "language": p.get("language"),
            }
            for p in plantillas
        ],
        "motivo": None,
    }


@router.get("/ajustes/etiquetas.json")
def ajustes_etiquetas(request: Request):
    """Account labels from the Chatwoot API (L24) for the /ajustes selector.
    Fail-soft: never breaks the page, always returns a motivo."""
    settings = request.app.state.settings
    if not _chatwoot_configurado(settings):
        return {
            "etiquetas": [],
            "motivo": "Chatwoot no está configurado (faltan URL, token o account id en el entorno). Se puede escribir el nombre a mano.",
        }
    try:
        chatwoot = ChatwootClient(
            settings.chatwoot_base_url, settings.chatwoot_api_token, settings.chatwoot_account_id
        )
        etiquetas = chatwoot.listar_labels_cuenta()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            return {
                "etiquetas": [],
                "motivo": (
                    "Chatwoot rechazó las credenciales (401): verificá el token de API "
                    "(Chatwoot → Perfil → Token de acceso) y el account id (el número "
                    "que aparece en la URL de Chatwoot después de /app/accounts/)."
                ),
            }
        return {
            "etiquetas": [],
            "motivo": f"Chatwoot respondió con error {exc.response.status_code}: {exc}",
        }
    except Exception as exc:
        return {"etiquetas": [], "motivo": f"Chatwoot no respondió: {exc}"}
    if not etiquetas:
        return {
            "etiquetas": [],
            "motivo": "La cuenta no tiene etiquetas creadas: se puede escribir el nombre a mano.",
        }
    return {
        "etiquetas": [
            {"title": e.get("title"), "color": e.get("color")} for e in etiquetas
        ],
        "motivo": None,
    }


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

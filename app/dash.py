"""Admin dashboard: server-rendered Jinja2 + HTMX (S5), minus the root page:
`/` now serves the FullCalendar SPA (S1, odd/tasks/calendario-en-raiz.md).
Forms work as plain HTTP (no JS needed); HTMX only enhances delete buttons
and swap-in-place behavior via the HX-Request header.
"""

from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    create_access_token,
    get_current_user as _api_current_user,
    oauth2_scheme,
)
from app.ajustes import obtener_configuracion
from app.chatwoot import ChatwootClient, variables_plantilla
from app.config import Settings
from app.db import get_session
from app.etiquetas import migrar_etiqueta, sincronizar_estado_reserva
from app.estados import estado_valido, mapa_etiquetas
from app.models import EstadoReserva, Reserva
from app.schemas import ReservaCreate
from app.scheduler import parse_hora, reprogramar

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SESSION_COOKIE = "losco_session"
# (the fixed ESTADOS list is gone — S3, tags-estados-sync: the form now
# reads the configured estados_reserva table; see _render_form.)

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
# JSON API for the calendar SPA (S8): same auth as the API router (401 JSON
# on miss — no /login redirect, which would confuse a fetch).
api_router = APIRouter(prefix="/api/ajustes", dependencies=[Depends(_api_current_user)])


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


CALENDAR_INDEX = Path(__file__).parent / "static" / "calendar" / "index.html"


@router.get("/")
def index():
    """S1: serve the FullCalendar SPA as the site root. Login-protected via
    the router dependency; all library views (Año/Mes/Semana/Día) live in
    the SPA toolbar. The assets stay on the /static mount."""
    return FileResponse(CALENDAR_INDEX)


def _render_form(request: Request, reserva: Reserva | None, session: Session,
                 errors: dict | None = None, values: dict | None = None):
    estados = session.scalars(select(EstadoReserva).order_by(EstadoReserva.id))
    return templates.TemplateResponse(
        request,
        "reserva_form.html",
        {"reserva": reserva, "errors": errors or {}, "values": values or {},
         "estados": estados},
    )


@router.get("/nuevo")
def nueva_reserva(request: Request, session: Session = Depends(get_session)):
    return _render_form(request, reserva=None, session=session)


@router.get("/reservas/{reserva_id}/editar")
def editar_reserva(request: Request, reserva_id: int, session: Session = Depends(get_session)):
    reserva = session.get(Reserva, reserva_id)
    if reserva is None:
        return _not_found()
    return _render_form(request, reserva=reserva, session=session)


def _parse_form(form, session) -> tuple[ReservaCreate | None, dict, dict]:
    """Returns (parsed_payload_or_None, field_errors, raw_values). S3
    (tags-estados-sync): the state is validated against the configured
    estados_reserva table, with the field-tagged error shape the form uses."""
    raw = {key: form.get(key) for key in ("nombre", "telefono", "fecha", "hora", "personas", "estado", "detalles")}
    raw["enviar_recordatorio"] = "enviar_recordatorio" in form
    try:
        estado_valido(session, raw.get("estado"))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "estado inválido"
        return None, {"estado": detail.replace("estado desconocido: ", "Estado desconocido: ")}, raw
    try:
        return ReservaCreate(**raw), {}, raw
    except ValidationError as exc:
        errors = {str(err["loc"][0]): err["msg"] for err in exc.errors()}
        return None, errors, raw


@router.post("/reservas")
def crear_reserva(request: Request, form: dict = Depends(_form_dict), session: Session = Depends(get_session)):
    payload, errors, values = _parse_form(form, session)
    if payload is None:
        return _render_form(request, reserva=None, session=session, errors=errors, values=values)
    reserva = Reserva(**payload.model_dump())
    session.add(reserva)
    session.commit()
    session.refresh(reserva)
    sincronizar_estado_reserva(
        request.app.state.settings, reserva.telefono, mapa_etiquetas(session),
        None, reserva.estado,
    )
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
    estado_anterior = reserva.estado
    payload, errors, values = _parse_form(form, session)
    if payload is None:
        return _render_form(request, reserva=reserva, session=session, errors=errors, values=values)
    for field, value in payload.model_dump().items():
        setattr(reserva, field, value)
    session.commit()
    session.refresh(reserva)
    sincronizar_estado_reserva(
        request.app.state.settings, reserva.telefono, mapa_etiquetas(session),
        estado_anterior, reserva.estado,
    )
    return RedirectResponse("/", status_code=303)


# --- ajustes de mensajería (L19: horas y plantillas en el panel, no env) ---

AJUSTES_CAMPOS = ("hora_confirmacion", "hora_invitados", "plantilla_confirmacion", "plantilla_invitados", "tag_disparadora")


def _render_ajustes(
    request: Request,
    config,
    errors: dict | None = None,
    values: dict | None = None,
    migrados_contactos: int | None = None,
    migrados_conv: int | None = None,
    fallas: int | None = None,
    estados: list | None = None,
):
    return templates.TemplateResponse(
        request,
        "ajustes.html",
        {
            "config": config,
            "errors": errors or {},
            "values": values or {},
            "migrados_contactos": migrados_contactos,
            "migrados_conv": migrados_conv,
            "fallas": fallas,
            # tags-panel-v2 S2: nombre/tag de cada estado para mostrar cuál
            # queda como disparador con la tag elegida.
            "estados": estados or [],
        },
    )


@router.get("/settings")
def settings_redirect():
    """/settings is the calendar gear's destination (L10): canonical panel
    stays at /ajustes."""
    return RedirectResponse("/ajustes", status_code=303)


@router.get("/ajustes")
def ajustes_page(
    request: Request,
    migrados_contactos: int | None = None,
    migrados_conv: int | None = None,
    fallas: int | None = None,
    session: Session = Depends(get_session),
):
    config = obtener_configuracion(session, request.app.state.settings)
    estados = [
        {"nombre": e.nombre, "slug": e.slug, "etiqueta_chatwoot": e.etiqueta_chatwoot}
        for e in session.scalars(select(EstadoReserva).order_by(EstadoReserva.id))
    ]
    return _render_ajustes(
        request,
        config,
        migrados_contactos=migrados_contactos,
        migrados_conv=migrados_conv,
        fallas=fallas,
        estados=estados,
    )


def _chatwoot_configurado(settings: Settings) -> bool:
    """True when the admin has supplied the three Chatwoot credentials."""
    return bool(
        settings.chatwoot_base_url
        and settings.chatwoot_api_token
        and settings.chatwoot_account_id
    )


# --- ajustes JSON API (S8: settings popup inside the SPA) ---

# T4 (tags-estados-sync): `etiqueta_confirmada` salió de la config de
# mensajería — el mapeo estado↔etiqueta vive en `estados_reserva` y se
# edita en la página Tags (/ajustes/tags).
# tags-panel-v2 (S2/D1): `tag_disparadora` vuelve a la config de mensajería:
# la tag elegida señala al estado disparador que gatea el cron.
AJUSTES_CAMPOS = ("hora_confirmacion", "hora_invitados", "plantilla_confirmacion", "plantilla_invitados", "tag_disparadora")


def _ajustes_payload(config) -> dict:
    return {campo: getattr(config, campo) for campo in AJUSTES_CAMPOS}


@api_router.get("")
def ajustes_json(request: Request, session: Session = Depends(get_session)):
    config = obtener_configuracion(session, request.app.state.settings)
    return _ajustes_payload(config)


@api_router.put("")
def ajustes_json_put(
    request: Request,
    payload: dict,
    session: Session = Depends(get_session),
):
    settings = request.app.state.settings
    values = {campo: (payload.get(campo) or "").strip() if isinstance(payload.get(campo), str) else ""
              for campo in AJUSTES_CAMPOS}
    errors: dict[str, str] = {}
    for campo in ("hora_confirmacion", "hora_invitados"):
        try:
            parse_hora(values[campo])
        except ValueError:
            errors[campo] = "Hora mal formada: usá HH:MM (ej. 09:00)."
    for campo in ("plantilla_confirmacion", "plantilla_invitados"):
        if not values[campo]:
            errors[campo] = "Es obligatorio."
    if not values["tag_disparadora"]:
        errors["tag_disparadora"] = "Es obligatoria: decide qué estado dispara los envíos."
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    config = obtener_configuracion(session, settings)
    for campo in AJUSTES_CAMPOS:
        setattr(config, campo, values[campo])
    session.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        reprogramar(scheduler, request, settings)
    return _ajustes_payload(config)


@router.post("/ajustes")
def ajustes_submit(
    request: Request,
    hora_confirmacion: str | None = Form(None),
    hora_invitados: str | None = Form(None),
    plantilla_confirmacion: str | None = Form(None),
    plantilla_invitados: str | None = Form(None),
    tag_disparadora: str | None = Form(None),
    session: Session = Depends(get_session),
):
    """T4: solo mensajería. Un POST legacy que aún envíe
    `etiqueta_confirmada` lo ignora sin fallar (el campo salió del panel)."""
    settings = request.app.state.settings
    values = {
        "hora_confirmacion": (hora_confirmacion or "").strip(),
        "hora_invitados": (hora_invitados or "").strip(),
        "plantilla_confirmacion": (plantilla_confirmacion or "").strip(),
        "plantilla_invitados": (plantilla_invitados or "").strip(),
        "tag_disparadora": (tag_disparadora or "").strip(),
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
    if not values["tag_disparadora"]:
        errors["tag_disparadora"] = "La tag disparadora es obligatoria."
    if errors:
        config = obtener_configuracion(session, settings)
        return _render_ajustes(
            request,
            config,
            errors=errors,
            values=values,
            estados=[
                {"nombre": e.nombre, "slug": e.slug, "etiqueta_chatwoot": e.etiqueta_chatwoot}
                for e in session.scalars(select(EstadoReserva).order_by(EstadoReserva.id))
            ],
        )

    config = obtener_configuracion(session, settings)
    config.hora_confirmacion = values["hora_confirmacion"]
    config.hora_invitados = values["hora_invitados"]
    config.plantilla_confirmacion = values["plantilla_confirmacion"]
    config.plantilla_invitados = values["plantilla_invitados"]
    config.tag_disparadora = values["tag_disparadora"]
    session.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        reprogramar(scheduler, request.app, settings)

    return RedirectResponse("/ajustes", status_code=303)


# --- sección Tags / estados (T4, tags-estados-sync) ---

def _redirect_tags(**params) -> RedirectResponse:
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    return RedirectResponse(f"/ajustes/tags{('?' + query) if query else ''}", status_code=303)


@router.get("/ajustes/tags")
def ajustes_tags_page(
    request: Request,
    migrados_contactos: int | None = None,
    migrados_conv: int | None = None,
    fallas: int | None = None,
    error: str | None = None,
    session: Session = Depends(get_session),
):
    """Sección Tags: estados de reserva configurables y su etiqueta de
    Chatwoot (S1-S4, tags-estados-sync). El estado guarda el mapeo; al
    cambiarlo se ofrece migrar la etiqueta vieja (L27)."""
    estados = list(session.scalars(select(EstadoReserva).order_by(EstadoReserva.id)))
    return templates.TemplateResponse(
        request,
        "ajustes_tags.html",
        {
            "estados": estados,
            "migrados_contactos": migrados_contactos,
            "migrados_conv": migrados_conv,
            "fallas": fallas,
            "error": error,
        },
    )


@router.post("/ajustes/tags")
def ajustes_tags_crear(
    request: Request,
    nombre: str | None = Form(None),
    etiqueta_chatwoot: str | None = Form(None),
    session: Session = Depends(get_session),
):
    nombre = (nombre or "").strip()
    if not nombre or len(nombre) > 60:
        return _redirect_tags(error="El nombre del estado es obligatorio (máx 60 caracteres).")
    slug = _slug_de(nombre)
    if session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug)):
        return _redirect_tags(error=f"Ya existe el estado '{slug}'.")
    session.add(EstadoReserva(
        nombre=nombre, slug=slug,
        etiqueta_chatwoot=(etiqueta_chatwoot or "").strip() or None,
    ))  # S6 (tags-panel-v2): envia_recordatorio ya no se edita por UI
    session.commit()
    return _redirect_tags()


@router.post("/ajustes/tags/{slug}")
def ajustes_tags_editar(
    request: Request,
    slug: str,
    nombre: str | None = Form(None),
    etiqueta_chatwoot: str | None = Form(None),
    session: Session = Depends(get_session),
):
    """Edición de un estado. Al cambiar la etiqueta de Chatwoot se consulta
    la cuenta: si la etiqueta vieja está en uso se ofrece la migración
    (mismo flujo que tenía /ajustes para etiqueta_confirmada)."""
    estado = session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug))
    if estado is None:
        return _redirect_tags(error="Estado no encontrado.")
    nombre = (nombre or "").strip()
    if not nombre or len(nombre) > 60:
        return _redirect_tags(error="El nombre del estado es obligatorio (máx 60 caracteres).")
    settings = request.app.state.settings
    vieja = estado.etiqueta_chatwoot
    nueva = (etiqueta_chatwoot or "").strip() or None
    estado.nombre = nombre
    estado.etiqueta_chatwoot = nueva
    session.commit()

    if _chatwoot_configurado(settings) and vieja and nueva and vieja != nueva:
        try:
            chatwoot = ChatwootClient(
                settings.chatwoot_base_url,
                settings.chatwoot_api_token,
                settings.chatwoot_account_id,
            )
            total_contactos = len(chatwoot.contactos_con_etiqueta(vieja))
            total_conversaciones = len(chatwoot.conversaciones_con_etiqueta(vieja))
        except Exception:
            return _redirect_tags()
        if total_contactos + total_conversaciones > 0:
            return templates.TemplateResponse(
                request,
                "migrar_etiqueta.html",
                {
                    "vieja": vieja,
                    "nueva": nueva,
                    "total_contactos": total_contactos,
                    "total_conversaciones": total_conversaciones,
                },
            )
    return _redirect_tags()


@router.post("/ajustes/tags/{slug}/eliminar")
def ajustes_tags_eliminar(
    request: Request, slug: str, session: Session = Depends(get_session)
):
    estado = session.scalar(select(EstadoReserva).where(EstadoReserva.slug == slug))
    if estado is None:
        return _redirect_tags(error="Estado no encontrado.")
    if estado.es_base:
        return _redirect_tags(error="Los tres estados base no se pueden eliminar.")
    en_uso = session.scalar(
        select(func.count()).select_from(Reserva).where(Reserva.estado == slug)
    )
    if en_uso:
        return _redirect_tags(
            error=f"El estado '{slug}' lo usan {en_uso} reserva(s): reasignalas antes de eliminarlo."
        )
    session.delete(estado)
    session.commit()
    return _redirect_tags()


def _slug_de(nombre: str) -> str:
    """Display name -> stored slug (solo el dashboard lo llama al crear)."""
    import re as _re

    return _re.sub(r"\s+", "-", (nombre or "").strip().lower()) or "estado"


@router.post("/ajustes/migrar-etiqueta")
def migrar_etiqueta_submit(
    request: Request,
    vieja: str | None = Form(None),
    nueva: str | None = Form(None),
):
    vieja = (vieja or "").strip()
    nueva = (nueva or "").strip()
    if not vieja or not nueva or vieja == nueva:
        return RedirectResponse("/ajustes/tags", status_code=303)

    resultado = migrar_etiqueta(request.app.state.settings, vieja, nueva)
    return RedirectResponse(
        f"/ajustes/tags?migrados_contactos={len(resultado['migrados_contactos'])}"
        f"&migrados_conv={len(resultado['migrados_conversaciones'])}"
        f"&fallas={len(resultado['fallas'])}",
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
                "variables": variables_plantilla(p),
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

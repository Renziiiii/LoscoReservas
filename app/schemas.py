"""Pydantic schemas for the reservas API (S1: "CRUD con API")."""

import re
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

# S3 (tags-estados-sync): the valid `estado` values are whatever the
# configured `estados_reserva` table holds — validation is dynamic and
# lives in app/estados.py `estado_valido` (called by the routers), NOT in a
# static Literal here. This alias keeps the deep field spreads working.
Estado = str


def _normalize_telefono(value: str) -> str:
    r"""Store phones in full international (E.164) format: both
    '+54 9 321 098-7654' and '5493210987654' become '+5493210987654'.

    Chatwoot 4.17 accepts only the /\+[1-9]\d{1,14}/ pattern (L51) and every
    phone in the account is stored '+...', so the '+' is always prepended
    and a number without a country code is rejected here — storing it
    anyway made the Chatwoot contact write fail with a 422 and the Meta
    send fail silently."""
    digitos = re.sub(r"\D", "", value)
    if not 11 <= len(digitos) <= 15 or digitos.startswith("0"):
        raise ValueError(
            "telefono must be in international format with the country "
            "code (e.g. 5493210987654)"
        )
    return f"+{digitos}"


class ReservaBase(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    telefono: str = Field(max_length=32)
    fecha: date
    hora: time
    personas: int = Field(ge=1, le=200)
    estado: Estado = "pendiente"
    detalles: str | None = Field(default=None, max_length=2000)
    enviar_recordatorio: bool = True

    @field_validator("nombre")
    @classmethod
    def _nombre_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nombre cannot be blank")
        return value.strip()

    @field_validator("estado")
    @classmethod
    def _estado_normalizado(cls, value: object) -> str:
        """Store states trimmed/lowercased (slugs of estados_reserva)."""
        if value is None:
            return "pendiente"
        return str(value).strip().lower()

    @field_validator("telefono")
    @classmethod
    def _telefono_digits(cls, value: str) -> str:
        return _normalize_telefono(value)


class ReservaCreate(ReservaBase):
    """All identifying fields required to register a reservation."""


class ReservaCreateIframe(ReservaCreate):
    """Iframe create (S11/L46): adds the Chatwoot contact id from the
    appContext so a phoneless contact (e.g. Instagram chats) can receive
    the phone the agent annotated. It is NOT persisted on the reserva."""

    contacto_chatwoot_id: int | None = None


class ReservaUpdate(BaseModel):
    """Partial update: only the fields actually sent change."""

    nombre: str | None = Field(default=None, min_length=1, max_length=120)
    telefono: str | None = Field(default=None, max_length=32)
    fecha: date | None = None
    hora: time | None = None
    personas: int | None = Field(default=None, ge=1, le=200)
    estado: Estado | None = None
    detalles: str | None = Field(default=None, max_length=2000)
    enviar_recordatorio: bool | None = None

    _nombre_not_blank = field_validator("nombre")(ReservaBase._nombre_not_blank.__func__)  # type: ignore[attr-defined]
    _telefono_digits = field_validator("telefono")(ReservaBase._telefono_digits.__func__)  # type: ignore[attr-defined]
    _estado_normalizado = field_validator("estado")(ReservaBase._estado_normalizado.__func__)  # type: ignore[attr-defined]


class ReservaOut(ReservaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ReservaUpdateIframe(ReservaUpdate):
    """Iframe edit: same optional contact id (the sync only fills empty
    phones), used when the agent corrects the reserva's phone."""

    contacto_chatwoot_id: int | None = None

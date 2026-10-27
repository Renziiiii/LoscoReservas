"""Pydantic schemas for the reservas API (S1: "CRUD con API")."""

import re
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Estado = Literal["pendiente", "confirmada", "cancelada"]


def _normalize_telefono(value: str) -> str:
    """Keep only leading '+' and digits: "+54 9 11 5555-1234" -> "+5491155551234"."""
    normalized = re.sub(r"(?<!^)\D", "", value)
    if len(re.sub(r"\D", "", normalized)) < 8:
        raise ValueError("telefono must contain at least 8 digits")
    return normalized


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

    @field_validator("telefono")
    @classmethod
    def _telefono_digits(cls, value: str) -> str:
        return _normalize_telefono(value)


class ReservaCreate(ReservaBase):
    """All identifying fields required to register a reservation."""


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


class ReservaOut(ReservaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

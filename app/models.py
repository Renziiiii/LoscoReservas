"""ORM models. `Reserva` implements the S7 field list verbatim:

"DB con: id,nombre,telefono,fecha, hora personas, estado, detalles, enviar
recordatorio,"

`created_at` / `updated_at` are a standard addition (audit), everything else is
exactly the user's list.
"""

from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, Integer, String, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Configuracion(Base):
    """Single-row (id=1) runtime configuration edited in the admin panel
    (L19): messaging hours and WhatsApp template names live here, not in
    environment variables. Seeded from Settings defaults on first boot."""

    __tablename__ = "configuracion"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    hora_confirmacion: Mapped[str] = mapped_column(String(5), default="09:00")
    hora_invitados: Mapped[str] = mapped_column(String(5), default="17:15")
    plantilla_confirmacion: Mapped[str] = mapped_column(String(120), default="reserva_confirmacion")
    plantilla_invitados: Mapped[str] = mapped_column(String(120), default="invitados_confirmacion")
    etiqueta_confirmada: Mapped[str] = mapped_column(String(120), default="Reserva confirmada")


class EstadoReserva(Base):
    """Configured reservation states (S3, odd/tasks/tags-estados-sync.md).

    Replaces the fixed `Estado = Literal[...]` of schemas.py as the single
    source of truth for which `Reserva.estado` values exist. Each state may
    map to a Chatwoot label (S4: `etiqueta_chatwoot`, None = no sync) and
    declares `envia_recordatorio` (S4: generalized replacement for the old
    hardcoded `estado != "cancelada"` of the reminder engine).

    `nombre` is the display name; `slug` is the stored value of
    `Reserva.estado` (immutable after creation). `es_base` marks the three
    migration seeds, which the Tags panel does not allow to delete.
    """

    __tablename__ = "estados_reserva"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60), unique=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    etiqueta_chatwoot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    envia_recordatorio: Mapped[bool] = mapped_column(Boolean, default=True)
    es_base: Mapped[bool] = mapped_column(Boolean, default=False)


class Reserva(Base):
    __tablename__ = "reservas"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    telefono: Mapped[str] = mapped_column(String(32))
    fecha: Mapped[date] = mapped_column(Date)
    hora: Mapped[time] = mapped_column(Time)
    personas: Mapped[int] = mapped_column(Integer, default=1)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    detalles: Mapped[str | None] = mapped_column(Text, nullable=True)
    enviar_recordatorio: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmacion_enviada_at: Mapped[datetime | None] = mapped_column(nullable=True)
    invitados_enviada_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

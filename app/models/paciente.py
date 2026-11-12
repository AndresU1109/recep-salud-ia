"""Pacientes (datos ficticios en desarrollo). El documento se guarda cifrado + hash para buscarlo."""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import TipoDocumento


class Paciente(TimestampMixin, Base):
    __tablename__ = "pacientes"
    __table_args__ = (
        UniqueConstraint("tipo_documento", "documento_hash", name="uq_pacientes_documento"),
        Index("ix_pacientes_apellidos", "apellidos", "nombres"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_documento: Mapped[TipoDocumento] = mapped_column(enum_type(TipoDocumento))
    # HMAC-SHA256(documento) con clave del servidor: permite buscar sin guardar el número en claro.
    documento_hash: Mapped[str] = mapped_column(String(64))
    documento_cifrado: Mapped[str] = mapped_column(Text)  # Fernet; se implementa en la Fase 3/5
    documento_ultimos4: Mapped[str] = mapped_column(String(4))  # para mostrar "****1234"
    nombres: Mapped[str] = mapped_column(String(120))
    apellidos: Mapped[str] = mapped_column(String(120))
    fecha_nacimiento: Mapped[date] = mapped_column(Date)  # segundo factor para verificar identidad
    telefono: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(254))
    entidad_aseguradora: Mapped[str | None] = mapped_column(String(150))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Solo avisos sobre sus propias citas (confirmación, cambios, recordatorio, cupo de la lista de espera); nunca publicidad. Puede apagarlos.
    notificaciones_activas: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ConsentimientoDatos(Base):
    """Evidencia de autorización de tratamiento de datos (Ley 1581 de 2012)."""

    __tablename__ = "consentimientos_datos"

    id: Mapped[int] = mapped_column(primary_key=True)
    paciente_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id", ondelete="RESTRICT"), index=True)
    politica_version: Mapped[str] = mapped_column(String(30))
    aceptado: Mapped[bool] = mapped_column(Boolean)
    autoriza_transcripcion: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    canal: Mapped[str] = mapped_column(String(30))
    otorgado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""Bandeja de salida de avisos a pacientes. Un aviso se encola dentro de la misma transacción que el hecho que lo causa
(así no hay avisos de citas que no se guardaron) y un proceso aparte lo envía, con reintentos."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import CanalNotificacion, EstadoNotificacion, TipoNotificacion


class Notificacion(TimestampMixin, Base):
    __tablename__ = "notificaciones"
    __table_args__ = (
        UniqueConstraint("clave_unica", name="uq_notificaciones_clave"),  # el mismo aviso no se encola dos veces
        Index("ix_notificaciones_pendientes", "estado", "programada_para"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clave_unica: Mapped[str] = mapped_column(String(120))
    paciente_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id", ondelete="RESTRICT"), index=True)
    cita_id: Mapped[int | None] = mapped_column(ForeignKey("citas.id", ondelete="SET NULL"), index=True)
    lista_espera_id: Mapped[int | None] = mapped_column(ForeignKey("lista_espera.id", ondelete="SET NULL"))
    tipo: Mapped[TipoNotificacion] = mapped_column(enum_type(TipoNotificacion))
    canal: Mapped[CanalNotificacion] = mapped_column(enum_type(CanalNotificacion))
    estado: Mapped[EstadoNotificacion] = mapped_column(
        enum_type(EstadoNotificacion), default=EstadoNotificacion.PENDIENTE, server_default=EstadoNotificacion.PENDIENTE.value)
    destino_enmascarado: Mapped[str | None] = mapped_column(String(80))  # el destino real se lee del paciente al enviar; no se copia aquí
    asunto: Mapped[str] = mapped_column(String(200), default="", server_default="")
    cuerpo: Mapped[str] = mapped_column(Text, default="", server_default="")  # vacío si se omitió: no se guarda lo que no se envió
    programada_para: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enviada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intentos: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    motivo: Mapped[str | None] = mapped_column(String(60))  # por qué se omitió o falló (código, nunca datos personales)
    contexto: Mapped[dict | None] = mapped_column(JSONB)  # p. ej. el inicio de la cita al encolar un recordatorio

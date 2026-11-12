"""Citas. La prevención de doble reserva la garantiza PostgreSQL (restricciones EXCLUDE), no solo el código."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, SmallInteger, String, Text, text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import AccionCita, ActorTipo, CanalCita, EstadoCita, EstadoListaEspera

_RANGO = text("tstzrange(inicio, fin, '[)')")
_OCUPA_CUPO = text("estado <> 'CANCELADA'")


class Cita(TimestampMixin, Base):
    __tablename__ = "citas"
    __table_args__ = (
        CheckConstraint("fin > inicio", name="fin_mayor"),
        CheckConstraint("reprogramaciones >= 0", name="reprogramaciones_no_negativas"),
        # Una cita no cancelada ocupa su franja: un profesional, un consultorio y un paciente
        # no pueden tener dos citas que se solapen.
        ExcludeConstraint(("profesional_id", "="), (_RANGO, "&&"), where=_OCUPA_CUPO,
                          using="gist", name="ex_citas_profesional_sin_solape"),
        ExcludeConstraint(("consultorio_id", "="), (_RANGO, "&&"), where=_OCUPA_CUPO,
                          using="gist", name="ex_citas_consultorio_sin_solape"),
        ExcludeConstraint(("paciente_id", "="), (_RANGO, "&&"), where=_OCUPA_CUPO,
                          using="gist", name="ex_citas_paciente_sin_solape"),
        Index("ix_citas_inicio", "inicio"),
        Index("ix_citas_profesional_inicio", "profesional_id", "inicio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo_reserva: Mapped[str] = mapped_column(String(12), unique=True)
    paciente_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id", ondelete="RESTRICT"), index=True)
    profesional_id: Mapped[int] = mapped_column(ForeignKey("profesionales.id", ondelete="RESTRICT"))
    especialidad_id: Mapped[int] = mapped_column(ForeignKey("especialidades.id", ondelete="RESTRICT"))
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("servicios.id", ondelete="RESTRICT"))
    sede_id: Mapped[int] = mapped_column(ForeignKey("sedes.id", ondelete="RESTRICT"))
    consultorio_id: Mapped[int | None] = mapped_column(ForeignKey("consultorios.id", ondelete="RESTRICT"))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[EstadoCita] = mapped_column(
        enum_type(EstadoCita), default=EstadoCita.PROGRAMADA, server_default=EstadoCita.PROGRAMADA.value
    )
    canal: Mapped[CanalCita] = mapped_column(enum_type(CanalCita))
    reprogramaciones: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    confirmada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_cancelacion: Mapped[str | None] = mapped_column(String(250))
    notas_administrativas: Mapped[str | None] = mapped_column(Text)  # nunca información clínica
    conversacion_id: Mapped[Any | None] = mapped_column(ForeignKey("conversaciones.id", ondelete="SET NULL"))
    creada_por_usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))


class CitaHistorial(Base):
    __tablename__ = "citas_historial"
    __table_args__ = (Index("ix_citas_historial_cita", "cita_id", "ocurrio_en"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cita_id: Mapped[int] = mapped_column(ForeignKey("citas.id", ondelete="RESTRICT"))
    accion: Mapped[AccionCita] = mapped_column(enum_type(AccionCita))
    estado_anterior: Mapped[EstadoCita | None] = mapped_column(enum_type(EstadoCita, "estadocita_anterior"))
    estado_nuevo: Mapped[EstadoCita] = mapped_column(enum_type(EstadoCita, "estadocita_nuevo"))
    inicio_anterior: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fin_anterior: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_tipo: Mapped[ActorTipo] = mapped_column(enum_type(ActorTipo))
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    detalle: Mapped[dict | None] = mapped_column(JSONB)
    ocurrio_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class RegistroInasistencia(TimestampMixin, Base):
    """Una inasistencia solo cuenta cuando una persona autorizada la verifica."""

    __tablename__ = "inasistencias"

    id: Mapped[int] = mapped_column(primary_key=True)
    cita_id: Mapped[int] = mapped_column(ForeignKey("citas.id", ondelete="RESTRICT"), unique=True)
    paciente_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id", ondelete="RESTRICT"), index=True)
    verificada: Mapped[bool] = mapped_column(default=False, server_default="false")
    verificada_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    verificada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observacion: Mapped[str | None] = mapped_column(String(300))
    consecuencia_aplicada: Mapped[str | None] = mapped_column(String(300))  # solo las autorizadas en una política


class ListaEspera(TimestampMixin, Base):
    __tablename__ = "lista_espera"
    __table_args__ = (CheckConstraint("hasta >= desde", name="rango_valido"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    paciente_id: Mapped[int] = mapped_column(ForeignKey("pacientes.id", ondelete="RESTRICT"), index=True)
    especialidad_id: Mapped[int] = mapped_column(ForeignKey("especialidades.id", ondelete="RESTRICT"))
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("servicios.id", ondelete="RESTRICT"))
    profesional_id: Mapped[int | None] = mapped_column(ForeignKey("profesionales.id", ondelete="RESTRICT"))
    sede_id: Mapped[int | None] = mapped_column(ForeignKey("sedes.id", ondelete="RESTRICT"))
    desde: Mapped[date] = mapped_column(Date)
    hasta: Mapped[date] = mapped_column(Date)
    estado: Mapped[EstadoListaEspera] = mapped_column(
        enum_type(EstadoListaEspera), default=EstadoListaEspera.ACTIVA, server_default=EstadoListaEspera.ACTIVA.value
    )
    cita_id: Mapped[int | None] = mapped_column(ForeignKey("citas.id", ondelete="SET NULL"))

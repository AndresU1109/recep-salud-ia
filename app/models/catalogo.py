"""Usuarios, sedes, especialidades, profesionales, horarios y requisitos."""
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, SmallInteger,
    String, Table, Text, Time, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import Rol


class Usuario(TimestampMixin, Base):
    __tablename__ = "usuarios"
    __table_args__ = (Index("uq_usuarios_email_lower", text("lower(email)"), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    nombre: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))  # nunca la contraseña en claro
    rol: Mapped[Rol] = mapped_column(enum_type(Rol))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    debe_cambiar_clave: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Los tokens emitidos antes de este instante dejan de valer (cambio o restablecimiento de clave).
    clave_cambiada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Sede(TimestampMixin, Base):
    __tablename__ = "sedes"
    # El nombre es único DENTRO de cada institución (dos instituciones pueden tener una «Sede Norte»).
    __table_args__ = (Index("uq_sedes_institucion_nombre", text("coalesce(institucion_id, 0)"), "nombre", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="RESTRICT"), index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    direccion: Mapped[str | None] = mapped_column(String(250))
    ciudad: Mapped[str | None] = mapped_column(String(100))
    telefono: Mapped[str | None] = mapped_column(String(30))
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    consultorios: Mapped[list["Consultorio"]] = relationship(back_populates="sede")


class Consultorio(TimestampMixin, Base):
    __tablename__ = "consultorios"
    __table_args__ = (UniqueConstraint("sede_id", "nombre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sede_id: Mapped[int] = mapped_column(ForeignKey("sedes.id", ondelete="RESTRICT"))
    nombre: Mapped[str] = mapped_column(String(100))
    piso: Mapped[str | None] = mapped_column(String(20))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    sede: Mapped[Sede] = relationship(back_populates="consultorios")


class Especialidad(TimestampMixin, Base):
    __tablename__ = "especialidades"
    __table_args__ = (Index("uq_especialidades_institucion_nombre", text("coalesce(institucion_id, 0)"), "nombre", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="RESTRICT"), index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    descripcion: Mapped[str | None] = mapped_column(Text)
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    servicios: Mapped[list["Servicio"]] = relationship(back_populates="especialidad")


class Servicio(TimestampMixin, Base):
    __tablename__ = "servicios"
    __table_args__ = (
        UniqueConstraint("especialidad_id", "nombre"),
        CheckConstraint("duracion_minutos > 0", name="duracion_positiva"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    especialidad_id: Mapped[int] = mapped_column(ForeignKey("especialidades.id", ondelete="RESTRICT"))
    nombre: Mapped[str] = mapped_column(String(150))
    descripcion: Mapped[str | None] = mapped_column(Text)
    duracion_minutos: Mapped[int] = mapped_column(SmallInteger)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    especialidad: Mapped[Especialidad] = relationship(back_populates="servicios")


profesional_especialidad = Table(
    "profesional_especialidad",
    Base.metadata,
    Column("profesional_id", ForeignKey("profesionales.id", ondelete="CASCADE"), primary_key=True),
    Column("especialidad_id", ForeignKey("especialidades.id", ondelete="RESTRICT"), primary_key=True),
)


class Profesional(TimestampMixin, Base):
    __tablename__ = "profesionales"

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="RESTRICT"), index=True)
    nombre_completo: Mapped[str] = mapped_column(String(200))
    registro_profesional: Mapped[str | None] = mapped_column(String(50))
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"), unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    especialidades: Mapped[list[Especialidad]] = relationship(secondary=profesional_especialidad)


class HorarioProfesional(TimestampMixin, Base):
    """Jornada semanal recurrente. Los cupos se derivan de hora_inicio/hora_fin/duracion_cita_min."""

    __tablename__ = "horarios_profesional"
    __table_args__ = (
        CheckConstraint("dia_semana BETWEEN 0 AND 6", name="dia_valido"),  # 0 = lunes
        CheckConstraint("hora_fin > hora_inicio", name="hora_fin_mayor"),
        CheckConstraint("duracion_cita_min > 0", name="duracion_positiva"),
        CheckConstraint("cupos_maximos IS NULL OR cupos_maximos > 0", name="cupos_positivos"),
        CheckConstraint("vigente_hasta IS NULL OR vigente_hasta >= vigente_desde", name="vigencia_valida"),
        Index("ix_horarios_profesional_dia", "profesional_id", "dia_semana"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profesional_id: Mapped[int] = mapped_column(ForeignKey("profesionales.id", ondelete="CASCADE"))
    especialidad_id: Mapped[int] = mapped_column(ForeignKey("especialidades.id", ondelete="RESTRICT"))
    sede_id: Mapped[int] = mapped_column(ForeignKey("sedes.id", ondelete="RESTRICT"))
    consultorio_id: Mapped[int | None] = mapped_column(ForeignKey("consultorios.id", ondelete="RESTRICT"))
    dia_semana: Mapped[int] = mapped_column(SmallInteger)
    hora_inicio: Mapped[time] = mapped_column(Time)
    hora_fin: Mapped[time] = mapped_column(Time)
    duracion_cita_min: Mapped[int] = mapped_column(SmallInteger)
    cupos_maximos: Mapped[int | None] = mapped_column(SmallInteger)  # tope opcional por jornada
    vigente_desde: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class ExcepcionAgenda(TimestampMixin, Base):
    """Bloqueo puntual (vacaciones, festivo, mantenimiento). Sin profesional/sede = aplica a todos."""

    __tablename__ = "excepciones_agenda"
    __table_args__ = (CheckConstraint("fin > inicio", name="fin_mayor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profesional_id: Mapped[int | None] = mapped_column(ForeignKey("profesionales.id", ondelete="CASCADE"))
    sede_id: Mapped[int | None] = mapped_column(ForeignKey("sedes.id", ondelete="CASCADE"))
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    motivo: Mapped[str] = mapped_column(String(250))


class Requisito(TimestampMixin, Base):
    __tablename__ = "requisitos"

    id: Mapped[int] = mapped_column(primary_key=True)
    especialidad_id: Mapped[int | None] = mapped_column(ForeignKey("especialidades.id", ondelete="CASCADE"))
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("servicios.id", ondelete="CASCADE"))
    titulo: Mapped[str] = mapped_column(String(200))
    descripcion: Mapped[str] = mapped_column(Text)
    orden: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

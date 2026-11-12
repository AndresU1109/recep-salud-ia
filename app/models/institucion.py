"""Políticas, preguntas frecuentes, solicitudes/quejas y configuración de IA."""
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import EstadoSolicitud, TipoPolitica, TipoSolicitud


class Institucion(TimestampMixin, Base):
    """Una institución de salud (clínica, IPS, centro odontológico…). Cada una tiene su catálogo, sus políticas y su conocimiento;
    el asistente de voz habla en nombre de UNA, la que se elige al empezar la llamada. Los pacientes son un registro común."""

    __tablename__ = "instituciones"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200), unique=True)
    tipo: Mapped[str | None] = mapped_column(String(80))  # «Clínica», «IPS», «Centro odontológico»…
    ciudad: Mapped[str | None] = mapped_column(String(100))
    descripcion: Mapped[str | None] = mapped_column(Text)
    mensaje_bienvenida: Mapped[str | None] = mapped_column(Text)  # lo que dice el asistente al contestar; vacío = saludo por defecto
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class PoliticaInstitucional(TimestampMixin, Base):
    """Reglas configurables. Si no hay política activa, el agente debe decir que no tiene la información."""

    __tablename__ = "politicas"
    __table_args__ = (
        CheckConstraint("vigente_hasta IS NULL OR vigente_hasta >= vigente_desde", name="vigencia_valida"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[TipoPolitica] = mapped_column(enum_type(TipoPolitica))
    titulo: Mapped[str] = mapped_column(String(200))
    # Ej.: {"horas_minimas_cancelar": 24, "maximo_reprogramaciones": 2} — lo define la institución.
    parametros: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    mensaje_paciente: Mapped[str | None] = mapped_column(Text)
    consecuencia_autorizada: Mapped[str | None] = mapped_column(Text)  # solo si la institución la autorizó
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="CASCADE"), index=True)  # vacío = aplica a todas
    sede_id: Mapped[int | None] = mapped_column(ForeignKey("sedes.id", ondelete="CASCADE"))
    especialidad_id: Mapped[int | None] = mapped_column(ForeignKey("especialidades.id", ondelete="CASCADE"))
    vigente_desde: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class PreguntaFrecuente(TimestampMixin, Base):
    __tablename__ = "preguntas_frecuentes"

    id: Mapped[int] = mapped_column(primary_key=True)
    pregunta: Mapped[str] = mapped_column(String(400))
    respuesta: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(String(100))
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="CASCADE"), index=True)  # vacío = de todas
    especialidad_id: Mapped[int | None] = mapped_column(ForeignKey("especialidades.id", ondelete="CASCADE"))
    sede_id: Mapped[int | None] = mapped_column(ForeignKey("sedes.id", ondelete="CASCADE"))
    orden: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Cada pregunta se refleja en un Documento (categoría PREGUNTAS_FRECUENTES) para que el RAG la encuentre como cualquier fuente.
    documento_id: Mapped[int | None] = mapped_column(ForeignKey("documentos.id", ondelete="SET NULL"), unique=True)


class Solicitud(TimestampMixin, Base):
    """Solicitudes, quejas, reclamos y pedidos de atención humana."""

    __tablename__ = "solicitudes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[TipoSolicitud] = mapped_column(enum_type(TipoSolicitud))
    estado: Mapped[EstadoSolicitud] = mapped_column(
        enum_type(EstadoSolicitud), default=EstadoSolicitud.ABIERTA, server_default=EstadoSolicitud.ABIERTA.value
    )
    asunto: Mapped[str] = mapped_column(String(250))
    descripcion: Mapped[str] = mapped_column(Text)
    paciente_id: Mapped[int | None] = mapped_column(ForeignKey("pacientes.id", ondelete="SET NULL"), index=True)
    conversacion_id: Mapped[object | None] = mapped_column(ForeignKey("conversaciones.id", ondelete="SET NULL"))
    asignada_a: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    resolucion: Mapped[str | None] = mapped_column(Text)
    cerrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfiguracionIA(Base):
    """Ajustes editables desde el panel (modelo, temperatura, voz...). Clave/valor."""

    __tablename__ = "configuracion_ia"

    clave: Mapped[str] = mapped_column(String(100), primary_key=True)
    valor: Mapped[dict] = mapped_column(JSONB)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    actualizado_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))

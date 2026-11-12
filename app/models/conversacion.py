"""Conversaciones, mensajes, herramientas ejecutadas y fuentes RAG usadas (trazabilidad)."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, enum_type
from app.models.enums import CanalConversacion, EstadoConversacion, ResultadoHerramienta, RolMensaje


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    canal: Mapped[CanalConversacion] = mapped_column(enum_type(CanalConversacion))
    estado: Mapped[EstadoConversacion] = mapped_column(
        enum_type(EstadoConversacion), default=EstadoConversacion.ACTIVA, server_default=EstadoConversacion.ACTIVA.value
    )
    paciente_id: Mapped[int | None] = mapped_column(ForeignKey("pacientes.id", ondelete="SET NULL"), index=True)
    identidad_verificada: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Si el paciente no autoriza guardar la transcripción, los mensajes se guardan sin contenido.
    guarda_transcripcion: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    es_prueba: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # conversaciones del personal: no cuentan en reportes
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="SET NULL"), index=True)  # en nombre de quién habló el asistente
    iniciada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalizada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = (Index("ix_mensajes_conversacion", "conversacion_id", "creado_en"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversacion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversaciones.id", ondelete="CASCADE"))
    rol: Mapped[RolMensaje] = mapped_column(enum_type(RolMensaje))
    contenido: Mapped[str | None] = mapped_column(Text)
    intencion: Mapped[str | None] = mapped_column(String(60))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EjecucionHerramienta(Base):
    __tablename__ = "ejecuciones_herramienta"
    __table_args__ = (Index("ix_ejecuciones_herramienta_nombre", "nombre", "creado_en"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversacion_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversaciones.id", ondelete="CASCADE"))
    mensaje_id: Mapped[int | None] = mapped_column(ForeignKey("mensajes.id", ondelete="SET NULL"))
    nombre: Mapped[str] = mapped_column(String(60))
    parametros: Mapped[dict | None] = mapped_column(JSONB)  # ya redactados: sin documento ni datos de contacto
    resultado: Mapped[ResultadoHerramienta] = mapped_column(enum_type(ResultadoHerramienta))
    codigo_error: Mapped[str | None] = mapped_column(String(60))
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MensajeFuente(Base):
    """Fragmentos RAG que respaldaron una respuesta."""

    __tablename__ = "mensaje_fuentes"

    mensaje_id: Mapped[int] = mapped_column(ForeignKey("mensajes.id", ondelete="CASCADE"), primary_key=True)
    fragmento_id: Mapped[int] = mapped_column(ForeignKey("documento_fragmentos.id", ondelete="CASCADE"), primary_key=True)
    puntaje: Mapped[float | None] = mapped_column(Float)


class EncuestaSatisfaccion(Base):
    """Calificación (1 a 5) que el paciente da al final de la conversación, con un comentario opcional. Una por conversación; no se liga al paciente."""

    __tablename__ = "encuestas_satisfaccion"
    __table_args__ = (CheckConstraint("puntaje BETWEEN 1 AND 5", name="puntaje_valido"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversaciones.id", ondelete="CASCADE"), unique=True)
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="SET NULL"), index=True)
    canal: Mapped[CanalConversacion] = mapped_column(enum_type(CanalConversacion))
    puntaje: Mapped[int] = mapped_column(SmallInteger)
    comentario: Mapped[str | None] = mapped_column(Text)  # lo que el paciente dijo con sus palabras, como mucho 300 caracteres
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

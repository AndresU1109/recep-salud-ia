from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditoriaLog(Base):
    """Registro de solo escritura: un trigger de la migración impide UPDATE y DELETE."""

    __tablename__ = "auditoria_log"
    __table_args__ = (
        Index("ix_auditoria_log_fecha", "ocurrio_en"),
        Index("ix_auditoria_log_entidad", "entidad", "entidad_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ocurrio_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(60))  # "usuario", "agente", "sistema"
    accion: Mapped[str] = mapped_column(String(80))
    entidad: Mapped[str | None] = mapped_column(String(60))
    entidad_id: Mapped[str | None] = mapped_column(String(60))
    exito: Mapped[bool] = mapped_column(default=True, server_default="true")
    detalle: Mapped[dict | None] = mapped_column(JSONB)  # sin datos personales
    ip: Mapped[str | None] = mapped_column(String(45))

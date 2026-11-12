"""Conocimiento documental (RAG). Separado de los datos transaccionales (citas, pacientes)."""
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.models.base import Base, TimestampMixin, enum_type
from app.models.enums import CategoriaDocumento, EstadoDocumento

# Dimensión fija de la columna. Si cambia el modelo de embeddings hay que migrar la columna y reindexar.
EMBEDDING_DIM = get_settings().embedding_dimensions


class Documento(TimestampMixin, Base):
    __tablename__ = "documentos"
    __table_args__ = (
        CheckConstraint("vigente_hasta IS NULL OR vigente_hasta >= vigente_desde", name="vigencia_valida"),
        Index("uq_documentos_institucion_titulo_version", text("coalesce(institucion_id, 0)"), "titulo", "version", unique=True),
        Index("ix_documentos_estado_categoria", "estado", "categoria"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    institucion_id: Mapped[int | None] = mapped_column(ForeignKey("instituciones.id", ondelete="CASCADE"), index=True)  # vacío = de todas las instituciones
    titulo: Mapped[str] = mapped_column(String(250))
    categoria: Mapped[CategoriaDocumento] = mapped_column(enum_type(CategoriaDocumento))
    fuente: Mapped[str] = mapped_column(String(250))  # de dónde sale (área, norma, archivo)
    version: Mapped[str] = mapped_column(String(30))
    estado: Mapped[EstadoDocumento] = mapped_column(
        enum_type(EstadoDocumento), default=EstadoDocumento.BORRADOR, server_default=EstadoDocumento.BORRADOR.value
    )
    vigente_desde: Mapped[date | None] = mapped_column(Date)
    vigente_hasta: Mapped[date | None] = mapped_column(Date)
    sede_id: Mapped[int | None] = mapped_column(ForeignKey("sedes.id", ondelete="SET NULL"))
    especialidad_id: Mapped[int | None] = mapped_column(ForeignKey("especialidades.id", ondelete="SET NULL"))
    archivo_nombre: Mapped[str | None] = mapped_column(String(255))
    archivo_ruta: Mapped[str | None] = mapped_column(String(500))
    hash_contenido: Mapped[str | None] = mapped_column(String(64))
    texto_extraido: Mapped[str | None] = mapped_column(Text)  # permite reindexar sin volver a leer el archivo
    embedding_model: Mapped[str | None] = mapped_column(String(100))  # modelo con el que se indexó
    indexado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subido_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))

    fragmentos: Mapped[list["DocumentoFragmento"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan", order_by="DocumentoFragmento.orden"
    )


class DocumentoFragmento(Base):
    __tablename__ = "documento_fragmentos"
    __table_args__ = (
        UniqueConstraint("documento_id", "orden"),
        Index(
            "ix_documento_fragmentos_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos.id", ondelete="CASCADE"))
    orden: Mapped[int] = mapped_column(Integer)
    contenido: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))  # NULL = aún sin indexar

    documento: Mapped[Documento] = relationship(back_populates="fragmentos")

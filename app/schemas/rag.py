from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.enums import CategoriaDocumento, EstadoDocumento

Corto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=250)]


class DocumentoActualizar(BaseModel):
    """Solo metadatos. El contenido no se edita: se sube una nueva versión (así queda trazable lo que se citó)."""

    model_config = ConfigDict(extra="forbid")

    titulo: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=3, max_length=250)] = None
    categoria: CategoriaDocumento | None = None
    fuente: Corto | None = None
    version: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)] = None
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    sede_id: int | None = None
    especialidad_id: int | None = None


class DocumentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    categoria: CategoriaDocumento
    fuente: str
    version: str
    estado: EstadoDocumento
    vigente_desde: date | None
    vigente_hasta: date | None
    vigencia: str  # vigente | pendiente | vencido | sin_fecha
    sede_id: int | None
    especialidad_id: int | None
    archivo_nombre: str | None
    n_fragmentos: int
    n_indexados: int
    indexado: bool
    embedding_model: str | None
    indexado_en: datetime | None
    created_at: datetime
    advertencias: list[str] = []


class FragmentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    orden: int
    contenido: str
    tokens: int | None
    indexado: bool


class DocumentoDetalleOut(DocumentoOut):
    fragmentos: list[FragmentoOut]
    caracteres: int


class BuscarIn(BaseModel):
    consulta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=500)]
    sede_id: int | None = None
    especialidad_id: int | None = None
    categorias: list[CategoriaDocumento] | None = None
    k: int | None = Field(default=None, ge=1, le=20)
    incluir_vencidos: bool = True


class FuenteOut(BaseModel):
    fragmento_id: int
    documento_id: int
    titulo: str
    version: str
    categoria: str
    fuente: str
    vigencia: str
    desactualizado: bool
    contenido: str
    score: float
    sede: str | None
    especialidad: str | None


class BuscarOut(BaseModel):
    suficiente: bool  # False = nada supera el umbral: el asistente no debe responder como si lo supiera
    umbral: float
    mejor_score: float | None
    descartados_por_umbral: int
    resultados: list[FuenteOut]


class ReindexarIn(BaseModel):
    solo_pendientes: bool = True  # False = recalcular todos los vectores


class FaqCrear(BaseModel):
    pregunta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=400)]
    respuesta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=4000)]
    categoria: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    especialidad_id: int | None = None
    sede_id: int | None = None
    orden: int = Field(default=0, ge=0, le=999)
    activa: bool = True


class FaqActualizar(BaseModel):
    pregunta: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=5, max_length=400)] = None
    respuesta: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=5, max_length=4000)] = None
    categoria: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    especialidad_id: int | None = None
    sede_id: int | None = None
    orden: int | None = Field(default=None, ge=0, le=999)
    activa: bool | None = None


class FaqOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pregunta: str
    respuesta: str
    categoria: str | None
    especialidad_id: int | None
    sede_id: int | None
    orden: int
    activa: bool
    documento_id: int | None

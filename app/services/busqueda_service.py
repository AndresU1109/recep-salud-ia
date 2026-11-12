"""Búsqueda semántica sobre el conocimiento documental (nunca sobre datos transaccionales de pacientes o citas).

Reglas: solo documentos ACTIVOS indexados con el modelo actual; no vigentes todavía → fuera; vencidos → se devuelven
marcados como desactualizados; si nada supera el umbral, ``suficiente=False`` y el asistente debe decir que no tiene la información.
"""
from dataclasses import dataclass, field

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.embeddings import cliente
from app.models import Documento, DocumentoFragmento, Especialidad, Sede
from app.models.enums import CategoriaDocumento, EstadoDocumento
from app.services import documento_service, politica_service

MAX_POR_DOCUMENTO = 3


@dataclass
class Fuente:
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


@dataclass
class ResultadoBusqueda:
    suficiente: bool
    umbral: float
    mejor_score: float | None
    resultados: list[Fuente] = field(default_factory=list)
    descartados_por_umbral: int = 0


def buscar(db: Session, consulta: str, *, sede_id: int | None = None, especialidad_id: int | None = None,
           categorias: list[CategoriaDocumento] | None = None, k: int | None = None, incluir_vencidos: bool = True,
           institucion_id: int | None = None) -> ResultadoBusqueda:
    consulta = (consulta or "").strip()
    if len(consulta) < 2:
        raise ErrorNegocio("consulta_corta", "Escribe una consulta de al menos 2 caracteres.", 422)
    s = get_settings()
    k = k or s.rag_k
    umbral = s.umbral_rag()
    hoy = politica_service.hoy_local()
    vector = cliente.obtener_embedder().embed_consulta(consulta[:1000])

    distancia = DocumentoFragmento.embedding.cosine_distance(vector).label("distancia")
    q = (select(DocumentoFragmento, Documento, distancia, Sede.nombre, Especialidad.nombre)
         .join(Documento, Documento.id == DocumentoFragmento.documento_id).outerjoin(Sede, Sede.id == Documento.sede_id)
         .outerjoin(Especialidad, Especialidad.id == Documento.especialidad_id)
         .where(Documento.estado == EstadoDocumento.ACTIVO, Documento.embedding_model == s.embedding_model, DocumentoFragmento.embedding.is_not(None),
                or_(Documento.vigente_desde.is_(None), Documento.vigente_desde <= hoy)))
    if not incluir_vencidos:
        q = q.where(or_(Documento.vigente_hasta.is_(None), Documento.vigente_hasta >= hoy))
    if institucion_id is not None:  # el conocimiento de una institución nunca se le muestra a otra (solo lo compartido)
        q = q.where(or_(Documento.institucion_id.is_(None), Documento.institucion_id == institucion_id))
    # Un documento «de la sede X» solo aplica en X; uno sin sede es general y aplica en todas.
    if sede_id:
        q = q.where(or_(Documento.sede_id.is_(None), Documento.sede_id == sede_id))
    if especialidad_id:
        q = q.where(or_(Documento.especialidad_id.is_(None), Documento.especialidad_id == especialidad_id))
    if categorias:
        q = q.where(Documento.categoria.in_(categorias))

    # Con filtros, el índice HNSW podría devolver menos filas de las pedidas: se pide el escaneo iterativo (pgvector >= 0.8).
    try:
        with db.begin_nested():
            db.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
            db.execute(text("SET LOCAL hnsw.ef_search = 100"))
    except Exception:  # noqa: BLE001 - versiones antiguas de pgvector no tienen estos parámetros
        pass
    filas = db.execute(q.order_by(distancia).limit(max(k * 4, 20))).all()

    resultados: list[Fuente] = []
    por_doc: dict[int, int] = {}
    descartados = 0
    mejor = None
    for frag, doc, dist, sede, esp in filas:
        puntaje = round(1.0 - float(dist), 4)
        mejor = puntaje if mejor is None else max(mejor, puntaje)
        if puntaje < umbral:
            descartados += 1
            continue
        if por_doc.get(doc.id, 0) >= MAX_POR_DOCUMENTO or len(resultados) >= k:
            continue
        por_doc[doc.id] = por_doc.get(doc.id, 0) + 1
        vig = documento_service.vigencia(doc, hoy)
        resultados.append(Fuente(fragmento_id=frag.id, documento_id=doc.id, titulo=doc.titulo, version=doc.version, categoria=doc.categoria.value,
                                 fuente=doc.fuente, vigencia=vig, desactualizado=vig == "vencido", contenido=frag.contenido, score=puntaje, sede=sede, especialidad=esp))
    return ResultadoBusqueda(suficiente=bool(resultados), umbral=umbral, mejor_score=mejor, resultados=resultados, descartados_por_umbral=descartados)


def construir_contexto(resultados: list[Fuente]) -> str:
    """Texto numerado para el LLM. Cada bloque lleva su fuente para que la respuesta pueda citarla como [n]."""
    bloques = []
    for i, r in enumerate(resultados, 1):
        marca = "; DOCUMENTO VENCIDO: la información puede estar desactualizada" if r.desactualizado else ""
        alcance = " · ".join(x for x in (r.sede, r.especialidad) if x)
        bloques.append(f"[{i}] {r.titulo} (versión {r.version}; fuente: {r.fuente}{'; aplica a: ' + alcance if alcance else ''}{marca})\n{r.contenido}")
    return "\n\n---\n\n".join(bloques)

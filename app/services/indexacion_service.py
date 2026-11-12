"""Indexación de documentos: calcula y guarda los embeddings. Un documento solo cuenta como indexado cuando TODOS sus
fragmentos tienen vector y fue calculado con el modelo configurado (si cambia el modelo, hay que reindexar)."""
import threading
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.database.session import get_session_factory
from app.embeddings import cliente
from app.models import Documento, DocumentoFragmento
from app.models.enums import EstadoDocumento
from app.services import auditoria, health_service


def indexar_documento(db: Session, doc_id: int, *, forzar: bool = False) -> int:
    """Devuelve cuántos fragmentos se calcularon. Sin forzar, solo los pendientes (o todos si el modelo cambió)."""
    modelo = get_settings().embedding_model
    doc = db.get(Documento, doc_id)
    if doc is None:
        raise ErrorNegocio("documento_no_encontrado", "El documento no existe.", 404)
    frags = list(db.scalars(select(DocumentoFragmento).where(DocumentoFragmento.documento_id == doc_id).order_by(DocumentoFragmento.orden)))
    if not frags:
        raise ErrorNegocio("sin_fragmentos", "El documento no tiene contenido para indexar.", 422)
    todos = forzar or (doc.embedding_model is not None and doc.embedding_model != modelo)
    pendientes = frags if todos else [f for f in frags if f.embedding is None]
    if not pendientes:
        return 0
    vectores = cliente.obtener_embedder().embed_documentos([f.contenido for f in pendientes])
    for f, v in zip(pendientes, vectores):
        f.embedding = v
    if all(f.embedding is not None for f in frags):
        doc.embedding_model, doc.indexado_en = modelo, datetime.now(timezone.utc)
    auditoria.registrar(db, accion="documento_indexado", entidad="documento", entidad_id=doc.id, detalle={"fragmentos": len(pendientes), "modelo": modelo})
    db.commit()
    return len(pendientes)


def intentar_indexar(db: Session, doc_id: int) -> str | None:
    """Indexa sin romper la operación que lo pidió: si falla, devuelve un aviso en vez de lanzar error."""
    try:
        indexar_documento(db, doc_id)
        return None
    except ErrorNegocio as e:
        db.rollback()
        return f"No se pudo indexar ahora ({e.mensaje}). Reintenta desde «Estado del índice»."


def estado_indice(db: Session) -> dict:
    s = get_settings()
    modelo = s.embedding_model
    dim_columna = health_service.dimension_columna_vectores(db.connection())
    docs = db.execute(select(Documento.id, Documento.estado, Documento.embedding_model)).all()
    sin_vector = set(db.scalars(select(DocumentoFragmento.documento_id).where(DocumentoFragmento.embedding.is_(None)).distinct()))
    actuales = [d for d in docs if d.embedding_model == modelo and d.id not in sin_vector]
    obsoletos = [d for d in docs if d.embedding_model not in (None, modelo)]
    pendientes = [d for d in docs if d.embedding_model is None or d.id in sin_vector]
    activos_no_buscables = [d for d in docs if d.estado == EstadoDocumento.ACTIVO and d not in actuales]
    ollama = health_service.revisar_ollama(s)
    return {
        "modelo": modelo, "dimensiones": s.embedding_dimensions, "dimension_columna": dim_columna, "umbral": s.umbral_rag(),
        "dimension_consistente": dim_columna == s.embedding_dimensions, "ollama": ollama,
        "documentos": {"total": len(docs), "indexados": len(actuales), "con_otro_modelo": len(obsoletos), "pendientes": len(pendientes),
                       "activos_sin_poder_buscarse": len(activos_no_buscables)},
        "fragmentos": db.scalar(select(func.count(DocumentoFragmento.id))) or 0,
        "requiere_reindexar": bool(obsoletos or pendientes),
    }


# ---------- reindexado de todo el corpus (en segundo plano) ----------
_trabajo: dict = {"estado": "inactivo", "total": 0, "hechos": 0, "errores": [], "iniciado_en": None, "terminado_en": None}
_candado = threading.Lock()


def estado_trabajo() -> dict:
    with _candado:
        return dict(_trabajo, errores=list(_trabajo["errores"]))


def _ids_a_reindexar(db: Session, solo_pendientes: bool) -> list[int]:
    modelo = get_settings().embedding_model
    sin_vector = set(db.scalars(select(DocumentoFragmento.documento_id).where(DocumentoFragmento.embedding.is_(None)).distinct()))
    docs = db.execute(select(Documento.id, Documento.embedding_model).order_by(Documento.id)).all()
    return [d.id for d in docs if not solo_pendientes or d.embedding_model != modelo or d.id in sin_vector]


def iniciar_reindexado(db: Session, solo_pendientes: bool = True) -> dict:
    ids = _ids_a_reindexar(db, solo_pendientes)
    with _candado:
        if _trabajo["estado"] == "ejecutando":
            raise ErrorNegocio("trabajo_en_curso", "Ya hay un reindexado en curso.", 409)
        _trabajo.update(estado="ejecutando", total=len(ids), hechos=0, errores=[], iniciado_en=datetime.now(timezone.utc).isoformat(), terminado_en=None)
    if not ids:
        with _candado:
            _trabajo.update(estado="terminado", terminado_en=datetime.now(timezone.utc).isoformat())
        return estado_trabajo()
    threading.Thread(target=_ejecutar, args=(ids, not solo_pendientes), daemon=True).start()
    return estado_trabajo()


def _ejecutar(ids: list[int], forzar: bool) -> None:
    with get_session_factory()() as db:
        for doc_id in ids:
            try:
                indexar_documento(db, doc_id, forzar=forzar)
            except Exception as exc:  # noqa: BLE001 - un documento con error no debe frenar a los demás
                db.rollback()
                with _candado:
                    _trabajo["errores"].append({"documento_id": doc_id, "error": getattr(exc, "mensaje", type(exc).__name__)})
            with _candado:
                _trabajo["hechos"] += 1
    with _candado:
        _trabajo.update(estado="terminado", terminado_en=datetime.now(timezone.utc).isoformat())


def _ollama_ok() -> bool:  # usado por pruebas para saltarse si no hay Ollama
    try:
        return httpx.get(f"{get_settings().ollama_base_url}/api/tags", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False

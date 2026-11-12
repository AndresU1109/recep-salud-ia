"""Preguntas frecuentes. Cada una se refleja en un Documento (PREGUNTAS_FRECUENTES) de un solo fragmento, así el RAG
las recupera, filtra y cita igual que cualquier otra fuente."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Documento, DocumentoFragmento, PreguntaFrecuente, Usuario
from app.models.enums import CategoriaDocumento, EstadoDocumento
from app.rag import chunking
from app.services import auditoria, documento_service, indexacion_service


def _texto(f: PreguntaFrecuente) -> str:
    return f"Pregunta: {f.pregunta}\nRespuesta: {f.respuesta}"


def _sincronizar(db: Session, f: PreguntaFrecuente, actor: Usuario) -> None:
    """Deja el Documento espejo igual a la pregunta. Si cambió el texto, el fragmento se rehace y queda sin indexar."""
    estado = EstadoDocumento.ACTIVO if f.activa else EstadoDocumento.INACTIVO
    doc = db.get(Documento, f.documento_id) if f.documento_id else None
    texto = _texto(f)
    if doc is None:
        doc, _ = documento_service.crear(
            db, actor, {"titulo": f"FAQ: {f.pregunta[:150]}", "categoria": CategoriaDocumento.PREGUNTAS_FRECUENTES, "fuente": "Preguntas frecuentes",
                        "version": f"faq-{f.id}", "sede_id": f.sede_id, "especialidad_id": f.especialidad_id, "institucion_id": f.institucion_id}, texto=texto, estado=estado, commit=False)
        f.documento_id = doc.id
        return
    doc.titulo, doc.estado, doc.sede_id, doc.especialidad_id, doc.institucion_id = f"FAQ: {f.pregunta[:150]}", estado, f.sede_id, f.especialidad_id, f.institucion_id
    if doc.texto_extraido != texto:
        doc.texto_extraido = texto
        for frag in list(doc.fragmentos):
            db.delete(frag)
        db.flush()
        doc.fragmentos = [DocumentoFragmento(orden=0, contenido=texto, tokens=chunking.tokens_aprox(texto))]
        doc.embedding_model, doc.indexado_en = None, None  # hay que reindexar


def listar(db: Session, solo_activas: bool = False) -> list[PreguntaFrecuente]:
    q = select(PreguntaFrecuente).order_by(PreguntaFrecuente.orden, PreguntaFrecuente.id)
    return list(db.scalars(q.where(PreguntaFrecuente.activa) if solo_activas else q))


def obtener(db: Session, faq_id: int) -> PreguntaFrecuente:
    f = db.get(PreguntaFrecuente, faq_id)
    if f is None:
        raise ErrorNegocio("no_encontrado", "La pregunta frecuente no existe.", 404)
    return f


def crear(db: Session, actor: Usuario, datos: dict) -> PreguntaFrecuente:
    documento_service._validar_referencias(db, datos.get("sede_id"), datos.get("especialidad_id"))
    f = PreguntaFrecuente(**datos)
    db.add(f)
    db.flush()
    _sincronizar(db, f, actor)
    auditoria.registrar(db, accion="faq_creada", usuario_id=actor.id, entidad="faq", entidad_id=f.id)
    db.commit()
    indexacion_service.intentar_indexar(db, f.documento_id)
    return f


def actualizar(db: Session, actor: Usuario, faq_id: int, datos: dict) -> PreguntaFrecuente:
    f = obtener(db, faq_id)
    datos = {k: v for k, v in datos.items() if v is not None or k in ("categoria", "sede_id", "especialidad_id")}
    documento_service._validar_referencias(db, datos.get("sede_id"), datos.get("especialidad_id"))
    for k, v in datos.items():
        setattr(f, k, v)
    _sincronizar(db, f, actor)
    auditoria.registrar(db, accion="faq_actualizada", usuario_id=actor.id, entidad="faq", entidad_id=f.id, detalle={"campos": sorted(datos)})
    db.commit()
    if f.activa:
        indexacion_service.intentar_indexar(db, f.documento_id)
    return f

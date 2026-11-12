"""Documentos de conocimiento: carga, versiones, estados y metadatos. La indexación (embeddings) es de la Fase 9."""
import hashlib
import uuid
from datetime import date
from pathlib import Path, PurePath

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.core.logging import redactar
from app.models import Documento, DocumentoFragmento, Especialidad, Sede, Usuario
from app.models.enums import CategoriaDocumento, EstadoDocumento
from app.rag import chunking, extractores
from app.rag.limpieza import limpiar
from app.services import auditoria, indexacion_service, politica_service

METADATOS = ("titulo", "categoria", "fuente", "version", "vigente_desde", "vigente_hasta", "sede_id", "especialidad_id", "institucion_id")


def _ruta_subidas() -> Path:
    ruta = Path(get_settings().uploads_dir)
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def vigencia(d: Documento, hoy: date | None = None) -> str:
    hoy = hoy or politica_service.hoy_local()
    if d.vigente_desde is None and d.vigente_hasta is None:
        return "sin_fecha"
    if d.vigente_desde and d.vigente_desde > hoy:
        return "pendiente"
    if d.vigente_hasta and d.vigente_hasta < hoy:
        return "vencido"
    return "vigente"


def conteos(db: Session, doc_id: int) -> tuple[int, int]:
    total, indexados = db.execute(select(func.count(DocumentoFragmento.id), func.count(DocumentoFragmento.embedding))
                                  .where(DocumentoFragmento.documento_id == doc_id)).one()
    return total, indexados


def obtener(db: Session, doc_id: int) -> Documento:
    d = db.get(Documento, doc_id)
    if d is None:
        raise ErrorNegocio("documento_no_encontrado", "El documento no existe.", 404)
    return d


def _validar_referencias(db: Session, sede_id: int | None, especialidad_id: int | None) -> None:
    if sede_id and db.get(Sede, sede_id) is None:
        raise ErrorNegocio("referencia_invalida", "La sede no existe.", 422)
    if especialidad_id and db.get(Especialidad, especialidad_id) is None:
        raise ErrorNegocio("referencia_invalida", "La especialidad no existe.", 422)


def _advertencias_texto(texto: str, db: Session, hash_: str, doc_id: int | None) -> list[str]:
    avisos = []
    if redactar(texto) != texto:  # el filtro de logs oculta correos, teléfonos y documentos: si cambia algo, había alguno
        avisos.append("El texto contiene posibles datos personales (correos, teléfonos o números de documento). "
                      "Verifica que sea información institucional y no datos de pacientes.")
    otro = db.scalar(select(Documento.titulo).where(Documento.hash_contenido == hash_, Documento.id != (doc_id or 0)).limit(1))
    if otro:
        avisos.append(f"Ya existe un documento con exactamente el mismo contenido («{otro}»).")
    return avisos


def crear(db: Session, actor: Usuario, metadatos: dict, *, nombre_archivo: str | None = None, contenido: bytes | None = None,
          texto: str | None = None, estado: EstadoDocumento = EstadoDocumento.BORRADOR, commit: bool = True) -> tuple[Documento, list[str]]:
    """Crea un documento (borrador) desde un archivo o desde texto pegado. Devuelve (documento, advertencias)."""
    ajustes = get_settings()
    if bool(contenido) == bool(texto):
        raise ErrorNegocio("fuente_invalida", "Envía un archivo o un texto, no ambos ni ninguno.", 422)
    if contenido is not None and len(contenido) > ajustes.max_upload_mb * 1024 * 1024:
        raise ErrorNegocio("archivo_muy_grande", f"El archivo supera el máximo de {ajustes.max_upload_mb} MB.", 413)
    _validar_referencias(db, metadatos.get("sede_id"), metadatos.get("especialidad_id"))
    d_ini, d_fin = metadatos.get("vigente_desde"), metadatos.get("vigente_hasta")
    if d_ini and d_fin and d_fin < d_ini:
        raise ErrorNegocio("vigencia_invalida", "La vigencia termina antes de empezar.", 422)

    if contenido is not None:
        limpio = extractores.extraer_texto(nombre_archivo or "", contenido)
    else:
        limpio = limpiar(texto)
        if len(limpio) < 20:
            raise ErrorNegocio("sin_texto", "El texto es demasiado corto para ser un documento.", 422)
    fragmentos = chunking.dividir(limpio, ajustes.chunk_max_chars, ajustes.chunk_overlap_chars)
    hash_ = hashlib.sha256(limpio.encode()).hexdigest()

    ruta_guardada = None
    nombre_visible = None
    if contenido is not None:
        nombre_visible = PurePath(nombre_archivo or "archivo").name[:255]
        ruta_guardada = _ruta_subidas() / f"{uuid.uuid4().hex}{PurePath(nombre_visible).suffix.lower()}"  # nunca el nombre del usuario
        ruta_guardada.write_bytes(contenido)

    doc = Documento(**{k: metadatos.get(k) for k in METADATOS}, estado=estado, archivo_nombre=nombre_visible,
                    archivo_ruta=str(ruta_guardada) if ruta_guardada else None, hash_contenido=hash_, texto_extraido=limpio, subido_por=actor.id if actor else None)
    doc.fragmentos = [DocumentoFragmento(orden=i, contenido=f, tokens=chunking.tokens_aprox(f)) for i, f in enumerate(fragmentos)]
    db.add(doc)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if ruta_guardada:
            ruta_guardada.unlink(missing_ok=True)
        raise ErrorNegocio("documento_duplicado", "Ya existe un documento con ese título y versión.", 409) from exc
    auditoria.registrar(db, accion="documento_creado", usuario_id=actor.id if actor else None, entidad="documento", entidad_id=doc.id,
                        detalle={"categoria": doc.categoria.value, "fragmentos": len(fragmentos)})
    avisos = _advertencias_texto(limpio, db, hash_, doc.id)
    if commit:
        db.commit()
    return doc, avisos


def nueva_version(db: Session, actor: Usuario, doc_id: int, version: str, **fuente) -> tuple[Documento, list[str]]:
    """Copia los metadatos del documento y le pone otro contenido y versión (queda en BORRADOR; el anterior no se toca)."""
    base = obtener(db, doc_id)
    metadatos = {k: getattr(base, k) for k in METADATOS}
    metadatos["version"] = version.strip()
    return crear(db, actor, metadatos, **fuente)


def actualizar_metadatos(db: Session, actor: Usuario, doc_id: int, datos: dict) -> Documento:
    d = obtener(db, doc_id)
    datos = {k: v for k, v in datos.items() if v is not None or k in ("vigente_desde", "vigente_hasta", "sede_id", "especialidad_id")}
    _validar_referencias(db, datos.get("sede_id"), datos.get("especialidad_id"))
    for k, v in datos.items():
        setattr(d, k, v)
    if d.vigente_desde and d.vigente_hasta and d.vigente_hasta < d.vigente_desde:
        db.rollback()
        raise ErrorNegocio("vigencia_invalida", "La vigencia termina antes de empezar.", 422)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ErrorNegocio("documento_duplicado", "Ya existe un documento con ese título y versión.", 409) from exc
    auditoria.registrar(db, accion="documento_actualizado", usuario_id=actor.id, entidad="documento", entidad_id=d.id, detalle={"campos": sorted(datos)})
    db.commit()
    return d


def cambiar_estado(db: Session, actor: Usuario, doc_id: int, estado: EstadoDocumento) -> tuple[Documento, list[str]]:
    d = obtener(db, doc_id)
    avisos: list[str] = []
    if estado == EstadoDocumento.ACTIVO:
        total, indexados = conteos(db, d.id)
        if total == 0:
            raise ErrorNegocio("sin_fragmentos", "El documento no tiene contenido para activar.", 422)
        if vigencia(d) == "vencido":
            avisos.append("El documento está vencido: el asistente avisará que la información puede estar desactualizada.")
        if indexados < total:
            avisos.append("Aún no está indexado: no aparecerá en las búsquedas hasta que se indexe.")
    anterior, d.estado = d.estado, estado
    auditoria.registrar(db, accion=f"documento_{estado.value.lower()}", usuario_id=actor.id, entidad="documento", entidad_id=d.id,
                        detalle={"antes": anterior.value})
    db.commit()
    if estado == EstadoDocumento.ACTIVO:  # al activar se indexa para que sea consultable (si falla, se avisa y se reintenta después)
        fallo = indexacion_service.intentar_indexar(db, d.id)
        avisos = [a for a in avisos if "indexado" not in a] + ([fallo] if fallo else [])
    return d, avisos


def listar(db: Session, *, estado=None, categoria=None, sede_id=None, especialidad_id=None, texto: str | None = None) -> list[Documento]:
    q = select(Documento).order_by(Documento.created_at.desc(), Documento.id.desc()).limit(500)
    if estado:
        q = q.where(Documento.estado == estado)
    if categoria:
        q = q.where(Documento.categoria == categoria)
    if sede_id:
        q = q.where(Documento.sede_id == sede_id)
    if especialidad_id:
        q = q.where(Documento.especialidad_id == especialidad_id)
    if texto:
        patron = "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        q = q.where(Documento.titulo.ilike(patron, escape="\\"))
    return list(db.scalars(q))


def fragmentos(db: Session, doc_id: int, limite: int = 200) -> list[DocumentoFragmento]:
    return list(db.scalars(select(DocumentoFragmento).where(DocumentoFragmento.documento_id == doc_id).order_by(DocumentoFragmento.orden).limit(limite)))


def ruta_original(db: Session, doc_id: int) -> tuple[Path, str]:
    d = obtener(db, doc_id)
    if not d.archivo_ruta:
        raise ErrorNegocio("sin_archivo", "Este documento se creó con texto, no tiene archivo original.", 404)
    ruta = Path(d.archivo_ruta).resolve()
    if _ruta_subidas().resolve() not in ruta.parents or not ruta.is_file():  # nunca servir nada fuera de la carpeta de subidas
        raise ErrorNegocio("sin_archivo", "El archivo original no está disponible.", 404)
    return ruta, d.archivo_nombre or ruta.name

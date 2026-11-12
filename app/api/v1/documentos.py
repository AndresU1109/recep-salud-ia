"""Base de conocimiento: documentos y preguntas frecuentes. Todo requiere documentos:gestionar."""
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import Documento, Usuario
from app.models.enums import CategoriaDocumento, EstadoDocumento
from app.schemas import rag as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import documento_service as svc
from app.services import faq_service

router = APIRouter(tags=["Base de conocimiento (RAG)"])
gestion = Depends(requiere(Permiso.DOCUMENTOS_GESTIONAR))


def _out(db: Session, d: Documento, avisos: list[str] | None = None) -> s.DocumentoOut:
    total, indexados = svc.conteos(db, d.id)
    return s.DocumentoOut(
        id=d.id, titulo=d.titulo, categoria=d.categoria, fuente=d.fuente, version=d.version, estado=d.estado, vigente_desde=d.vigente_desde,
        vigente_hasta=d.vigente_hasta, vigencia=svc.vigencia(d), sede_id=d.sede_id, especialidad_id=d.especialidad_id, archivo_nombre=d.archivo_nombre,
        n_fragmentos=total, n_indexados=indexados, indexado=total > 0 and indexados == total, embedding_model=d.embedding_model, indexado_en=d.indexado_en,
        created_at=d.created_at, advertencias=avisos or [])


async def _leer(archivo: UploadFile | None) -> tuple[str | None, bytes | None]:
    if archivo is None or not archivo.filename:
        return None, None
    limite = get_settings().max_upload_mb * 1024 * 1024
    datos = await archivo.read(limite + 1)  # no se lee más de lo permitido
    if len(datos) > limite:
        raise ErrorNegocio("archivo_muy_grande", f"El archivo supera el máximo de {get_settings().max_upload_mb} MB.", 413)
    return archivo.filename, datos


# ---------- documentos ----------
@router.get("/documentos", response_model=list[s.DocumentoOut])
def listar(estado: EstadoDocumento | None = None, categoria: CategoriaDocumento | None = None, sede_id: int | None = None,
           especialidad_id: int | None = None, q: str | None = Query(None, max_length=100), _: Usuario = gestion, db: Session = Depends(get_db)):
    return [_out(db, d) for d in svc.listar(db, estado=estado, categoria=categoria, sede_id=sede_id, especialidad_id=especialidad_id, texto=q)]


@router.post("/documentos", response_model=s.DocumentoOut, status_code=status.HTTP_201_CREATED, summary="Subir un archivo (PDF, DOCX, TXT, MD) o pegar texto")
async def subir(titulo: Annotated[str, Form(min_length=3, max_length=250)], categoria: Annotated[CategoriaDocumento, Form()],
                fuente: Annotated[str, Form(min_length=2, max_length=250)], version: Annotated[str, Form(min_length=1, max_length=30)],
                vigente_desde: Annotated[date | None, Form()] = None, vigente_hasta: Annotated[date | None, Form()] = None,
                sede_id: Annotated[int | None, Form()] = None, especialidad_id: Annotated[int | None, Form()] = None,
                texto: Annotated[str | None, Form(max_length=500000)] = None, archivo: UploadFile | None = File(None),
                actor: Usuario = gestion, db: Session = Depends(get_db)):
    nombre, contenido = await _leer(archivo)
    meta = {"titulo": titulo.strip(), "categoria": categoria, "fuente": fuente.strip(), "version": version.strip(), "vigente_desde": vigente_desde,
            "vigente_hasta": vigente_hasta, "sede_id": sede_id, "especialidad_id": especialidad_id}
    d, avisos = svc.crear(db, actor, meta, nombre_archivo=nombre, contenido=contenido, texto=texto if contenido is None else None)
    return _out(db, d, avisos)


@router.get("/documentos/{doc_id}", response_model=s.DocumentoDetalleOut)
def detalle(doc_id: int, _: Usuario = gestion, db: Session = Depends(get_db)):
    d = svc.obtener(db, doc_id)
    base = _out(db, d)
    frags = [s.FragmentoOut(id=f.id, orden=f.orden, contenido=f.contenido, tokens=f.tokens, indexado=f.embedding is not None) for f in svc.fragmentos(db, doc_id)]
    return s.DocumentoDetalleOut(**base.model_dump(), fragmentos=frags, caracteres=len(d.texto_extraido or ""))


@router.patch("/documentos/{doc_id}", response_model=s.DocumentoOut, summary="Editar metadatos (el contenido se cambia con una nueva versión)")
def actualizar(doc_id: int, datos: s.DocumentoActualizar, actor: Usuario = gestion, db: Session = Depends(get_db)):
    return _out(db, svc.actualizar_metadatos(db, actor, doc_id, datos.model_dump(exclude_unset=True)))


@router.post("/documentos/{doc_id}/activar", response_model=s.DocumentoOut)
def activar(doc_id: int, actor: Usuario = gestion, db: Session = Depends(get_db)):
    d, avisos = svc.cambiar_estado(db, actor, doc_id, EstadoDocumento.ACTIVO)
    return _out(db, d, avisos)


@router.post("/documentos/{doc_id}/desactivar", response_model=s.DocumentoOut)
def desactivar(doc_id: int, actor: Usuario = gestion, db: Session = Depends(get_db)):
    d, avisos = svc.cambiar_estado(db, actor, doc_id, EstadoDocumento.INACTIVO)
    return _out(db, d, avisos)


@router.post("/documentos/{doc_id}/nueva-version", response_model=s.DocumentoOut, status_code=status.HTTP_201_CREATED,
             summary="Crear una nueva versión (borrador) con otro contenido")
async def nueva_version(doc_id: int, version: Annotated[str, Form(min_length=1, max_length=30)], texto: Annotated[str | None, Form(max_length=500000)] = None,
                        archivo: UploadFile | None = File(None), actor: Usuario = gestion, db: Session = Depends(get_db)):
    nombre, contenido = await _leer(archivo)
    d, avisos = svc.nueva_version(db, actor, doc_id, version, nombre_archivo=nombre, contenido=contenido, texto=texto if contenido is None else None)
    return _out(db, d, avisos)


@router.get("/documentos/{doc_id}/archivo", summary="Descargar el archivo original")
def descargar(doc_id: int, _: Usuario = gestion, db: Session = Depends(get_db)):
    ruta, nombre = svc.ruta_original(db, doc_id)
    return FileResponse(ruta, filename=nombre, headers={"X-Content-Type-Options": "nosniff"})


# ---------- preguntas frecuentes ----------
@router.get("/faqs", response_model=list[s.FaqOut])
def listar_faqs(_: Usuario = gestion, db: Session = Depends(get_db)):
    return faq_service.listar(db)


@router.post("/faqs", response_model=s.FaqOut, status_code=status.HTTP_201_CREATED)
def crear_faq(datos: s.FaqCrear, actor: Usuario = gestion, db: Session = Depends(get_db)):
    return faq_service.crear(db, actor, datos.model_dump())


@router.patch("/faqs/{faq_id}", response_model=s.FaqOut)
def actualizar_faq(faq_id: int, datos: s.FaqActualizar, actor: Usuario = gestion, db: Session = Depends(get_db)):
    return faq_service.actualizar(db, actor, faq_id, datos.model_dump(exclude_unset=True))

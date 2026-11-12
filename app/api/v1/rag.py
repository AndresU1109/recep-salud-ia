"""Búsqueda semántica e indexación. Permiso: documentos:gestionar (el agente usa el servicio directamente, no esta API)."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.schemas import rag as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import auditoria, busqueda_service, indexacion_service

router = APIRouter(prefix="/rag", tags=["Base de conocimiento (RAG)"])
gestion = Depends(requiere(Permiso.DOCUMENTOS_GESTIONAR))


@router.post("/buscar", response_model=s.BuscarOut, summary="Probar la búsqueda semántica")
def buscar(datos: s.BuscarIn, usuario: Usuario = gestion, db: Session = Depends(get_db)):
    r = busqueda_service.buscar(db, datos.consulta, sede_id=datos.sede_id, especialidad_id=datos.especialidad_id, categorias=datos.categorias,
                                k=datos.k, incluir_vencidos=datos.incluir_vencidos)
    # Lo que se buscó puede contener datos personales: se audita solo el resultado, no el texto.
    auditoria.registrar(db, accion="rag_busqueda", usuario_id=usuario.id, detalle={"resultados": len(r.resultados), "suficiente": r.suficiente})
    db.commit()
    return s.BuscarOut(suficiente=r.suficiente, umbral=r.umbral, mejor_score=r.mejor_score, descartados_por_umbral=r.descartados_por_umbral,
                       resultados=[s.FuenteOut(**asdict(x)) for x in r.resultados])


@router.get("/estado", summary="Estado del índice: modelo, dimensiones y documentos pendientes")
def estado(_: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return indexacion_service.estado_indice(db)


@router.post("/documentos/{doc_id}/indexar", summary="(Re)indexar un documento")
def indexar(doc_id: int, forzar: bool = True, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return {"fragmentos_calculados": indexacion_service.indexar_documento(db, doc_id, forzar=forzar)}


@router.post("/reindexar", status_code=status.HTTP_202_ACCEPTED, summary="Reindexar el corpus en segundo plano")
def reindexar(datos: s.ReindexarIn, _: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return indexacion_service.iniciar_reindexado(db, datos.solo_pendientes)


@router.get("/reindexar/estado")
def estado_reindexado(_: Usuario = gestion) -> dict:
    return indexacion_service.estado_trabajo()

"""Reportes agregados y su exportación a Excel/PDF. Cada exportación queda auditada (quién, qué reporte, qué período; nunca el contenido)."""
import re
from dataclasses import asdict
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import auditoria, exportacion, reporte_service

router = APIRouter(prefix="/reportes", tags=["Reportes"])
ver = Depends(requiere(Permiso.REPORTES_VER))
TIPOS = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}


@router.get("", summary="Reportes disponibles")
def disponibles(_: Usuario = ver) -> list[dict]:
    return [{"clave": c, "titulo": reporte_service.TITULOS[c]} for c in reporte_service.CLAVES]


@router.get("/{clave}", summary="Un reporte en JSON (por defecto, los últimos 30 días)")
def reporte(clave: str, desde: date | None = None, hasta: date | None = None, _: Usuario = ver, db: Session = Depends(get_db)) -> dict:
    return asdict(reporte_service.generar(db, clave, desde, hasta))


@router.get("/{clave}/exportar", summary="Descargar el reporte en Excel o PDF", response_class=Response)
async def exportar(clave: str, formato: Literal["xlsx", "pdf"] = "xlsx", desde: date | None = None, hasta: date | None = None,
                   usuario: Usuario = ver, db: Session = Depends(get_db)) -> Response:
    r = await run_in_threadpool(reporte_service.generar, db, clave, desde, hasta)
    archivo = await run_in_threadpool(exportacion.reporte_a_excel if formato == "xlsx" else exportacion.reporte_a_pdf, r)
    auditoria.registrar(db, accion="reporte_exportado", usuario_id=usuario.id, detalle={"reporte": clave, "formato": formato, "desde": r.desde.isoformat(), "hasta": r.hasta.isoformat()})
    db.commit()
    nombre = re.sub(r"[^a-z0-9_-]", "", f"reporte-{clave}-{r.desde}-{r.hasta}".lower()) + f".{formato}"
    return Response(archivo, media_type=TIPOS[formato], headers={"Content-Disposition": f'attachment; filename="{nombre}"', "Cache-Control": "no-store"})

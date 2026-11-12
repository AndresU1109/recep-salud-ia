"""Consulta y exportación de la bitácora de auditoría (solo administradores). El registro es de solo escritura: aquí solo se lee."""
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import AuditoriaLog, Usuario
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import agenda_service, auditoria, exportacion

router = APIRouter(prefix="/auditoria", tags=["Auditoría"])
ver = Depends(requiere(Permiso.AUDITORIA_VER))
MAX_EXPORTAR = exportacion.MAX_FILAS_EXCEL


class RegistroOut(BaseModel):
    id: int
    ocurrio_en: datetime
    usuario: str | None  # nombre de quien actuó (personal); None si fue el agente o el sistema
    actor: str
    accion: str
    entidad: str | None
    entidad_id: str | None
    exito: bool
    detalle: dict | None
    ip: str | None


class Filtros:
    def __init__(self, accion: str | None = Query(None, max_length=80, description="Contiene este texto"), actor: str | None = Query(None, max_length=60),
                 usuario_id: int | None = None, entidad: str | None = Query(None, max_length=60), exito: bool | None = None,
                 desde: date | None = None, hasta: date | None = None):
        self.accion, self.actor, self.usuario_id, self.entidad, self.exito, self.desde, self.hasta = accion, actor, usuario_id, entidad, exito, desde, hasta

    def aplicar(self, q):
        tz = agenda_service._tz()
        if self.accion:
            texto = self.accion.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # un % o _ escrito por el usuario no es comodín
            q = q.where(AuditoriaLog.accion.ilike(f"%{texto}%", escape="\\"))
        if self.actor:
            q = q.where(AuditoriaLog.actor == self.actor)
        if self.usuario_id is not None:
            q = q.where(AuditoriaLog.usuario_id == self.usuario_id)
        if self.entidad:
            q = q.where(AuditoriaLog.entidad == self.entidad)
        if self.exito is not None:
            q = q.where(AuditoriaLog.exito.is_(self.exito))
        if self.desde:
            q = q.where(AuditoriaLog.ocurrio_en >= datetime.combine(self.desde, time.min, tzinfo=tz))
        if self.hasta:
            q = q.where(AuditoriaLog.ocurrio_en < datetime.combine(self.hasta + timedelta(days=1), time.min, tzinfo=tz))
        return q


def _consulta(f: Filtros):
    return f.aplicar(select(AuditoriaLog, Usuario.nombre).outerjoin(Usuario, Usuario.id == AuditoriaLog.usuario_id).order_by(AuditoriaLog.id.desc()))


def _out(reg: AuditoriaLog, nombre: str | None) -> RegistroOut:
    return RegistroOut(id=reg.id, ocurrio_en=reg.ocurrio_en, usuario=nombre, actor=reg.actor, accion=reg.accion, entidad=reg.entidad, entidad_id=reg.entidad_id,
                       exito=reg.exito, detalle=reg.detalle, ip=reg.ip)


@router.get("", summary="Bitácora (más reciente primero)")
def listar(f: Filtros = Depends(), limite: int = Query(50, ge=1, le=200), pagina: int = Query(1, ge=1, le=10_000), _: Usuario = ver, db: Session = Depends(get_db)) -> dict:
    total = db.scalar(f.aplicar(select(func.count()).select_from(AuditoriaLog)))
    filas = db.execute(_consulta(f).limit(limite).offset((pagina - 1) * limite)).all()
    return {"total": total, "pagina": pagina, "limite": limite, "registros": [_out(r, n) for r, n in filas]}


@router.get("/acciones", summary="Acciones registradas (para el filtro)")
def acciones(_: Usuario = ver, db: Session = Depends(get_db)) -> list[str]:
    return list(db.scalars(select(AuditoriaLog.accion).distinct().order_by(AuditoriaLog.accion).limit(500)))


@router.get("/exportar", summary="Descargar la bitácora filtrada en Excel", response_class=Response)
def exportar(f: Filtros = Depends(), usuario: Usuario = ver, db: Session = Depends(get_db)) -> Response:
    filas = db.execute(_consulta(f).limit(MAX_EXPORTAR + 1)).all()
    if not filas:
        raise ErrorNegocio("sin_datos", "No hay registros con esos filtros.", 404)
    truncado = len(filas) > MAX_EXPORTAR
    tz = agenda_service._tz()
    datos = [[r.id, r.ocurrio_en.astimezone(tz), n or "", r.actor, r.accion, r.entidad or "", r.entidad_id or "", "Sí" if r.exito else "No",
              "" if r.detalle is None else str(r.detalle), r.ip or ""] for r, n in filas[:MAX_EXPORTAR]]
    notas = [f"Bitácora de auditoría exportada por {usuario.nombre}. Horas en {tz.key}. El detalle nunca contiene datos personales."]
    if truncado:
        notas.append(f"Se exportaron solo los {MAX_EXPORTAR} registros más recientes; acota los filtros para ver el resto.")
    archivo = exportacion.filas_a_excel("Auditoría", ["Id", "Fecha", "Usuario", "Actor", "Acción", "Entidad", "Id entidad", "Éxito", "Detalle", "IP"], datos, notas)
    auditoria.registrar(db, accion="auditoria_exportada", usuario_id=usuario.id, detalle={"filas": len(datos), "truncado": truncado})
    db.commit()
    return Response(archivo, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="auditoria.xlsx"', "Cache-Control": "no-store"})

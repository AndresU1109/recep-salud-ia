"""Bandeja de avisos a pacientes. El contenido (asunto y cuerpo) solo lo ve quien puede gestionar citas; nunca se listan correos completos."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models import Notificacion, Usuario
from app.models.enums import CanalNotificacion, EstadoNotificacion, TipoNotificacion
from app.notificaciones import emisores
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import auditoria, notificacion_service

router = APIRouter(prefix="/notificaciones", tags=["Notificaciones"])
ver = Depends(requiere(Permiso.CITAS_VER))
gestionar = Depends(requiere(Permiso.CITAS_GESTIONAR))


class NotificacionOut(BaseModel):
    id: int
    paciente_id: int
    cita_id: int | None
    tipo: TipoNotificacion
    canal: CanalNotificacion
    estado: EstadoNotificacion
    destino_enmascarado: str | None
    asunto: str
    programada_para: datetime
    enviada_en: datetime | None
    intentos: int
    motivo: str | None

    model_config = {"from_attributes": True}


@router.get("/estado", summary="Qué canales hay y cuántos avisos hay en cada estado")
def estado(_: Usuario = ver, db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    correo = emisores.EmisorCorreo(s)
    conteo = dict(db.execute(select(Notificacion.estado, func.count()).group_by(Notificacion.estado)).all())
    return {"activas": s.notificaciones_activas,
            "canales": {"EMAIL": correo.configurado(), "SMS": False},  # SMS: sin proveedor integrado
            "conteo": {e.value: conteo.get(e, 0) for e in EstadoNotificacion},
            "recordatorio_horas_antes": s.recordatorio_horas_antes, "horario": [s.notificaciones_hora_desde, s.notificaciones_hora_hasta],
            "espera_notificar_maximo": s.espera_notificar_maximo}


@router.get("", response_model=list[NotificacionOut], summary="Avisos (más recientes primero)")
def listar(estado: EstadoNotificacion | None = None, tipo: TipoNotificacion | None = None, paciente_id: int | None = None,
           limite: int = Query(100, ge=1, le=300), _: Usuario = ver, db: Session = Depends(get_db)):
    q = select(Notificacion).order_by(Notificacion.id.desc()).limit(limite)
    for columna, valor in ((Notificacion.estado, estado), (Notificacion.tipo, tipo), (Notificacion.paciente_id, paciente_id)):
        if valor is not None:
            q = q.where(columna == valor)
    return list(db.scalars(q))


@router.post("/procesar", summary="Generar recordatorios y enviar lo pendiente ahora")
async def procesar(usuario: Usuario = gestionar, db: Session = Depends(get_db)) -> dict:
    nuevos = await run_in_threadpool(notificacion_service.generar_recordatorios, db)
    resultado = await run_in_threadpool(notificacion_service.procesar_pendientes, db)
    auditoria.registrar(db, accion="notificaciones_procesadas", usuario_id=usuario.id, detalle={"recordatorios_nuevos": nuevos, **resultado})
    db.commit()
    return {"recordatorios_nuevos": nuevos, **resultado}


@router.post("/{notificacion_id}/reintentar", response_model=NotificacionOut, summary="Volver a poner en cola un aviso que falló")
def reintentar(notificacion_id: int, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    n = notificacion_service.reintentar(db, notificacion_id)
    auditoria.registrar(db, accion="notificacion_reintentada", usuario_id=usuario.id, entidad="notificacion", entidad_id=n.id)
    db.commit()
    return n

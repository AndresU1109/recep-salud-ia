from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models import Cita, Documento, Especialidad, Paciente, Profesional, Sede, Usuario
from app.models.enums import EstadoCita, EstadoDocumento
from app.security.dependencias import usuario_actual
from app.security.permisos import Permiso, tiene_permiso

router = APIRouter(prefix="/panel", tags=["Panel"])


def _contar(db: Session, consulta) -> int:
    return db.scalar(select(func.count()).select_from(consulta.subquery())) or 0


@router.get("/resumen", summary="Cifras del inicio (solo las que el rol puede ver)")
def resumen(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)) -> dict:
    ve = lambda p: tiene_permiso(usuario.rol, p)  # noqa: E731
    cifras: dict[str, int] = {}
    if ve(Permiso.AGENDA_VER):
        cifras["sedes_activas"] = _contar(db, select(Sede).where(Sede.activa))
        cifras["especialidades_activas"] = _contar(db, select(Especialidad).where(Especialidad.activa))
        cifras["profesionales_activos"] = _contar(db, select(Profesional).where(Profesional.activo))
    if ve(Permiso.PACIENTES_VER):
        cifras["pacientes"] = _contar(db, select(Paciente).where(Paciente.activo))
    if ve(Permiso.CITAS_VER):
        tz = ZoneInfo(get_settings().timezone)
        hoy = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        manana = (hoy + timedelta(days=1)).astimezone(timezone.utc)
        activas = Cita.estado != EstadoCita.CANCELADA
        cifras["citas_hoy"] = _contar(db, select(Cita).where(activas, Cita.inicio >= hoy, Cita.inicio < manana))
        cifras["citas_proximas"] = _contar(db, select(Cita).where(activas, Cita.inicio >= datetime.now(timezone.utc)))
    if ve(Permiso.DOCUMENTOS_GESTIONAR):
        cifras["documentos_activos"] = _contar(db, select(Documento).where(Documento.estado == EstadoDocumento.ACTIVO))
    if ve(Permiso.USUARIOS_GESTIONAR):
        cifras["usuarios_activos"] = _contar(db, select(Usuario).where(Usuario.activo))
    return {"institucion": get_settings().institution_name, "zona_horaria": get_settings().timezone, "cifras": cifras}

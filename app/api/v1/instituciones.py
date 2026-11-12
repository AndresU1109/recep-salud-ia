"""Instituciones entre las que se elige al empezar una conversación de voz o de chat. Solo lectura: lo que el asistente puede decir de cada una
es información pública (nombre, ciudad, qué atiende); nada de pacientes ni citas."""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import Especialidad, Institucion, Sede, Usuario
from app.security.dependencias import usuario_actual
from app.security.permisos import Permiso, tiene_permiso
from app.services import institucion_service

router = APIRouter(prefix="/instituciones", tags=["Instituciones"])


def puede_conversar(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
    if not (tiene_permiso(usuario.rol, Permiso.AGENTE_PROBAR) or tiene_permiso(usuario.rol, Permiso.AGENTE_ATENDER)):
        raise ErrorNegocio("sin_permiso", "No tienes permiso para esta acción.", 403)
    return usuario


class InstitucionOut(BaseModel):
    id: int
    nombre: str  # como está guardado, p. ej. «Clínica Santa Aurora (ficticia)»
    nombre_corto: str  # como lo dice el asistente, sin la marca de ficticia
    tipo: str | None
    ciudad: str | None
    descripcion: str | None
    sedes: int
    especialidades: int


def nombre_corto(nombre: str) -> str:
    return re.sub(r"\s*\((?:demo|ficticio|ficticia)\)", "", nombre, flags=re.IGNORECASE).strip()


@router.get("", response_model=list[InstitucionOut], summary="Instituciones con las que se puede conversar")
def listar(_: Usuario = Depends(puede_conversar), db: Session = Depends(get_db)):
    sedes = dict(db.execute(select(Sede.institucion_id, func.count()).where(Sede.activa).group_by(Sede.institucion_id)).all())
    especialidades = dict(db.execute(select(Especialidad.institucion_id, func.count()).where(Especialidad.activa).group_by(Especialidad.institucion_id)).all())
    return [InstitucionOut(id=i.id, nombre=i.nombre, nombre_corto=nombre_corto(i.nombre), tipo=i.tipo, ciudad=i.ciudad, descripcion=i.descripcion,
                           sedes=sedes.get(i.id, 0), especialidades=especialidades.get(i.id, 0)) for i in institucion_service.listar(db)]

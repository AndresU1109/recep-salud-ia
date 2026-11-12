"""Administración del catálogo: sedes, consultorios, especialidades, servicios, requisitos, profesionales.
Leer requiere agenda:ver; crear/editar requiere agenda:gestionar. No hay DELETE: se desactiva (activo=false)."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Consultorio, Especialidad, Profesional, Requisito, Sede, Servicio, Usuario
from app.schemas import catalogo as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import catalogo_service as svc

ver = Depends(requiere(Permiso.AGENDA_VER))
gestionar = Depends(requiere(Permiso.AGENDA_GESTIONAR))

router = APIRouter(tags=["Catálogo"])


def _dump(datos, **kw) -> dict:
    return datos.model_dump(exclude_unset=True, **kw)


# ---- Sedes ----
@router.get("/sedes", response_model=list[s.SedeOut])
def listar_sedes(_: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Sede, Sede.nombre)


@router.post("/sedes", response_model=s.SedeOut, status_code=status.HTTP_201_CREATED)
def crear_sede(datos: s.SedeCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear(db, actor, "sede", Sede, _dump(datos))


@router.patch("/sedes/{id_}", response_model=s.SedeOut)
def actualizar_sede(id_: int, datos: s.SedeActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar(db, actor, "sede", Sede, id_, _dump(datos))


# ---- Consultorios ----
@router.get("/consultorios", response_model=list[s.ConsultorioOut])
def listar_consultorios(sede_id: int | None = Query(None), _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Consultorio, (Consultorio.sede_id, Consultorio.nombre), {Consultorio.sede_id: sede_id})


@router.post("/consultorios", response_model=s.ConsultorioOut, status_code=status.HTTP_201_CREATED)
def crear_consultorio(datos: s.ConsultorioCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear(db, actor, "consultorio", Consultorio, _dump(datos))


@router.patch("/consultorios/{id_}", response_model=s.ConsultorioOut)
def actualizar_consultorio(id_: int, datos: s.ConsultorioActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar(db, actor, "consultorio", Consultorio, id_, _dump(datos))


# ---- Especialidades ----
@router.get("/especialidades", response_model=list[s.EspecialidadOut])
def listar_especialidades(_: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Especialidad, Especialidad.nombre)


@router.post("/especialidades", response_model=s.EspecialidadOut, status_code=status.HTTP_201_CREATED)
def crear_especialidad(datos: s.EspecialidadCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear(db, actor, "especialidad", Especialidad, _dump(datos))


@router.patch("/especialidades/{id_}", response_model=s.EspecialidadOut)
def actualizar_especialidad(id_: int, datos: s.EspecialidadActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar(db, actor, "especialidad", Especialidad, id_, _dump(datos))


# ---- Servicios ----
@router.get("/servicios", response_model=list[s.ServicioOut])
def listar_servicios(especialidad_id: int | None = Query(None), _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Servicio, (Servicio.especialidad_id, Servicio.nombre), {Servicio.especialidad_id: especialidad_id})


@router.post("/servicios", response_model=s.ServicioOut, status_code=status.HTTP_201_CREATED)
def crear_servicio(datos: s.ServicioCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear(db, actor, "servicio", Servicio, _dump(datos))


@router.patch("/servicios/{id_}", response_model=s.ServicioOut)
def actualizar_servicio(id_: int, datos: s.ServicioActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar(db, actor, "servicio", Servicio, id_, _dump(datos))


# ---- Requisitos ----
@router.get("/requisitos", response_model=list[s.RequisitoOut])
def listar_requisitos(especialidad_id: int | None = Query(None), servicio_id: int | None = Query(None),
                      _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Requisito, (Requisito.orden, Requisito.titulo),
                      {Requisito.especialidad_id: especialidad_id, Requisito.servicio_id: servicio_id})


@router.post("/requisitos", response_model=s.RequisitoOut, status_code=status.HTTP_201_CREATED)
def crear_requisito(datos: s.RequisitoCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear(db, actor, "requisito", Requisito, _dump(datos))


@router.patch("/requisitos/{id_}", response_model=s.RequisitoOut)
def actualizar_requisito(id_: int, datos: s.RequisitoActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar(db, actor, "requisito", Requisito, id_, _dump(datos))


# ---- Profesionales ----
@router.get("/profesionales", response_model=list[s.ProfesionalOut])
def listar_profesionales(_: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar(db, Profesional, Profesional.nombre_completo)


@router.post("/profesionales", response_model=s.ProfesionalOut, status_code=status.HTTP_201_CREATED)
def crear_profesional(datos: s.ProfesionalCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear_profesional(db, actor, _dump(datos))


@router.patch("/profesionales/{id_}", response_model=s.ProfesionalOut)
def actualizar_profesional(id_: int, datos: s.ProfesionalActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar_profesional(db, actor, id_, _dump(datos))

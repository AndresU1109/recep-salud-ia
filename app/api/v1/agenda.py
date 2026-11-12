from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.schemas import agenda as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import agenda_service as svc

router = APIRouter(tags=["Agenda médica"])
ver = Depends(requiere(Permiso.AGENDA_VER))
gestionar = Depends(requiere(Permiso.AGENDA_GESTIONAR))


@router.get("/agenda/disponibilidad", response_model=list[s.SlotOut], summary="Cupos libres (máximo 500)")
def disponibilidad(desde: date, hasta: date, especialidad_id: int | None = None, profesional_id: int | None = None,
                   sede_id: int | None = None, servicio_id: int | None = None, _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.disponibilidad(db, desde, hasta, especialidad_id=especialidad_id, profesional_id=profesional_id, sede_id=sede_id, servicio_id=servicio_id)


@router.get("/agenda/dia", response_model=list[s.SlotOut], summary="Agenda de un día con todos los estados")
def agenda_dia(fecha: date, profesional_id: int | None = None, sede_id: int | None = None, especialidad_id: int | None = None,
               _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.agenda_del_dia(db, fecha, profesional_id=profesional_id, sede_id=sede_id, especialidad_id=especialidad_id)


# ---- horarios ----
@router.get("/horarios", response_model=list[s.HorarioOut])
def listar_horarios(profesional_id: int | None = Query(None), _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar_horarios(db, profesional_id)


@router.post("/horarios", response_model=s.HorarioOut, status_code=status.HTTP_201_CREATED)
def crear_horario(datos: s.HorarioCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.crear_horario(db, actor, datos.model_dump())


@router.patch("/horarios/{horario_id}", response_model=s.HorarioOut)
def actualizar_horario(horario_id: int, datos: s.HorarioActualizar, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.actualizar_horario(db, actor, horario_id, datos.model_dump(exclude_unset=True))


# ---- bloqueos de agenda ----
@router.get("/excepciones-agenda", response_model=list[s.ExcepcionOut], summary="Bloqueos vigentes o futuros")
def listar_excepciones(desde: datetime | None = None, profesional_id: int | None = None, _: Usuario = ver, db: Session = Depends(get_db)):
    return svc.listar_excepciones(db, desde, profesional_id)


@router.post("/excepciones-agenda", response_model=s.ExcepcionOut, status_code=status.HTTP_201_CREATED)
def crear_excepcion(datos: s.ExcepcionCrear, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    e, afectadas = svc.crear_excepcion(db, actor, datos.model_dump())
    return s.ExcepcionOut.model_validate(e).model_copy(update={"citas_afectadas": afectadas})


@router.delete("/excepciones-agenda/{excepcion_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_excepcion(excepcion_id: int, actor: Usuario = gestionar, db: Session = Depends(get_db)):
    svc.eliminar_excepcion(db, actor, excepcion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

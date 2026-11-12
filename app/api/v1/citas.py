from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models import Cita, CitaHistorial, Consultorio, Especialidad, ListaEspera, Paciente, Profesional, RegistroInasistencia, Sede, Servicio, Usuario
from app.models.enums import EstadoCita, EstadoListaEspera, TipoPolitica
from app.schemas import cita as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso, tiene_permiso
from app.services import cita_service as svc
from app.services import politica_service

router = APIRouter(tags=["Citas"])
ver = Depends(requiere(Permiso.CITAS_VER))
gestionar = Depends(requiere(Permiso.CITAS_GESTIONAR))
verificar = Depends(requiere(Permiso.INASISTENCIAS_VERIFICAR))
politicas_admin = Depends(requiere(Permiso.POLITICAS_GESTIONAR))


def _salida(db: Session, usuario: Usuario, ids: list[int] | None = None, **filtros) -> list[s.CitaOut]:
    ve_pacientes = tiene_permiso(usuario.rol, Permiso.PACIENTES_VER)
    q = (select(Cita, Paciente.nombres, Paciente.apellidos, Profesional.nombre_completo, Especialidad.nombre, Servicio.nombre, Sede.nombre, Consultorio.nombre)
         .join(Paciente, Paciente.id == Cita.paciente_id).join(Profesional, Profesional.id == Cita.profesional_id)
         .join(Especialidad, Especialidad.id == Cita.especialidad_id).outerjoin(Servicio, Servicio.id == Cita.servicio_id)
         .join(Sede, Sede.id == Cita.sede_id).outerjoin(Consultorio, Consultorio.id == Cita.consultorio_id))
    if ids is not None:
        q = q.where(Cita.id.in_(ids))
    tz = ZoneInfo(get_settings().timezone)
    if filtros.get("desde"):
        q = q.where(Cita.inicio >= datetime.combine(filtros["desde"], time.min, tzinfo=tz))
    if filtros.get("hasta"):
        q = q.where(Cita.inicio < datetime.combine(filtros["hasta"] + timedelta(days=1), time.min, tzinfo=tz))
    for campo in ("profesional_id", "especialidad_id", "sede_id", "paciente_id", "estado"):
        if filtros.get(campo) is not None:
            q = q.where(getattr(Cita, campo) == filtros[campo])
    filas = db.execute(q.order_by(Cita.inicio).limit(filtros.get("limite", 200))).all()
    return [s.CitaOut(id=c.id, codigo_reserva=c.codigo_reserva, paciente_id=c.paciente_id if ve_pacientes else None,
                      paciente=f"{n} {a}" if ve_pacientes else None, profesional_id=c.profesional_id, profesional=prof, especialidad_id=c.especialidad_id,
                      especialidad=esp, servicio=serv, sede_id=c.sede_id, sede=sede, consultorio=cons, inicio=c.inicio, fin=c.fin, estado=c.estado,
                      canal=c.canal, reprogramaciones=c.reprogramaciones, motivo_cancelacion=c.motivo_cancelacion, confirmada_en=c.confirmada_en,
                      cancelada_en=c.cancelada_en) for c, n, a, prof, esp, serv, sede, cons in filas]


def _una(db: Session, usuario: Usuario, cita_id: int) -> s.CitaOut:
    return _salida(db, usuario, [cita_id])[0]


# ---------- citas ----------
@router.get("/citas", response_model=list[s.CitaOut], summary="Buscar citas")
def listar(desde: date | None = None, hasta: date | None = None, profesional_id: int | None = None, especialidad_id: int | None = None,
           sede_id: int | None = None, paciente_id: int | None = None, estado: EstadoCita | None = None, limite: int = Query(200, ge=1, le=500),
           usuario: Usuario = ver, db: Session = Depends(get_db)):
    return _salida(db, usuario, desde=desde, hasta=hasta, profesional_id=profesional_id, especialidad_id=especialidad_id, sede_id=sede_id,
                   paciente_id=paciente_id, estado=estado, limite=limite)


@router.post("/citas", response_model=s.CitaOut, status_code=status.HTTP_201_CREATED, summary="Agendar (reconfirma el cupo)")
def agendar(datos: s.CitaCrear, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    c = svc.agendar(db, paciente_id=datos.paciente_id, profesional_id=datos.profesional_id, especialidad_id=datos.especialidad_id, inicio=datos.inicio,
                    actor=svc.Actor.de_usuario(usuario), canal=datos.canal, servicio_id=datos.servicio_id, sede_id=datos.sede_id, notas=datos.notas)
    return _una(db, usuario, c.id)


@router.get("/citas/codigo/{codigo}", response_model=s.CitaOut)
def por_codigo(codigo: str, usuario: Usuario = ver, db: Session = Depends(get_db)):
    return _una(db, usuario, svc.obtener_por_codigo(db, codigo).id)


@router.get("/citas/{cita_id}", response_model=s.CitaOut)
def ver_cita(cita_id: int, usuario: Usuario = ver, db: Session = Depends(get_db)):
    return _una(db, usuario, svc.obtener(db, cita_id).id)


@router.get("/citas/{cita_id}/historial", response_model=list[s.HistorialOut], summary="Historial de cambios")
def historial(cita_id: int, _: Usuario = ver, db: Session = Depends(get_db)):
    svc.obtener(db, cita_id)
    return list(db.scalars(select(CitaHistorial).where(CitaHistorial.cita_id == cita_id).order_by(CitaHistorial.ocurrio_en, CitaHistorial.id)))


@router.post("/citas/{cita_id}/confirmar", response_model=s.CitaOut)
def confirmar(cita_id: int, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return _una(db, usuario, svc.confirmar(db, cita_id, svc.Actor.de_usuario(usuario)).id)


@router.post("/citas/{cita_id}/cancelar", response_model=s.CitaOut)
def cancelar(cita_id: int, datos: s.CancelarIn, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    cita, candidatos = svc.cancelar(db, cita_id, datos.motivo, svc.Actor.de_usuario(usuario), forzar=datos.forzar)
    return _una(db, usuario, cita.id).model_copy(update={"lista_espera_candidatos": candidatos})


@router.post("/citas/{cita_id}/reprogramar", response_model=s.CitaOut)
def reprogramar(cita_id: int, datos: s.ReprogramarIn, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    cita = svc.reprogramar(db, cita_id, datos.inicio, svc.Actor.de_usuario(usuario), profesional_id=datos.profesional_id, forzar=datos.forzar)
    return _una(db, usuario, cita.id)


@router.post("/citas/{cita_id}/atendida", response_model=s.CitaOut, summary="Marcar como atendida")
def atendida(cita_id: int, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return _una(db, usuario, svc.marcar_atendida(db, cita_id, svc.Actor.de_usuario(usuario)).id)


@router.post("/citas/{cita_id}/inasistencia", response_model=s.CitaOut, summary="Registrar inasistencia (queda pendiente de verificación)")
def inasistencia(cita_id: int, datos: s.InasistenciaIn, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    svc.registrar_inasistencia(db, cita_id, datos.observacion, svc.Actor.de_usuario(usuario))
    return _una(db, usuario, cita_id)


# ---------- inasistencias ----------
@router.get("/inasistencias", response_model=list[s.InasistenciaOut])
def listar_inasistencias(pendientes: bool | None = None, paciente_id: int | None = None, _: Usuario = verificar, db: Session = Depends(get_db)):
    q = (select(RegistroInasistencia, Cita.inicio, Paciente.nombres, Paciente.apellidos).join(Cita, Cita.id == RegistroInasistencia.cita_id)
         .join(Paciente, Paciente.id == RegistroInasistencia.paciente_id).order_by(RegistroInasistencia.id.desc()).limit(300))
    if pendientes is not None:
        q = q.where(RegistroInasistencia.verificada_en.is_(None) if pendientes else RegistroInasistencia.verificada_en.is_not(None))
    if paciente_id:
        q = q.where(RegistroInasistencia.paciente_id == paciente_id)
    return [s.InasistenciaOut(id=r.id, cita_id=r.cita_id, paciente_id=r.paciente_id, paciente=f"{n} {a}", fecha=ini, verificada=r.verificada,
                              pendiente=r.verificada_en is None, observacion=r.observacion, consecuencia_aplicada=r.consecuencia_aplicada)
            for r, ini, n, a in db.execute(q).all()]


@router.post("/inasistencias/{registro_id}/verificar", response_model=s.InasistenciaOut)
def verificar_inasistencia(registro_id: int, datos: s.VerificarInasistenciaIn, usuario: Usuario = verificar, db: Session = Depends(get_db)):
    svc.verificar_inasistencia(db, registro_id, datos.confirmada, datos.observacion, datos.aplicar_consecuencia, svc.Actor.de_usuario(usuario))
    return next(i for i in listar_inasistencias(None, None, usuario, db) if i.id == registro_id)


# ---------- lista de espera ----------
@router.get("/lista-espera", response_model=list[s.ListaEsperaOut])
def listar_espera(estado: EstadoListaEspera | None = EstadoListaEspera.ACTIVA, _: Usuario = gestionar, db: Session = Depends(get_db)):
    q = select(ListaEspera).order_by(ListaEspera.created_at, ListaEspera.id).limit(300)
    return list(db.scalars(q.where(ListaEspera.estado == estado) if estado else q))


@router.post("/lista-espera", response_model=s.ListaEsperaOut, status_code=status.HTTP_201_CREATED)
def agregar_espera(datos: s.ListaEsperaCrear, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.agregar_lista_espera(db, svc.Actor.de_usuario(usuario), **datos.model_dump())


@router.post("/lista-espera/{item_id}/cancelar", response_model=s.ListaEsperaOut)
def cancelar_espera(item_id: int, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.cancelar_lista_espera(db, item_id, svc.Actor.de_usuario(usuario))


@router.post("/lista-espera/{item_id}/asignar", response_model=s.CitaOut, status_code=status.HTTP_201_CREATED, summary="Agendar a quien esperaba")
def asignar_espera(item_id: int, datos: s.AsignarListaEsperaIn, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    cita = svc.asignar_desde_lista_espera(db, item_id, datos.profesional_id, datos.inicio, svc.Actor.de_usuario(usuario))
    return _una(db, usuario, cita.id)


# ---------- políticas ----------
@router.get("/politicas", response_model=list[s.PoliticaOut])
def listar_politicas(tipo: TipoPolitica | None = None, _: Usuario = ver, db: Session = Depends(get_db)):
    return politica_service.listar(db, tipo)


@router.get("/politicas/aplicable", response_model=s.PoliticaOut | None, summary="La política vigente más específica (o null si no hay)")
def politica_aplicable(tipo: TipoPolitica, sede_id: int | None = None, especialidad_id: int | None = None, _: Usuario = ver, db: Session = Depends(get_db)):
    return politica_service.aplicable(db, tipo, sede_id=sede_id, especialidad_id=especialidad_id)


@router.post("/politicas", response_model=s.PoliticaOut, status_code=status.HTTP_201_CREATED)
def crear_politica(datos: s.PoliticaCrear, usuario: Usuario = politicas_admin, db: Session = Depends(get_db)):
    cuerpo = datos.model_dump()
    if cuerpo["vigente_desde"] is None:
        cuerpo.pop("vigente_desde")
    return politica_service.crear(db, usuario, cuerpo)


@router.patch("/politicas/{politica_id}", response_model=s.PoliticaOut)
def actualizar_politica(politica_id: int, datos: s.PoliticaActualizar, usuario: Usuario = politicas_admin, db: Session = Depends(get_db)):
    return politica_service.actualizar(db, usuario, politica_id, datos.model_dump(exclude_unset=True))

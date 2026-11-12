"""Agenda médica: horarios semanales, bloqueos y motor de disponibilidad.

Un cupo está LIBRE solo si: cae dentro de un horario vigente y activo, no es pasado, no choca con un bloqueo,
ni con una cita no cancelada del profesional ni del consultorio, y la jornada no alcanzó su máximo de citas.
Todo en hora local de la institución (TIMEZONE). La BD sigue siendo la última barrera contra la doble reserva.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import Cita, Consultorio, Especialidad, ExcepcionAgenda, HorarioProfesional, Profesional, Sede, Servicio, Usuario
from app.models.enums import EstadoCita
from app.schemas.agenda import EstadoSlot, SlotOut
from app.services import auditoria

MAX_DIAS_CONSULTA = 62
MAX_SLOTS = 500


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _local(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=_tz())


def _se_solapan(a_ini: datetime, a_fin: datetime, b_ini: datetime, b_fin: datetime) -> bool:
    return a_ini < b_fin and b_ini < a_fin


# ======================= disponibilidad =======================
def generar_slots(db: Session, desde: date, hasta: date, *, profesional_id: int | None = None, especialidad_id: int | None = None,
                  sede_id: int | None = None, servicio_id: int | None = None, ahora: datetime | None = None,
                  ignorar_cita_id: int | None = None) -> list[SlotOut]:
    """Todos los cupos del rango con su estado (LIBRE, OCUPADO, BLOQUEADO, SIN_CUPO, PASADO)."""
    if hasta < desde:
        raise ErrorNegocio("rango_invalido", "La fecha final es anterior a la inicial.", 422)
    if (hasta - desde).days + 1 > MAX_DIAS_CONSULTA:
        raise ErrorNegocio("rango_muy_largo", f"Consulta como máximo {MAX_DIAS_CONSULTA} días a la vez.", 422)
    ahora = ahora or datetime.now(timezone.utc)

    duracion_servicio = None
    if servicio_id is not None:
        serv = db.get(Servicio, servicio_id)
        if serv is None or not serv.activo or (especialidad_id and serv.especialidad_id != especialidad_id):
            raise ErrorNegocio("servicio_invalido", "El servicio no existe, está inactivo o no pertenece a esa especialidad.", 422)
        duracion_servicio, especialidad_id = serv.duracion_minutos, serv.especialidad_id

    consulta = (
        select(HorarioProfesional, Profesional.nombre_completo, Sede.nombre, Consultorio.nombre)
        .join(Profesional, Profesional.id == HorarioProfesional.profesional_id).join(Sede, Sede.id == HorarioProfesional.sede_id)
        .join(Especialidad, Especialidad.id == HorarioProfesional.especialidad_id)
        .outerjoin(Consultorio, Consultorio.id == HorarioProfesional.consultorio_id)
        .where(HorarioProfesional.activo, Profesional.activo, Sede.activa, Especialidad.activa,
               or_(HorarioProfesional.consultorio_id.is_(None), Consultorio.activo),
               HorarioProfesional.vigente_desde <= hasta,
               or_(HorarioProfesional.vigente_hasta.is_(None), HorarioProfesional.vigente_hasta >= desde))
    )
    if profesional_id:
        consulta = consulta.where(HorarioProfesional.profesional_id == profesional_id)
    if especialidad_id:
        consulta = consulta.where(HorarioProfesional.especialidad_id == especialidad_id)
    if sede_id:
        consulta = consulta.where(HorarioProfesional.sede_id == sede_id)
    horarios = db.execute(consulta).all()
    if not horarios:
        return []

    ini_rango, fin_rango = _local(desde, time.min), _local(hasta + timedelta(days=1), time.min)
    pids = {h[0].profesional_id for h in horarios}
    cids = {h[0].consultorio_id for h in horarios if h[0].consultorio_id}
    filtro_cita = Cita.profesional_id.in_(pids) if not cids else or_(Cita.profesional_id.in_(pids), Cita.consultorio_id.in_(cids))
    consulta_citas = select(Cita.profesional_id, Cita.consultorio_id, Cita.inicio, Cita.fin, Cita.codigo_reserva).where(
        Cita.estado != EstadoCita.CANCELADA, Cita.inicio < fin_rango, Cita.fin > ini_rango, filtro_cita)
    if ignorar_cita_id:  # al reprogramar, la cita no debe bloquearse a sí misma
        consulta_citas = consulta_citas.where(Cita.id != ignorar_cita_id)
    citas = db.execute(consulta_citas).all()
    bloqueos = list(db.scalars(select(ExcepcionAgenda).where(ExcepcionAgenda.inicio < fin_rango, ExcepcionAgenda.fin > ini_rango)))

    slots: list[SlotOut] = []
    for dia in (desde + timedelta(days=i) for i in range((hasta - desde).days + 1)):
        for h, prof_nombre, sede_nombre, cons_nombre in horarios:
            if h.dia_semana != dia.weekday() or h.vigente_desde > dia or (h.vigente_hasta and h.vigente_hasta < dia):
                continue
            jor_ini, jor_fin = _local(dia, h.hora_inicio), _local(dia, h.hora_fin)
            paso = timedelta(minutes=h.duracion_cita_min)
            dur = timedelta(minutes=duracion_servicio or h.duracion_cita_min)
            ocupadas_jornada = sum(1 for c in citas if c.profesional_id == h.profesional_id and jor_ini <= c.inicio < jor_fin)
            cupos_restantes = None if h.cupos_maximos is None else max(h.cupos_maximos - ocupadas_jornada, 0)
            libres_dados = 0
            ini = jor_ini
            while ini + dur <= jor_fin:
                fin = ini + dur
                estado, detalle = EstadoSlot.LIBRE, None
                choque = next((c for c in citas if _se_solapan(ini, fin, c.inicio, c.fin)
                               and (c.profesional_id == h.profesional_id or (h.consultorio_id and c.consultorio_id == h.consultorio_id))), None)
                bloqueo = next((b for b in bloqueos if _se_solapan(ini, fin, b.inicio, b.fin)
                                and (b.profesional_id in (None, h.profesional_id)) and (b.sede_id in (None, h.sede_id))), None)
                if ini <= ahora:
                    estado = EstadoSlot.PASADO
                elif bloqueo:
                    estado, detalle = EstadoSlot.BLOQUEADO, bloqueo.motivo
                elif choque:
                    estado, detalle = EstadoSlot.OCUPADO, choque.codigo_reserva
                elif cupos_restantes is not None and libres_dados >= cupos_restantes:
                    estado = EstadoSlot.SIN_CUPO
                if estado == EstadoSlot.LIBRE:
                    libres_dados += 1
                slots.append(SlotOut(inicio=ini, fin=fin, estado=estado, detalle=detalle, horario_id=h.id, profesional_id=h.profesional_id,
                                     profesional=prof_nombre, especialidad_id=h.especialidad_id, sede_id=h.sede_id, sede=sede_nombre,
                                     consultorio_id=h.consultorio_id, consultorio=cons_nombre))
                ini += paso
    slots.sort(key=lambda s: (s.inicio, s.profesional))
    return slots


def disponibilidad(db: Session, desde: date, hasta: date, **filtros) -> list[SlotOut]:
    return [s for s in generar_slots(db, desde, hasta, **filtros) if s.estado == EstadoSlot.LIBRE][:MAX_SLOTS]


def agenda_del_dia(db: Session, dia: date, **filtros) -> list[SlotOut]:
    return generar_slots(db, dia, dia, **filtros)


def buscar_slot_libre(db: Session, profesional_id: int, inicio: datetime, *, especialidad_id: int | None = None,
                      servicio_id: int | None = None, sede_id: int | None = None, ignorar_cita_id: int | None = None) -> SlotOut | None:
    """Para reconfirmar al reservar: devuelve el cupo exacto si sigue libre, o None."""
    dia = inicio.astimezone(_tz()).date()
    for s in generar_slots(db, dia, dia, profesional_id=profesional_id, especialidad_id=especialidad_id, servicio_id=servicio_id,
                           sede_id=sede_id, ignorar_cita_id=ignorar_cita_id):
        if s.estado == EstadoSlot.LIBRE and s.inicio == inicio:
            return s
    return None


# ======================= horarios =======================
def _citas_activas_entre(db: Session, ini: datetime, fin: datetime, profesional_id: int | None = None, sede_id: int | None = None) -> list:
    q = select(Cita).where(Cita.estado != EstadoCita.CANCELADA, Cita.inicio < fin, Cita.fin > ini)
    if profesional_id:
        q = q.where(Cita.profesional_id == profesional_id)
    if sede_id:
        q = q.where(Cita.sede_id == sede_id)
    return list(db.scalars(q))


def _validar_horario(db: Session, h: HorarioProfesional) -> None:
    prof = db.get(Profesional, h.profesional_id)
    if prof is None or db.get(Especialidad, h.especialidad_id) is None or db.get(Sede, h.sede_id) is None:
        raise ErrorNegocio("referencia_invalida", "El profesional, la especialidad o la sede no existen.", 422)
    if h.especialidad_id not in {e.id for e in prof.especialidades}:
        raise ErrorNegocio("especialidad_no_asignada", "El profesional no tiene asignada esa especialidad.", 422)
    if h.consultorio_id is not None:
        cons = db.get(Consultorio, h.consultorio_id)
        if cons is None or cons.sede_id != h.sede_id:
            raise ErrorNegocio("consultorio_invalido", "El consultorio no existe o no pertenece a la sede.", 422)
    if h.hora_fin <= h.hora_inicio:
        raise ErrorNegocio("horas_invalidas", "La hora de fin debe ser posterior a la de inicio.", 422)
    if not h.activo:
        return
    otros = db.scalars(select(HorarioProfesional).where(
        HorarioProfesional.profesional_id == h.profesional_id, HorarioProfesional.dia_semana == h.dia_semana, HorarioProfesional.activo,
        HorarioProfesional.id != (h.id or 0), HorarioProfesional.hora_inicio < h.hora_fin, HorarioProfesional.hora_fin > h.hora_inicio,
        HorarioProfesional.vigente_desde <= (h.vigente_hasta or date.max),
        or_(HorarioProfesional.vigente_hasta.is_(None), HorarioProfesional.vigente_hasta >= h.vigente_desde))).first()
    if otros:
        raise ErrorNegocio("horario_solapado", "El profesional ya tiene un horario activo que se cruza con ese día y franja.", 409)


def _advertencias_horario(db: Session, h: HorarioProfesional) -> list[str]:
    """Citas futuras que quedarían fuera del horario. No se cancelan ni se mueven solas."""
    ahora = datetime.now(timezone.utc)
    futuras = _citas_activas_entre(db, ahora, datetime(9999, 12, 31, tzinfo=timezone.utc), profesional_id=h.profesional_id)  # sin tope: todas las futuras
    fuera = 0
    for c in futuras:
        loc = c.inicio.astimezone(_tz())
        if loc.weekday() != h.dia_semana:
            continue
        dentro = h.activo and h.hora_inicio <= loc.time() and c.fin.astimezone(_tz()).time() <= h.hora_fin \
            and h.vigente_desde <= loc.date() and (h.vigente_hasta is None or loc.date() <= h.vigente_hasta)
        fuera += 0 if dentro else 1
    return [f"{fuera} cita(s) futura(s) de ese día de la semana quedan fuera del horario; reprográmalas o cancélalas desde Citas."] if fuera else []


def listar_horarios(db: Session, profesional_id: int | None = None) -> list[HorarioProfesional]:
    q = select(HorarioProfesional).order_by(HorarioProfesional.profesional_id, HorarioProfesional.dia_semana, HorarioProfesional.hora_inicio)
    if profesional_id:
        q = q.where(HorarioProfesional.profesional_id == profesional_id)
    return list(db.scalars(q))


def _guardar(db: Session, actor: Usuario, accion: str, obj, detalle: dict | None = None) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ErrorNegocio("datos_fuera_de_regla", "Los datos no cumplen las reglas del sistema.", 422) from exc
    auditoria.registrar(db, accion=accion, usuario_id=actor.id, entidad=accion.split("_")[0], entidad_id=obj.id, detalle=detalle)
    db.commit()


def crear_horario(db: Session, actor: Usuario, datos: dict) -> HorarioProfesional:
    datos = dict(datos)
    if not datos.get("vigente_desde"):
        datos["vigente_desde"] = datetime.now(_tz()).date()
    h = HorarioProfesional(**datos)
    _validar_horario(db, h)
    db.add(h)
    _guardar(db, actor, "horario_creado", h)
    h.advertencias = []  # type: ignore[attr-defined]
    return h


def actualizar_horario(db: Session, actor: Usuario, horario_id: int, datos: dict) -> HorarioProfesional:
    h = db.get(HorarioProfesional, horario_id)
    if h is None:
        raise ErrorNegocio("no_encontrado", "El horario no existe.", 404)
    for campo, valor in datos.items():
        setattr(h, campo, valor)
    if h.vigente_hasta and h.vigente_hasta < h.vigente_desde:
        raise ErrorNegocio("vigencia_invalida", "La vigencia termina antes de empezar.", 422)
    try:
        _validar_horario(db, h)
    except ErrorNegocio:
        db.rollback()
        raise
    _guardar(db, actor, "horario_actualizado", h, {"campos": sorted(datos)})
    h.advertencias = _advertencias_horario(db, h)  # type: ignore[attr-defined]
    return h


# ======================= bloqueos =======================
def listar_excepciones(db: Session, desde: datetime | None = None, profesional_id: int | None = None) -> list[ExcepcionAgenda]:
    q = select(ExcepcionAgenda).order_by(ExcepcionAgenda.inicio)
    if desde:
        q = q.where(ExcepcionAgenda.fin > desde)
    if profesional_id:
        q = q.where(ExcepcionAgenda.profesional_id == profesional_id)
    return list(db.scalars(q))


def _afectadas(db: Session, e: ExcepcionAgenda) -> int:
    return len(_citas_activas_entre(db, e.inicio, e.fin, e.profesional_id, e.sede_id))


def crear_excepcion(db: Session, actor: Usuario, datos: dict) -> tuple[ExcepcionAgenda, int]:
    if datos.get("profesional_id") and db.get(Profesional, datos["profesional_id"]) is None:
        raise ErrorNegocio("referencia_invalida", "El profesional no existe.", 422)
    if datos.get("sede_id") and db.get(Sede, datos["sede_id"]) is None:
        raise ErrorNegocio("referencia_invalida", "La sede no existe.", 422)
    e = ExcepcionAgenda(**datos)
    db.add(e)
    _guardar(db, actor, "excepcion_creada", e)
    return e, _afectadas(db, e)


def eliminar_excepcion(db: Session, actor: Usuario, excepcion_id: int) -> None:
    e = db.get(ExcepcionAgenda, excepcion_id)
    if e is None:
        raise ErrorNegocio("no_encontrado", "El bloqueo no existe.", 404)
    auditoria.registrar(db, accion="excepcion_eliminada", usuario_id=actor.id, entidad="excepcion", entidad_id=e.id)
    db.delete(e)
    db.commit()

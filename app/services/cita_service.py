"""Ciclo de vida de las citas: agendar (con reconfirmación), confirmar, reprogramar, cancelar, atender e inasistencias.

Principios: (1) el cupo se reconfirma al reservar y PostgreSQL (EXCLUDE) es la última barrera; (2) solo se aplican
políticas que la institución configuró; (3) nada se sanciona solo; (4) toda acción deja historial y auditoría.
"""
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Cita, CitaHistorial, ListaEspera, Paciente, RegistroInasistencia, Usuario
from app.models.enums import AccionCita, ActorTipo, CanalCita, EstadoCita, EstadoListaEspera, TipoNotificacion, TipoPolitica
from app.repositories import paciente_repository
from app.security.permisos import Permiso, tiene_permiso
from app.services import agenda_service, auditoria, notificacion_service, politica_service

ACTIVAS = (EstadoCita.PROGRAMADA, EstadoCita.CONFIRMADA)
_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sin 0/O/1/I para poder dictarlo por teléfono


@dataclass(frozen=True)
class Actor:
    tipo: ActorTipo
    usuario_id: int | None = None
    puede_forzar: bool = False  # saltarse una política configurada (solo personal autorizado)

    @staticmethod
    def de_usuario(u: Usuario) -> "Actor":
        return Actor(ActorTipo.USUARIO, u.id, tiene_permiso(u.rol, Permiso.INASISTENCIAS_VERIFICAR))


AGENTE = Actor(ActorTipo.AGENTE)
PACIENTE = Actor(ActorTipo.PACIENTE)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _codigo() -> str:
    return "".join(secrets.choice(_ALFABETO) for _ in range(8))


def _historial(db: Session, cita: Cita, accion: AccionCita, anterior: EstadoCita | None, actor: Actor, detalle: dict | None = None,
               inicio_anterior: datetime | None = None, fin_anterior: datetime | None = None) -> None:
    db.add(CitaHistorial(cita_id=cita.id, accion=accion, estado_anterior=anterior, estado_nuevo=cita.estado, actor_tipo=actor.tipo,
                         usuario_id=actor.usuario_id, detalle=detalle, inicio_anterior=inicio_anterior, fin_anterior=fin_anterior))
    auditoria.registrar(db, accion=f"cita_{accion.value.lower()}", actor=actor.tipo.value.lower(), usuario_id=actor.usuario_id,
                        entidad="cita", entidad_id=cita.id, detalle=detalle)


def _es_conflicto_de_concurrencia(exc: Exception) -> bool:
    """Dos reservas simultáneas del mismo cupo pueden resolverse en PostgreSQL como violación de la restricción EXCLUDE
    o como deadlock (40P01) / fallo de serialización (40001): para el usuario es lo mismo, el cupo ya se ocupó."""
    return isinstance(exc, OperationalError) and getattr(exc.orig, "sqlstate", None) in ("40P01", "40001")


def _traducir_choque(exc: DBAPIError) -> ErrorNegocio:
    if _es_conflicto_de_concurrencia(exc):
        return ErrorNegocio("cupo_no_disponible", "Ese horario acaba de ser ocupado. Elige otro cupo.", 409)
    n = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
    if "paciente_sin_solape" in n:
        return ErrorNegocio("paciente_con_cita_en_ese_horario", "El paciente ya tiene otra cita en ese horario.", 409)
    if "sin_solape" in n:
        return ErrorNegocio("cupo_no_disponible", "Ese horario acaba de ser ocupado. Elige otro cupo.", 409)
    return ErrorNegocio("datos_fuera_de_regla", "Los datos de la cita no cumplen las reglas del sistema.", 422)


def obtener(db: Session, cita_id: int, solo_paciente_id: int | None = None) -> Cita:
    """solo_paciente_id: si se indica, una cita ajena responde 'no existe' (no revela que existe)."""
    cita = db.get(Cita, cita_id)
    if cita is None or (solo_paciente_id is not None and cita.paciente_id != solo_paciente_id):
        raise ErrorNegocio("cita_no_encontrada", "No se encontró la cita.", 404)
    return cita


def obtener_por_codigo(db: Session, codigo: str, solo_paciente_id: int | None = None) -> Cita:
    cita = db.scalar(select(Cita).where(Cita.codigo_reserva == codigo.strip().upper()))
    if cita is None or (solo_paciente_id is not None and cita.paciente_id != solo_paciente_id):
        raise ErrorNegocio("cita_no_encontrada", "No se encontró la cita.", 404)
    return cita


def _exigir_activa_futura(cita: Cita, verbo: str) -> None:
    if cita.estado not in ACTIVAS:
        raise ErrorNegocio("estado_no_permite", f"No se puede {verbo} una cita en estado {cita.estado.value}.", 409)
    if cita.inicio <= _ahora():
        raise ErrorNegocio("cita_pasada", f"La cita ya comenzó o pasó; no se puede {verbo}.", 409)


def _exigir_politica_horas(db: Session, cita: Cita, tipo: TipoPolitica, clave: str, verbo: str, actor: Actor) -> dict | None:
    """Aplica 'horas mínimas de anticipación' solo si la institución configuró la política. Devuelve detalle si se forzó."""
    pol = politica_service.aplicable(db, tipo, sede_id=cita.sede_id, especialidad_id=cita.especialidad_id)
    horas = (pol.parametros or {}).get(clave) if pol else None
    if horas is None:
        return None
    faltan = (cita.inicio - _ahora()).total_seconds() / 3600
    if faltan >= horas:
        return None
    if actor.puede_forzar:
        return {"politica_id": pol.id, "forzado": True, "horas_minimas": horas}
    raise ErrorNegocio("politica_incumplida", pol.mensaje_paciente or f"Para {verbo} la cita se requiere al menos {horas} hora(s) de anticipación.", 409)


# ---------- agendar ----------
def agendar(db: Session, *, paciente_id: int, profesional_id: int, especialidad_id: int, inicio: datetime, actor: Actor,
            canal: CanalCita, servicio_id: int | None = None, sede_id: int | None = None, notas: str | None = None,
            conversacion_id=None) -> Cita:
    paciente = db.get(Paciente, paciente_id)
    if paciente is None or not paciente.activo:
        raise ErrorNegocio("paciente_no_encontrado", "El paciente no existe o está inactivo.", 404)
    consentimiento = paciente_repository.consentimiento_vigente(db, paciente_id)
    if consentimiento is None or not consentimiento.aceptado:
        raise ErrorNegocio("sin_consentimiento", "El paciente no tiene vigente su autorización de tratamiento de datos.", 422)

    # Reconfirmación: el cupo se vuelve a calcular en este instante, no se confía en una consulta anterior.
    slot = agenda_service.buscar_slot_libre(db, profesional_id, inicio, especialidad_id=especialidad_id, servicio_id=servicio_id, sede_id=sede_id)
    if slot is None:
        raise ErrorNegocio("cupo_no_disponible", "Ese horario no está disponible. Consulta la disponibilidad de nuevo.", 409)

    for _ in range(5):  # reintento solo por colisión (improbable) del código de reserva
        cita = Cita(codigo_reserva=_codigo(), paciente_id=paciente_id, profesional_id=profesional_id, especialidad_id=slot.especialidad_id,
                    servicio_id=servicio_id, sede_id=slot.sede_id, consultorio_id=slot.consultorio_id, inicio=slot.inicio, fin=slot.fin,
                    estado=EstadoCita.PROGRAMADA, canal=canal, notas_administrativas=notas, conversacion_id=conversacion_id,
                    creada_por_usuario_id=actor.usuario_id)
        try:
            with db.begin_nested():
                db.add(cita)
                db.flush()
            break
        except (IntegrityError, OperationalError) as exc:
            n = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
            if "codigo_reserva" in n:
                continue
            db.rollback()
            raise _traducir_choque(exc) from exc
    else:
        raise ErrorNegocio("error_interno", "No se pudo generar el código de reserva.", 500)
    _historial(db, cita, AccionCita.CREADA, None, actor, {"canal": canal.value})
    notificacion_service.avisar_cita(db, cita, TipoNotificacion.CITA_AGENDADA)
    db.commit()
    return cita


# ---------- confirmar ----------
def confirmar(db: Session, cita_id: int, actor: Actor, solo_paciente_id: int | None = None) -> Cita:
    cita = obtener(db, cita_id, solo_paciente_id)
    _exigir_activa_futura(cita, "confirmar")
    if cita.estado == EstadoCita.CONFIRMADA:
        return cita
    anterior, cita.estado, cita.confirmada_en = cita.estado, EstadoCita.CONFIRMADA, _ahora()
    _historial(db, cita, AccionCita.CONFIRMADA, anterior, actor)
    db.commit()
    return cita


# ---------- cancelar ----------
def cancelar(db: Session, cita_id: int, motivo: str | None, actor: Actor, *, forzar: bool = False, solo_paciente_id: int | None = None) -> tuple[Cita, int]:
    """Devuelve (cita, candidatos en lista de espera para ese cupo)."""
    cita = obtener(db, cita_id, solo_paciente_id)
    _exigir_activa_futura(cita, "cancelar")
    detalle = _exigir_politica_horas(db, cita, TipoPolitica.CANCELACION, "horas_minimas_cancelar", "cancelar",
                                     actor if forzar else Actor(actor.tipo, actor.usuario_id, False))
    anterior, cita.estado, cita.cancelada_en, cita.motivo_cancelacion = cita.estado, EstadoCita.CANCELADA, _ahora(), (motivo or "").strip() or None
    _historial(db, cita, AccionCita.CANCELADA, anterior, actor, {**(detalle or {}), **({"motivo": cita.motivo_cancelacion} if cita.motivo_cancelacion else {})} or None)
    notificacion_service.avisar_cita(db, cita, TipoNotificacion.CITA_CANCELADA)
    db.commit()
    candidatos = candidatos_lista_espera(db, cita)
    if candidatos and notificacion_service.avisar_cupo_liberado(db, cita, candidatos):
        db.commit()  # quienes recibieron el aviso pasan a NOTIFICADA
    return cita, len(candidatos)


# ---------- reprogramar ----------
def reprogramar(db: Session, cita_id: int, nuevo_inicio: datetime, actor: Actor, *, profesional_id: int | None = None, forzar: bool = False,
                solo_paciente_id: int | None = None) -> Cita:
    cita = obtener(db, cita_id, solo_paciente_id)
    _exigir_activa_futura(cita, "reprogramar")
    efectivo = actor if forzar else Actor(actor.tipo, actor.usuario_id, False)
    detalle = _exigir_politica_horas(db, cita, TipoPolitica.REPROGRAMACION, "horas_minimas_reprogramar", "reprogramar", efectivo) or {}
    pol = politica_service.aplicable(db, TipoPolitica.REPROGRAMACION, sede_id=cita.sede_id, especialidad_id=cita.especialidad_id)
    maximo = (pol.parametros or {}).get("maximo_reprogramaciones") if pol else None
    if maximo is not None and cita.reprogramaciones >= maximo:
        if not efectivo.puede_forzar:
            raise ErrorNegocio("politica_incumplida", pol.mensaje_paciente or f"Esta cita ya alcanzó el máximo de {maximo} reprogramación(es).", 409)
        detalle = {**detalle, "politica_id": pol.id, "forzado": True, "maximo_reprogramaciones": maximo}

    prof = profesional_id or cita.profesional_id
    slot = agenda_service.buscar_slot_libre(db, prof, nuevo_inicio, especialidad_id=cita.especialidad_id, servicio_id=cita.servicio_id,
                                            ignorar_cita_id=cita.id)
    if slot is None:
        raise ErrorNegocio("cupo_no_disponible", "Ese horario no está disponible. Consulta la disponibilidad de nuevo.", 409)

    anterior, ini_ant, fin_ant = cita.estado, cita.inicio, cita.fin
    cita.profesional_id, cita.sede_id, cita.consultorio_id, cita.inicio, cita.fin = prof, slot.sede_id, slot.consultorio_id, slot.inicio, slot.fin
    cita.reprogramaciones += 1
    cita.estado, cita.confirmada_en = EstadoCita.PROGRAMADA, None  # hay que volver a confirmar
    try:
        with db.begin_nested():
            db.flush()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise _traducir_choque(exc) from exc
    _historial(db, cita, AccionCita.REPROGRAMADA, anterior, actor, {**detalle, "n": cita.reprogramaciones}, ini_ant, fin_ant)
    notificacion_service.avisar_cita(db, cita, TipoNotificacion.CITA_REPROGRAMADA)
    db.commit()
    return cita


# ---------- atención e inasistencias ----------
def marcar_atendida(db: Session, cita_id: int, actor: Actor) -> Cita:
    cita = obtener(db, cita_id)
    if cita.estado not in ACTIVAS or cita.inicio > _ahora():
        raise ErrorNegocio("estado_no_permite", "Solo se marca como atendida una cita activa cuya hora ya llegó.", 409)
    anterior, cita.estado = cita.estado, EstadoCita.ATENDIDA
    _historial(db, cita, AccionCita.ATENDIDA, anterior, actor)
    db.commit()
    return cita


def registrar_inasistencia(db: Session, cita_id: int, observacion: str | None, actor: Actor) -> RegistroInasistencia:
    """Queda PENDIENTE de verificación: mientras nadie autorizado la verifique, no cuenta para nada."""
    cita = obtener(db, cita_id)
    if cita.estado not in ACTIVAS or cita.inicio > _ahora():
        raise ErrorNegocio("estado_no_permite", "Solo se registra inasistencia en una cita activa cuya hora ya pasó.", 409)
    anterior, cita.estado = cita.estado, EstadoCita.INASISTENCIA
    reg = RegistroInasistencia(cita_id=cita.id, paciente_id=cita.paciente_id, observacion=(observacion or "").strip() or None)
    db.add(reg)
    db.flush()
    _historial(db, cita, AccionCita.INASISTENCIA, anterior, actor)
    db.commit()
    return reg


def verificar_inasistencia(db: Session, registro_id: int, confirmada: bool, observacion: str | None, aplicar_consecuencia: bool, actor: Actor) -> RegistroInasistencia:
    reg = db.get(RegistroInasistencia, registro_id)
    if reg is None:
        raise ErrorNegocio("no_encontrado", "El registro de inasistencia no existe.", 404)
    if reg.verificada_en is not None:
        raise ErrorNegocio("ya_verificada", "Esta inasistencia ya fue revisada.", 409)
    cita = db.get(Cita, reg.cita_id)
    consecuencia = None
    if confirmada and aplicar_consecuencia:  # se valida antes de tocar nada
        pol = politica_service.aplicable(db, TipoPolitica.INASISTENCIA, sede_id=cita.sede_id, especialidad_id=cita.especialidad_id)
        if pol is None or not pol.consecuencia_autorizada:
            raise ErrorNegocio("sin_consecuencia_autorizada", "No hay una consecuencia autorizada en las políticas; no se puede aplicar ninguna.", 422)
        consecuencia = pol.consecuencia_autorizada[:300]
    reg.verificada_por, reg.verificada_en = actor.usuario_id, _ahora()
    if observacion:
        reg.observacion = observacion.strip()
    if confirmada:
        reg.verificada = True
        reg.consecuencia_aplicada = consecuencia
    else:  # se descarta: el paciente sí fue atendido (registro equivocado)
        reg.verificada = False
        anterior, cita.estado = cita.estado, EstadoCita.ATENDIDA
        _historial(db, cita, AccionCita.ATENDIDA, anterior, actor, {"origen": "inasistencia_descartada"})
    auditoria.registrar(db, accion="inasistencia_verificada" if confirmada else "inasistencia_descartada", usuario_id=actor.usuario_id,
                        entidad="inasistencia", entidad_id=reg.id)
    db.commit()
    return reg


def contar_inasistencias_verificadas(db: Session, paciente_id: int) -> int:
    return db.scalar(select(func.count()).select_from(RegistroInasistencia).where(
        RegistroInasistencia.paciente_id == paciente_id, RegistroInasistencia.verificada.is_(True))) or 0


# ---------- lista de espera ----------
def agregar_lista_espera(db: Session, actor: Actor, **datos) -> ListaEspera:
    paciente = db.get(Paciente, datos["paciente_id"])
    if paciente is None or not paciente.activo:
        raise ErrorNegocio("paciente_no_encontrado", "El paciente no existe o está inactivo.", 404)
    existe = db.scalar(select(ListaEspera.id).where(ListaEspera.paciente_id == datos["paciente_id"], ListaEspera.especialidad_id == datos["especialidad_id"],
                                                    ListaEspera.estado == EstadoListaEspera.ACTIVA))
    if existe:
        raise ErrorNegocio("ya_en_lista_espera", "El paciente ya está en la lista de espera de esa especialidad.", 409)
    item = ListaEspera(**datos)
    db.add(item)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ErrorNegocio("datos_fuera_de_regla", "Datos inválidos para la lista de espera.", 422) from exc
    auditoria.registrar(db, accion="lista_espera_agregada", actor=actor.tipo.value.lower(), usuario_id=actor.usuario_id, entidad="lista_espera", entidad_id=item.id)
    db.commit()
    return item


def cancelar_lista_espera(db: Session, item_id: int, actor: Actor) -> ListaEspera:
    item = db.get(ListaEspera, item_id)
    if item is None:
        raise ErrorNegocio("no_encontrado", "El registro no existe.", 404)
    if item.estado not in (EstadoListaEspera.ACTIVA, EstadoListaEspera.NOTIFICADA):
        raise ErrorNegocio("estado_no_permite", "Ese registro ya no está activo.", 409)
    item.estado = EstadoListaEspera.CANCELADA
    auditoria.registrar(db, accion="lista_espera_cancelada", actor=actor.tipo.value.lower(), usuario_id=actor.usuario_id, entidad="lista_espera", entidad_id=item.id)
    db.commit()
    return item


def candidatos_lista_espera(db: Session, cita: Cita) -> list[ListaEspera]:
    """Quién esperaba un cupo como el de esta cita (mismo orden de llegada). El aviso lo encola `notificacion_service.avisar_cupo_liberado`."""
    dia = cita.inicio.astimezone(agenda_service._tz()).date()
    q = select(ListaEspera).where(ListaEspera.estado == EstadoListaEspera.ACTIVA, ListaEspera.especialidad_id == cita.especialidad_id,
                                  ListaEspera.desde <= dia, ListaEspera.hasta >= dia, ListaEspera.paciente_id != cita.paciente_id)
    candidatos = list(db.scalars(q.order_by(ListaEspera.created_at, ListaEspera.id)))
    return [c for c in candidatos if (c.profesional_id in (None, cita.profesional_id)) and (c.sede_id in (None, cita.sede_id))
            and (c.servicio_id in (None, cita.servicio_id))]


def asignar_desde_lista_espera(db: Session, item_id: int, profesional_id: int, inicio: datetime, actor: Actor, canal: CanalCita = CanalCita.PRESENCIAL) -> Cita:
    item = db.get(ListaEspera, item_id)
    if item is None or item.estado not in (EstadoListaEspera.ACTIVA, EstadoListaEspera.NOTIFICADA):
        raise ErrorNegocio("no_encontrado", "No hay un registro activo de lista de espera con ese identificador.", 404)
    cita = agendar(db, paciente_id=item.paciente_id, profesional_id=profesional_id, especialidad_id=item.especialidad_id, inicio=inicio,
                   actor=actor, canal=canal, servicio_id=item.servicio_id, sede_id=item.sede_id)
    item.estado, item.cita_id = EstadoListaEspera.ASIGNADA, cita.id
    db.commit()
    return cita

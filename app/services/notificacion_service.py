"""Avisos a pacientes por correo: encolar (dentro de la transacción del hecho), enviar con reintentos, recordatorios y cupos liberados.

Principios: (1) un aviso fallido NUNCA impide agendar o cancelar; (2) cada aviso se encola una sola vez (clave única); (3) antes de enviar se
vuelve a revisar que siga siendo cierto (la cita no cambió, el paciente no apagó los avisos); (4) el texto no revela datos clínicos;
(5) lo que no se envió queda registrado como omitido con su motivo, nunca como «enviado»; (6) los registros y la auditoría no guardan
el correo ni el contenido."""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.fechas import texto_fecha
from app.models import Cita, ListaEspera, Notificacion, Paciente, Sede
from app.models.enums import CanalNotificacion, EstadoCita, EstadoListaEspera, EstadoNotificacion, TipoNotificacion
from app.notificaciones import emisores, plantillas
from app.notificaciones.emisores import ErrorDeEnvio
from app.services import agenda_service, auditoria

log = logging.getLogger("notificaciones")
ACTIVAS = (EstadoCita.PROGRAMADA, EstadoCita.CONFIRMADA)
CON_HORARIO = (TipoNotificacion.RECORDATORIO, TipoNotificacion.CUPO_DISPONIBLE)  # los avisos que no son urgentes no se mandan de madrugada
CON_CITA_VIVA = (TipoNotificacion.CITA_AGENDADA, TipoNotificacion.CITA_REPROGRAMADA, TipoNotificacion.RECORDATORIO)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def enmascarar(email: str) -> str:
    local, _, dominio = email.partition("@")
    return f"{local[:1]}***@{dominio}"


def _datos(db: Session, paciente: Paciente, cita: Cita, *, con_codigo: bool = True) -> plantillas.Datos:
    sede = db.get(Sede, cita.sede_id)
    return plantillas.Datos(nombre=(paciente.nombres.split() or ["paciente"])[0].title(), institucion=get_settings().institution_name,
                            cuando=texto_fecha(cita.inicio), sede=sede.nombre if sede else "", codigo=cita.codigo_reserva if con_codigo else "")


# ======================= encolar =======================
def encolar(db: Session, *, paciente: Paciente, tipo: TipoNotificacion, cita: Cita, clave: str, espera: ListaEspera | None = None,
            programada_para: datetime | None = None, contexto: dict | None = None) -> Notificacion | None:
    """Registra un aviso. Si ya existía (misma clave) devuelve el anterior. Si no se puede enviar a propósito, queda OMITIDA con su motivo."""
    s = get_settings()
    if not s.notificaciones_activas:
        return None
    previa = db.scalar(select(Notificacion).where(Notificacion.clave_unica == clave))
    if previa is not None:
        return previa
    canal = CanalNotificacion.EMAIL
    salida = emisores.emisor(canal)
    n = Notificacion(clave_unica=clave, paciente_id=paciente.id, tipo=tipo, canal=canal, programada_para=programada_para or _ahora(), contexto=contexto,
                     cita_id=None if espera else cita.id, lista_espera_id=espera.id if espera else None)  # el aviso de cupo no se enlaza a la cita de OTRA persona
    motivo = None
    if not paciente.activo or not paciente.notificaciones_activas:
        motivo = "paciente_no_acepta"
    elif not paciente.email:
        motivo = "sin_email"
    elif salida is None or not salida.configurado():
        motivo = "canal_no_configurado"
    if motivo:
        n.estado, n.motivo = EstadoNotificacion.OMITIDA, motivo  # sin asunto ni cuerpo: no se guarda lo que no se va a enviar
    else:
        n.destino_enmascarado = enmascarar(paciente.email)
        n.asunto, n.cuerpo = plantillas.render(tipo, _datos(db, paciente, cita, con_codigo=espera is None))
    try:
        with db.begin_nested():
            db.add(n)
            db.flush()
    except IntegrityError:  # otro proceso la encoló justo ahora
        return db.scalar(select(Notificacion).where(Notificacion.clave_unica == clave))
    return n


def avisar_cita(db: Session, cita: Cita, tipo: TipoNotificacion) -> None:
    """Aviso sobre una cita del propio paciente. Nunca lanza: un aviso que falla no debe deshacer la cita."""
    try:
        paciente = db.get(Paciente, cita.paciente_id)
        extra = f":{cita.reprogramaciones}" if tipo == TipoNotificacion.CITA_REPROGRAMADA else ""
        encolar(db, paciente=paciente, tipo=tipo, cita=cita, clave=f"{tipo.value.lower()}:{cita.id}{extra}", contexto={"cita_inicio": _iso(cita.inicio)})
    except Exception:  # noqa: BLE001
        log.exception("notificaciones: no se pudo encolar el aviso de la cita %s", cita.id)


def avisar_cupo_liberado(db: Session, cita_cancelada: Cita, candidatos: list[ListaEspera]) -> int:
    """Avisa, por orden de llegada, a las primeras personas en espera (máximo configurable). No reserva nada: quien responda primero lo ocupa."""
    maximo, avisados = get_settings().espera_notificar_maximo, 0
    for item in candidatos:
        if avisados >= maximo:
            break
        try:
            paciente = db.get(Paciente, item.paciente_id)
            n = encolar(db, paciente=paciente, tipo=TipoNotificacion.CUPO_DISPONIBLE, cita=cita_cancelada, espera=item,
                        clave=f"cupo:{item.id}:{cita_cancelada.id}", contexto={"cupo_inicio": _iso(cita_cancelada.inicio)})
        except Exception:  # noqa: BLE001
            log.exception("notificaciones: no se pudo avisar a la lista de espera %s", item.id)
            continue
        if n is not None and n.estado == EstadoNotificacion.PENDIENTE:  # a quien no se pudo avisar (sin correo...) sigue ACTIVA para que una persona lo contacte
            item.estado = EstadoListaEspera.NOTIFICADA
            avisados += 1
    return avisados


# ======================= recordatorios =======================
def generar_recordatorios(db: Session, ahora: datetime | None = None, limite: int = 500) -> int:
    """Encola el recordatorio de las citas que empiezan dentro de la ventana (p. ej. en menos de 24 h y en más de 2 h)."""
    s, ahora = get_settings(), ahora or _ahora()
    citas = db.scalars(select(Cita).where(Cita.estado.in_(ACTIVAS), Cita.inicio > ahora + timedelta(hours=s.recordatorio_minimo_horas),
                                          Cita.inicio <= ahora + timedelta(hours=s.recordatorio_horas_antes)).order_by(Cita.inicio).limit(limite)).all()
    nuevos = 0
    for cita in citas:
        clave = f"recordatorio:{cita.id}:{int(cita.inicio.timestamp())}"  # si la cita se reprograma, corresponde un recordatorio nuevo
        if db.scalar(select(Notificacion.id).where(Notificacion.clave_unica == clave)):
            continue
        try:
            encolar(db, paciente=db.get(Paciente, cita.paciente_id), tipo=TipoNotificacion.RECORDATORIO, cita=cita, clave=clave,
                    programada_para=max(ahora, cita.inicio - timedelta(hours=s.recordatorio_horas_antes)), contexto={"cita_inicio": _iso(cita.inicio)})
            nuevos += 1
        except Exception:  # noqa: BLE001
            log.exception("notificaciones: no se pudo encolar el recordatorio de la cita %s", cita.id)
    db.commit()
    return nuevos


# ======================= enviar =======================
def _en_horario(ahora: datetime) -> bool:
    s = get_settings()
    return s.notificaciones_hora_desde <= ahora.astimezone(agenda_service._tz()).hour < s.notificaciones_hora_hasta


def _motivo_para_omitir(db: Session, n: Notificacion, paciente: Paciente | None, ahora: datetime) -> str | None:
    """Revisión de último momento: lo que era cierto al encolar puede haber cambiado."""
    if paciente is None or not paciente.activo or not paciente.notificaciones_activas:
        return "paciente_no_acepta"
    if not paciente.email:
        return "sin_email"
    if n.tipo in CON_CITA_VIVA:
        cita = db.get(Cita, n.cita_id) if n.cita_id else None
        if cita is None or cita.estado not in ACTIVAS or _iso(cita.inicio) != (n.contexto or {}).get("cita_inicio"):
            return "cita_cambio"
        if cita.inicio <= ahora:
            return "cita_pasada"
    elif n.tipo == TipoNotificacion.CUPO_DISPONIBLE:
        item = db.get(ListaEspera, n.lista_espera_id) if n.lista_espera_id else None
        if item is None or item.estado != EstadoListaEspera.NOTIFICADA:
            return "espera_cambio"
        if (n.contexto or {}).get("cupo_inicio", "") <= _iso(ahora):
            return "cupo_pasado"
    return None


def _enviar_una(db: Session, n: Notificacion, ahora: datetime) -> str:
    s = get_settings()
    if n.tipo in CON_HORARIO and not _en_horario(ahora):
        return "aplazada"  # queda pendiente hasta que abra la franja diurna
    paciente = db.get(Paciente, n.paciente_id)
    motivo = _motivo_para_omitir(db, n, paciente, ahora)
    salida = emisores.emisor(n.canal)
    if motivo is None and (salida is None or not salida.configurado()):
        motivo = "canal_no_configurado"
    if motivo:
        n.estado, n.motivo, n.cuerpo = EstadoNotificacion.OMITIDA, motivo, ""
        resultado = "omitida"
    else:
        n.intentos += 1
        try:
            salida.enviar(paciente.email, n.asunto, n.cuerpo)
        except ErrorDeEnvio as e:
            n.motivo = e.codigo
            if not e.reintentable or n.intentos >= s.notificaciones_max_intentos:
                n.estado = EstadoNotificacion.FALLIDA
                resultado = "fallida"
            else:
                n.programada_para = ahora + timedelta(minutes=s.notificaciones_espera_reintento_min * n.intentos)
                resultado = "reintento"
        else:
            n.estado, n.enviada_en, n.motivo = EstadoNotificacion.ENVIADA, ahora, None
            resultado = "enviada"
    auditoria.registrar(db, accion=f"notificacion_{resultado}", actor="sistema", entidad="notificacion", entidad_id=n.id,
                        exito=resultado in ("enviada", "omitida", "reintento"), detalle={"tipo": n.tipo.value, "canal": n.canal.value, **({"motivo": n.motivo} if n.motivo else {})})
    return resultado


def procesar_pendientes(db: Session, ahora: datetime | None = None, limite: int = 50) -> dict[str, int]:
    """Envía lo que ya toca. Cada aviso se bloquea (SKIP LOCKED) y se confirma por separado: varios procesos pueden correr a la vez sin duplicar."""
    ahora, resultados = ahora or _ahora(), Counter()
    ids = db.scalars(select(Notificacion.id).where(Notificacion.estado == EstadoNotificacion.PENDIENTE, Notificacion.programada_para <= ahora)
                     .order_by(Notificacion.programada_para, Notificacion.id).limit(limite)).all()
    for nid in ids:
        n = db.scalar(select(Notificacion).where(Notificacion.id == nid, Notificacion.estado == EstadoNotificacion.PENDIENTE).with_for_update(skip_locked=True))
        if n is None:
            continue
        resultados[_enviar_una(db, n, ahora)] += 1
        db.commit()
    return dict(resultados)


def reintentar(db: Session, notificacion_id: int) -> Notificacion:
    """Una persona autorizada vuelve a poner en cola un aviso fallido."""
    from app.core.errores import ErrorNegocio

    n = db.get(Notificacion, notificacion_id)
    if n is None:
        raise ErrorNegocio("no_encontrado", "La notificación no existe.", 404)
    if n.estado != EstadoNotificacion.FALLIDA:
        raise ErrorNegocio("estado_no_permite", "Solo se reintentan avisos que fallaron.", 409)
    n.estado, n.intentos, n.motivo, n.programada_para = EstadoNotificacion.PENDIENTE, 0, None, _ahora()
    db.commit()
    return n

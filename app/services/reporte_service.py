"""Reportes agregados: citas, asistente, avisos y solicitudes. Solo cifras: ningún reporte trae nombres, documentos ni contactos de pacientes.
Reglas: (1) las conversaciones de prueba del personal NO cuentan; (2) una inasistencia solo cuenta si una persona autorizada la verificó;
(3) cuando una tasa no se puede calcular (divisor 0) se muestra «N/D», nunca 0 %; (4) el período se interpreta en la hora de la institución."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import Date, Float, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import (Cita, Conversacion, EjecucionHerramienta, EncuestaSatisfaccion, Especialidad, Institucion, ListaEspera, Mensaje, Notificacion, Profesional, RegistroInasistencia, Sede,
                        Solicitud)
from app.models.enums import EstadoCita, ResultadoHerramienta, RolMensaje
from app.services import agenda_service

MAX_DIAS = 366
CLAVES = ("citas", "asistente", "avisos", "solicitudes", "satisfaccion")
TITULOS = {"citas": "Citas", "asistente": "Asistente conversacional", "avisos": "Avisos a pacientes", "solicitudes": "Solicitudes, quejas y reclamos", "satisfaccion": "Satisfacción con la atención"}


@dataclass
class Tabla:
    titulo: str
    columnas: list[str]
    filas: list[list[Any]]


@dataclass
class Reporte:
    clave: str
    titulo: str
    desde: date
    hasta: date
    generado_en: datetime
    indicadores: list[tuple[str, Any]] = field(default_factory=list)
    tablas: list[Tabla] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)


def rango(desde: date | None, hasta: date | None) -> tuple[date, date, datetime, datetime]:
    """(desde, hasta, inicio UTC, fin UTC exclusivo). Por defecto, los últimos 30 días hasta hoy."""
    tz = agenda_service._tz()
    hoy = datetime.now(tz).date()
    hasta = hasta or hoy
    desde = desde or hasta - timedelta(days=29)
    if desde > hasta:
        raise ErrorNegocio("rango_invalido", "La fecha inicial no puede ser posterior a la final.", 422)
    if (hasta - desde).days + 1 > MAX_DIAS:
        raise ErrorNegocio("rango_muy_largo", f"El período no puede pasar de {MAX_DIAS} días.", 422)
    ini = datetime.combine(desde, time.min, tzinfo=tz).astimezone(timezone.utc)
    fin = datetime.combine(hasta + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return desde, hasta, ini, fin


def _pct(parte: int, total: int) -> str:
    return "N/D" if total == 0 else f"{round(100 * parte / total, 1)} %"


def _minutos(segundos: float | None) -> str:
    return "N/D" if segundos is None else f"{round(segundos / 60, 1)} min"


def _contar(db: Session, columna, *condiciones, desde=None) -> list[list[Any]]:
    q = select(columna, func.count()).group_by(columna).order_by(func.count().desc(), columna)
    if desde is not None:
        q = q.select_from(desde)
    return [[str(getattr(k, "value", k) if k is not None else "(sin dato)"), n] for k, n in db.execute(q.where(*condiciones)).all()]


# ======================= citas =======================
def _citas_por(db: Session, nombre_col, unir, donde) -> list[list[Any]]:
    cuenta = lambda e: func.count(case((Cita.estado == e, 1)))  # noqa: E731
    q = (select(nombre_col, func.count(Cita.id), cuenta(EstadoCita.ATENDIDA), cuenta(EstadoCita.CANCELADA), cuenta(EstadoCita.INASISTENCIA))
         .select_from(Cita).join(*unir).where(*donde).group_by(nombre_col).order_by(func.count(Cita.id).desc(), nombre_col))
    return [list(f) for f in db.execute(q).all()]


def reporte_citas(db: Session, desde: date, hasta: date, ini: datetime, fin: datetime) -> Reporte:
    donde = [Cita.inicio >= ini, Cita.inicio < fin]
    r = Reporte("citas", TITULOS["citas"], desde, hasta, datetime.now(timezone.utc))
    por_estado = dict(db.execute(select(Cita.estado, func.count()).where(*donde).group_by(Cita.estado)).all())
    total = sum(por_estado.values())
    n = lambda e: por_estado.get(e, 0)  # noqa: E731
    verificadas = db.scalar(select(func.count()).select_from(RegistroInasistencia).join(Cita, Cita.id == RegistroInasistencia.cita_id)
                            .where(*donde, RegistroInasistencia.verificada.is_(True))) or 0
    pendientes = db.scalar(select(func.count()).select_from(RegistroInasistencia).join(Cita, Cita.id == RegistroInasistencia.cita_id)
                           .where(*donde, RegistroInasistencia.verificada_en.is_(None))) or 0
    ya_debieron = n(EstadoCita.ATENDIDA) + n(EstadoCita.INASISTENCIA) + db.scalar(
        select(func.count()).where(*donde, Cita.estado.in_((EstadoCita.PROGRAMADA, EstadoCita.CONFIRMADA)), Cita.inicio < datetime.now(timezone.utc)))
    reprogramaciones = db.scalar(select(func.coalesce(func.sum(Cita.reprogramaciones), 0)).where(*donde)) or 0
    r.indicadores = [("Citas en el período", total)] + [(e.value.capitalize(), n(e)) for e in EstadoCita] + [
        ("Tasa de cancelación", _pct(n(EstadoCita.CANCELADA), total)),
        ("Inasistencias verificadas", verificadas),
        ("Inasistencias por verificar", pendientes),
        ("Tasa de inasistencia verificada (sobre citas cuya hora ya pasó y no se cancelaron)", _pct(verificadas, ya_debieron)),
        ("Reprogramaciones", reprogramaciones)]
    cols = ["Total", "Atendidas", "Canceladas", "Inasistencias"]
    r.tablas = [
        Tabla("Por especialidad", ["Especialidad", *cols], _citas_por(db, Especialidad.nombre, (Especialidad, Especialidad.id == Cita.especialidad_id), donde)),
        Tabla("Por profesional", ["Profesional", *cols], _citas_por(db, Profesional.nombre_completo, (Profesional, Profesional.id == Cita.profesional_id), donde)),
        Tabla("Por sede", ["Sede", *cols], _citas_por(db, Sede.nombre, (Sede, Sede.id == Cita.sede_id), donde)),
        Tabla("Por canal de agendamiento", ["Canal", "Citas"], _contar(db, Cita.canal, *donde, desde=Cita)),
        Tabla("Por día", ["Día", "Citas"], [[d.isoformat(), c] for d, c in db.execute(
            select(cast(func.timezone(get_settings().timezone, Cita.inicio), Date).label("dia"), func.count()).where(*donde).group_by("dia").order_by("dia")).all()]),
        Tabla("Lista de espera (personas que entraron en el período)", ["Estado", "Personas"],
              _contar(db, ListaEspera.estado, ListaEspera.created_at >= ini, ListaEspera.created_at < fin, desde=ListaEspera)),
    ]
    r.notas = ["Las citas se cuentan por la fecha en que ocurren (o iban a ocurrir), no por la de su creación.",
               "Una inasistencia solo cuenta cuando una persona autorizada la verifica; las pendientes se muestran aparte.",
               "N/D: no se puede calcular porque el divisor es cero."]
    return r


# ======================= asistente =======================
def reporte_asistente(db: Session, desde: date, hasta: date, ini: datetime, fin: datetime) -> Reporte:
    conv = [Conversacion.es_prueba.is_(False), Conversacion.iniciada_en >= ini, Conversacion.iniciada_en < fin]
    r = Reporte("asistente", TITULOS["asistente"], desde, hasta, datetime.now(timezone.utc))
    total = db.scalar(select(func.count()).select_from(Conversacion).where(*conv)) or 0
    por_estado = dict(db.execute(select(Conversacion.estado, func.count()).where(*conv).group_by(Conversacion.estado)).all())
    verificadas = db.scalar(select(func.count()).select_from(Conversacion).where(*conv, Conversacion.identidad_verificada.is_(True))) or 0
    duracion = db.scalar(select(func.avg(cast(func.extract("epoch", Conversacion.finalizada_en - Conversacion.iniciada_en), Float))).where(*conv, Conversacion.finalizada_en.is_not(None)))
    mensajes = (Mensaje.rol == RolMensaje.AGENTE, Mensaje.intencion.is_not(None))
    por_intencion = db.execute(select(Mensaje.intencion, func.count()).join(Conversacion, Conversacion.id == Mensaje.conversacion_id).where(*conv, *mensajes)
                               .group_by(Mensaje.intencion).order_by(func.count().desc(), Mensaje.intencion)).all()
    intenciones = dict(por_intencion)
    citas_agente = db.scalar(select(func.count()).select_from(Cita).join(Conversacion, Conversacion.id == Cita.conversacion_id).where(*conv)) or 0
    r.indicadores = [
        ("Conversaciones (sin las de prueba)", total),
        ("Transferidas a una persona", por_estado.get("TRANSFERIDA", 0)), ("% transferidas", _pct(por_estado.get("TRANSFERIDA", 0), total)),
        ("Con identidad verificada", verificadas), ("% con identidad verificada", _pct(verificadas, total)),
        ("Abandonadas", por_estado.get("ABANDONADA", 0)), ("Con error", por_estado.get("ERROR", 0)),
        ("Posibles emergencias detectadas", intenciones.get("emergencia", 0)),
        ("Respuestas «no entendí el audio»", intenciones.get("no_entendido", 0)),
        ("Citas agendadas dentro de conversaciones", citas_agente),
        ("Duración media de las conversaciones terminadas", _minutos(duracion))]
    ejecs = [EjecucionHerramienta.creado_en >= ini, EjecucionHerramienta.creado_en < fin, Conversacion.es_prueba.is_(False)]
    herramientas = db.execute(
        select(EjecucionHerramienta.nombre, func.count(), func.count(case((EjecucionHerramienta.resultado == ResultadoHerramienta.EXITO, 1))),
               func.count(case((EjecucionHerramienta.resultado == ResultadoHerramienta.RECHAZADA, 1))), func.count(case((EjecucionHerramienta.resultado == ResultadoHerramienta.ERROR, 1))),
               func.round(func.coalesce(func.avg(EjecucionHerramienta.duracion_ms), 0)))
        .join(Conversacion, Conversacion.id == EjecucionHerramienta.conversacion_id).where(*ejecs).group_by(EjecucionHerramienta.nombre).order_by(func.count().desc(), EjecucionHerramienta.nombre)).all()
    r.tablas = [
        Tabla("Por canal", ["Canal", "Conversaciones"], _contar(db, Conversacion.canal, *conv, desde=Conversacion)),
        Tabla("Por estado final", ["Estado", "Conversaciones"], _contar(db, Conversacion.estado, *conv, desde=Conversacion)),
        Tabla("Respuestas por tipo de intención", ["Intención", "Respuestas"], [[k, c] for k, c in por_intencion]),
        Tabla("Herramientas del asistente", ["Herramienta", "Llamadas", "Éxito", "Rechazadas por reglas", "Error", "Duración media (ms)"], [list(f) for f in herramientas]),
    ]
    r.notas = ["Las conversaciones de prueba del personal no se cuentan. Mientras la interfaz de pacientes no esté abierta, es normal que este reporte salga en cero.",
               "«Rechazadas por reglas»: el backend negó la acción (permiso, política, dato inválido); es una protección, no un fallo.",
               "No incluye lo que dijo ninguna persona: el contenido de las conversaciones solo lo ve quien tiene permiso de transcripciones."]
    return r


# ======================= avisos =======================
def reporte_avisos(db: Session, desde: date, hasta: date, ini: datetime, fin: datetime) -> Reporte:
    donde = [Notificacion.created_at >= ini, Notificacion.created_at < fin]
    r = Reporte("avisos", TITULOS["avisos"], desde, hasta, datetime.now(timezone.utc))
    por_estado = dict(db.execute(select(Notificacion.estado, func.count()).where(*donde).group_by(Notificacion.estado)).all())
    total = sum(por_estado.values())
    enviadas, fallidas, omitidas = (por_estado.get(e, 0) for e in ("ENVIADA", "FALLIDA", "OMITIDA"))
    r.indicadores = [("Avisos generados", total), ("Enviados", enviadas), ("Fallidos", fallidas), ("Omitidos", omitidas), ("Pendientes", por_estado.get("PENDIENTE", 0)),
                     ("% enviados sobre los que se intentaron enviar", _pct(enviadas, enviadas + fallidas))]
    cruzado = db.execute(select(Notificacion.tipo, Notificacion.estado, func.count()).where(*donde).group_by(Notificacion.tipo, Notificacion.estado)).all()
    estados = ["PENDIENTE", "ENVIADA", "FALLIDA", "OMITIDA"]
    tipos = sorted({t.value for t, _, _ in cruzado})
    celdas = {(t.value, e.value): c for t, e, c in cruzado}
    r.tablas = [
        Tabla("Por tipo y estado", ["Tipo", *[e.capitalize() for e in estados]], [[t, *[celdas.get((t, e), 0) for e in estados]] for t in tipos]),
        Tabla("Motivos de omisión o fallo", ["Motivo", "Avisos"], _contar(db, Notificacion.motivo, *donde, Notificacion.motivo.is_not(None), desde=Notificacion)),
        Tabla("Por canal", ["Canal", "Avisos"], _contar(db, Notificacion.canal, *donde, desde=Notificacion))]
    r.notas = ["«Omitido» significa que no se envió a propósito (sin correo, el paciente apagó los avisos, el correo no está configurado o la cita cambió).",
               "Solo existe el canal de correo; no hay SMS ni WhatsApp."]
    return r


# ======================= solicitudes =======================
def reporte_solicitudes(db: Session, desde: date, hasta: date, ini: datetime, fin: datetime) -> Reporte:
    donde = [Solicitud.created_at >= ini, Solicitud.created_at < fin]
    r = Reporte("solicitudes", TITULOS["solicitudes"], desde, hasta, datetime.now(timezone.utc))
    total = db.scalar(select(func.count()).select_from(Solicitud).where(*donde)) or 0
    abiertas = db.scalar(select(func.count()).select_from(Solicitud).where(*donde, Solicitud.estado.in_(("ABIERTA", "EN_PROCESO")))) or 0
    segundos = db.scalar(select(func.avg(cast(func.extract("epoch", Solicitud.cerrada_en - Solicitud.created_at), Float))).where(*donde, Solicitud.cerrada_en.is_not(None)))
    r.indicadores = [("Solicitudes en el período", total), ("Todavía abiertas o en proceso", abiertas), ("Tiempo medio hasta cerrarlas", _minutos(segundos))]
    r.tablas = [Tabla("Por tipo", ["Tipo", "Solicitudes"], _contar(db, Solicitud.tipo, *donde, desde=Solicitud)),
                Tabla("Por estado", ["Estado", "Solicitudes"], _contar(db, Solicitud.estado, *donde, desde=Solicitud))]
    r.notas = ["No incluye el asunto ni la descripción de ninguna solicitud: pueden contener datos personales."]
    return r


# ======================= satisfacción =======================
def reporte_satisfaccion(db: Session, desde: date, hasta: date, ini: datetime, fin: datetime) -> Reporte:
    """Las calificaciones (1 a 5) que los pacientes dieron al final de la conversación. Sin datos del paciente; los comentarios son anónimos."""
    base = [Conversacion.es_prueba.is_(False), EncuestaSatisfaccion.creado_en >= ini, EncuestaSatisfaccion.creado_en < fin]
    r = Reporte("satisfaccion", TITULOS["satisfaccion"], desde, hasta, datetime.now(timezone.utc))
    dentro = lambda *cols: select(*cols).select_from(EncuestaSatisfaccion).join(Conversacion, Conversacion.id == EncuestaSatisfaccion.conversacion_id).where(*base)  # noqa: E731
    total = db.scalar(dentro(func.count())) or 0
    promedio = db.scalar(dentro(func.avg(EncuestaSatisfaccion.puntaje)))
    por_puntaje = dict(db.execute(dentro(EncuestaSatisfaccion.puntaje, func.count()).group_by(EncuestaSatisfaccion.puntaje)).all())
    con_comentario = db.scalar(dentro(func.count()).where(EncuestaSatisfaccion.comentario.is_not(None))) or 0
    satisfechos, insatisfechos = por_puntaje.get(4, 0) + por_puntaje.get(5, 0), por_puntaje.get(1, 0) + por_puntaje.get(2, 0)
    voz_total = db.scalar(select(func.count()).select_from(Conversacion).where(Conversacion.es_prueba.is_(False), Conversacion.canal == "VOZ", Conversacion.iniciada_en >= ini, Conversacion.iniciada_en < fin,
                                                                                 Conversacion.estado == "RESUELTA")) or 0
    de_prueba = db.scalar(select(func.count()).select_from(EncuestaSatisfaccion).join(Conversacion, Conversacion.id == EncuestaSatisfaccion.conversacion_id)
                          .where(Conversacion.es_prueba.is_(True), EncuestaSatisfaccion.creado_en >= ini, EncuestaSatisfaccion.creado_en < fin)) or 0
    r.indicadores = [
        ("Encuestas respondidas", total), ("Calificación promedio (1 a 5)", "N/D" if promedio is None else round(float(promedio), 2)),
        ("% satisfechos (4 o 5)", _pct(satisfechos, total)), ("% insatisfechos (1 o 2)", _pct(insatisfechos, total)), ("Con comentario", con_comentario),
        ("% de las llamadas de voz terminadas que respondieron", _pct(total, voz_total)), ("Encuestas de conversaciones de prueba (no cuentan)", de_prueba)]
    por_inst = db.execute(dentro(func.coalesce(Institucion.nombre, "(sin institución)"), func.count(), func.round(func.avg(EncuestaSatisfaccion.puntaje), 2))
                          .outerjoin(Institucion, Institucion.id == EncuestaSatisfaccion.institucion_id).group_by(Institucion.nombre).order_by(func.count().desc())).all()
    comentarios = db.execute(dentro(EncuestaSatisfaccion.creado_en, EncuestaSatisfaccion.puntaje, EncuestaSatisfaccion.comentario).where(EncuestaSatisfaccion.comentario.is_not(None))
                             .order_by(EncuestaSatisfaccion.creado_en.desc()).limit(20)).all()
    tz = agenda_service._tz()
    r.tablas = [
        Tabla("Distribución de las calificaciones", ["Calificación", "Respuestas"], [[str(p), por_puntaje.get(p, 0)] for p in (5, 4, 3, 2, 1)]),
        Tabla("Por institución", ["Institución", "Respuestas", "Promedio"], [[n, c, float(a)] for n, c, a in por_inst]),
        Tabla("Últimos comentarios (anónimos)", ["Fecha", "Calificación", "Comentario"], [[f.astimezone(tz).strftime("%Y-%m-%d %H:%M"), p, c] for f, p, c in comentarios]),
    ]
    r.notas = ["La encuesta se hace por voz al final de la conversación, solo si el paciente confirma que no necesita más ayuda; puede no responderla. No se liga al paciente.",
               "Si la calificación es 3 o menos, el asistente pregunta una vez qué podría mejorar; ese comentario es opcional y puede contener datos personales: trátelo con cuidado.",
               "Las conversaciones de prueba del personal no se cuentan."]
    return r


_GENERADORES = {"satisfaccion": reporte_satisfaccion, "citas": reporte_citas, "asistente": reporte_asistente, "avisos": reporte_avisos, "solicitudes": reporte_solicitudes}


def generar(db: Session, clave: str, desde: date | None, hasta: date | None) -> Reporte:
    if clave not in _GENERADORES:
        raise ErrorNegocio("reporte_desconocido", f"Reporte desconocido. Disponibles: {', '.join(CLAVES)}.", 404)
    d, h, ini, fin = rango(desde, hasta)
    return _GENERADORES[clave](db, d, h, ini, fin)

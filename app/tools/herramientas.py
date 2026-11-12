"""Las 15 herramientas del asistente. Reglas comunes:
- El LLM NUNCA pasa un paciente_id: se usa el de la conversación (identidad ya verificada).
- Solo se agenda/reprograma a un cupo que se le ofreció al paciente en esta conversación (por su número).
- Los resultados llevan lo mínimo necesario (primer nombre, fechas, códigos), nunca documentos ni datos de contacto.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agents import seguridad
from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.core.fechas import DIAS as _DIAS, MESES as _MESES, texto_fecha  # noqa: F401 - texto_fecha se importa desde aquí en las pruebas
from app.models import Especialidad, HorarioProfesional, Paciente, Profesional, Requisito, Sede, Solicitud
from app.models.enums import CanalCita, CanalConversacion, EstadoCita, EstadoSolicitud, TipoDocumento, TipoPolitica, TipoSolicitud
from app.repositories import paciente_repository
from app.schemas.paciente import ConsentimientoIn
from app.security import crypto
from app.services import agenda_service, auditoria, cita_service, institucion_service, paciente_service, politica_service
from app.tools.contexto import ContextoHerramientas, CupoOfrecido
from app.tools.registro import REGISTRO, Herramienta

MAX_CUPOS_OFRECIDOS = 6
MAX_DIAS_CONSULTA = 14


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)




class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)  # un campo no previsto (p. ej. paciente_id) se rechaza


# ---------- resolución de nombres (el paciente habla en lenguaje natural) ----------
def _resolver(db, modelo, texto: str, extraer, etiqueta: str, filtro=None):
    clave = seguridad.normalizar(texto)
    todos = [x for x in db.scalars(select(modelo)) if (filtro is None or filtro(x))]
    exactos = [x for x in todos if seguridad.normalizar(extraer(x)) == clave]
    parecidos = exactos or [x for x in todos if clave and (clave in seguridad.normalizar(extraer(x)) or seguridad.normalizar(extraer(x)) in clave)]
    if len(parecidos) == 1:
        return parecidos[0]
    opciones = ", ".join(sorted(extraer(x) for x in (parecidos or todos))[:12])
    if not parecidos:
        raise ErrorNegocio(f"{etiqueta}_no_encontrada", f"No encontré {etiqueta} «{texto}». Opciones: {opciones or 'ninguna registrada'}.", 422)
    raise ErrorNegocio(f"{etiqueta}_ambigua", f"Hay varias coincidencias para «{texto}»: {opciones}. Pida al paciente que precise.", 422)


def _es_de_la_institucion(ctx, obj) -> bool:
    """Lo de la institución con la que se habla (y lo compartido). Sin institución en la conversación no se filtra."""
    return ctx.institucion_id is None or obj.institucion_id in (None, ctx.institucion_id)


def _especialidad(ctx, texto: str) -> Especialidad:
    return _resolver(ctx.db, Especialidad, texto, lambda e: e.nombre, "especialidad", lambda e: e.activa and _es_de_la_institucion(ctx, e))


def _profesional(ctx, texto: str) -> Profesional:
    return _resolver(ctx.db, Profesional, texto, lambda p: p.nombre_completo, "profesional", lambda p: p.activo and _es_de_la_institucion(ctx, p))


# ======================= identidad =======================
class ArgsBuscarPaciente(_Args):
    tipo_documento: TipoDocumento = Field(description="CC, TI, RC, CE, PA o PPT")
    numero_documento: str = Field(min_length=4, max_length=20)
    fecha_nacimiento: date = Field(description="AAAA-MM-DD")


def buscar_paciente(ctx: ContextoHerramientas, a: ArgsBuscarPaciente) -> dict:
    p = paciente_service.verificar_identidad(ctx.db, a.tipo_documento, a.numero_documento, a.fecha_nacimiento)
    if ctx.identidad_verificada and ctx.paciente_id != p.id:
        raise ErrorNegocio("otra_identidad", "En esta conversación ya se verificó a otra persona. Para atender a alguien más hay que iniciar una conversación nueva.", 403)
    ctx.paciente_id, ctx.identidad_verificada, ctx.primer_nombre = p.id, True, p.nombres.split()[0]
    consentimiento = paciente_repository.consentimiento_vigente(ctx.db, p.id)
    ctx.guarda_transcripcion = bool(consentimiento and consentimiento.aceptado and consentimiento.autoriza_transcripcion)
    return {"verificado": True, "nombre": ctx.primer_nombre}


class ArgsRegistrarPaciente(_Args):
    tipo_documento: TipoDocumento
    numero_documento: str = Field(min_length=4, max_length=20)
    nombres: str = Field(min_length=2, max_length=120)
    apellidos: str = Field(min_length=2, max_length=120)
    fecha_nacimiento: date = Field(description="AAAA-MM-DD")
    telefono: str | None = None
    email: str | None = None
    autoriza_tratamiento_datos: bool = Field(description="true solo si el paciente autorizó expresamente el tratamiento de sus datos personales")
    autoriza_transcripcion: bool = Field(default=False, description="true solo si autorizó guardar la transcripción de la conversación")


def resumen_registrar(ctx, a: ArgsRegistrarPaciente) -> str:
    return (f"Registrar a {a.nombres} {a.apellidos} ({a.tipo_documento.value} terminado en {crypto.ultimos4(a.numero_documento)}), nacido(a) el {a.fecha_nacimiento.isoformat()}, "
            "con su autorización para el tratamiento de datos personales")


def registrar_paciente(ctx: ContextoHerramientas, a: ArgsRegistrarPaciente) -> dict:
    if not ctx.politica_datos_version:
        raise ErrorNegocio("registro_no_habilitado", "El registro de pacientes por este canal no está habilitado. Ofrezca transferir a una persona de la institución.", 403)
    if ctx.identidad_verificada:
        raise ErrorNegocio("ya_verificado", "Ya hay un paciente identificado en esta conversación.", 409)
    if not a.autoriza_tratamiento_datos:
        raise ErrorNegocio("sin_consentimiento", "Sin la autorización del paciente para el tratamiento de sus datos personales no se puede registrar.", 422)
    datos = {"tipo_documento": a.tipo_documento, "numero_documento": a.numero_documento, "nombres": a.nombres, "apellidos": a.apellidos,
             "fecha_nacimiento": a.fecha_nacimiento, "telefono": a.telefono, "email": a.email}
    p = paciente_service.registrar(ctx.db, None, datos, ConsentimientoIn(politica_version=ctx.politica_datos_version, aceptado=True, autoriza_transcripcion=a.autoriza_transcripcion), canal="AGENTE")
    ctx.paciente_id, ctx.identidad_verificada, ctx.primer_nombre, ctx.guarda_transcripcion = p.id, True, p.nombres.split()[0], a.autoriza_transcripcion
    return {"registrado": True, "nombre": ctx.primer_nombre}


# ======================= catálogo =======================
class ArgsVacios(_Args):
    pass


def consultar_especialidades(ctx: ContextoHerramientas, a: ArgsVacios) -> dict:
    nombres = [e.nombre for e in ctx.db.scalars(select(Especialidad).where(Especialidad.activa, institucion_service.alcance(Especialidad.institucion_id, ctx.institucion_id)).order_by(Especialidad.nombre))]
    return {"especialidades": nombres} if nombres else {"especialidades": [], "nota": "La institución no tiene especialidades registradas todavía."}


class ArgsProfesionales(_Args):
    especialidad: str = Field(min_length=2, max_length=150)


def consultar_profesionales(ctx: ContextoHerramientas, a: ArgsProfesionales) -> dict:
    esp = _especialidad(ctx, a.especialidad)
    nombres = [p.nombre_completo for p in ctx.db.scalars(select(Profesional).where(Profesional.activo, institucion_service.alcance(Profesional.institucion_id, ctx.institucion_id))
                                                          .order_by(Profesional.nombre_completo)) if esp in p.especialidades]
    return {"especialidad": esp.nombre, "profesionales": nombres} if nombres else {"especialidad": esp.nombre, "profesionales": [], "nota": "No hay profesionales registrados para esa especialidad."}


class ArgsHorarios(_Args):
    especialidad: str | None = Field(default=None, max_length=150)
    profesional: str | None = Field(default=None, max_length=200)


def consultar_horarios(ctx: ContextoHerramientas, a: ArgsHorarios) -> dict:
    if not a.especialidad and not a.profesional:
        raise ErrorNegocio("falta_dato", "Indique una especialidad o un profesional.", 422)
    hoy = politica_service.hoy_local()
    q = select(HorarioProfesional, Profesional.nombre_completo, Sede.nombre).join(Profesional, Profesional.id == HorarioProfesional.profesional_id).join(Sede, Sede.id == HorarioProfesional.sede_id).where(
        HorarioProfesional.activo, Profesional.activo, Sede.activa, HorarioProfesional.vigente_desde <= hoy, institucion_service.alcance(Sede.institucion_id, ctx.institucion_id))
    nombre_esp = None
    if a.especialidad:
        esp = _especialidad(ctx, a.especialidad)
        nombre_esp = esp.nombre
        q = q.where(HorarioProfesional.especialidad_id == esp.id)
    if a.profesional:
        q = q.where(HorarioProfesional.profesional_id == _profesional(ctx, a.profesional).id)
    filas = [(h, p, s) for h, p, s in ctx.db.execute(q.order_by(Profesional.nombre_completo, HorarioProfesional.dia_semana, HorarioProfesional.hora_inicio)).all()
             if h.vigente_hasta is None or h.vigente_hasta >= hoy]
    horarios = [{"profesional": p, "dia": _DIAS[h.dia_semana], "desde": h.hora_inicio.strftime("%H:%M"), "hasta": h.hora_fin.strftime("%H:%M"), "sede": s} for h, p, s in filas[:24]]
    return {"especialidad": nombre_esp, "horarios": horarios} if horarios else {"horarios": [], "nota": "No hay horarios de atención registrados para eso."}


class ArgsRequisitos(_Args):
    especialidad: str | None = Field(default=None, max_length=150)


def consultar_requisitos(ctx: ContextoHerramientas, a: ArgsRequisitos) -> dict:
    esp_id = _especialidad(ctx, a.especialidad).id if a.especialidad else None
    q = select(Requisito).where(Requisito.activo).order_by(Requisito.orden, Requisito.titulo)
    items = [r for r in ctx.db.scalars(q) if r.servicio_id is None and (r.especialidad_id is None or r.especialidad_id == esp_id)]
    if not items:
        return {"requisitos": [], "nota": "No hay requisitos registrados para esto. No invente ninguno."}
    return {"requisitos": [{"titulo": r.titulo, "descripcion": r.descripcion, "aplica_a": "todas las especialidades" if r.especialidad_id is None else a.especialidad} for r in items]}


class ArgsPoliticas(_Args):
    tipo: Literal["CANCELACION", "REPROGRAMACION", "INASISTENCIA"]


def _politica_resumida(ctx, tipo: str) -> dict | None:
    p = politica_service.aplicable(ctx.db, TipoPolitica(tipo), institucion_id=ctx.institucion_id)
    if not p:
        return None
    return {"titulo": p.titulo, "mensaje": p.mensaje_paciente, "reglas": p.parametros, "consecuencia_autorizada": p.consecuencia_autorizada}


def consultar_politicas(ctx: ContextoHerramientas, a: ArgsPoliticas) -> dict:
    p = _politica_resumida(ctx, a.tipo)
    if p is None:
        return {"configurada": False, "nota": "La institución no ha registrado una política de este tipo. No invente plazos, condiciones ni sanciones; ofrezca transferir a una persona."}
    return {"configurada": True, **p}


def consultar_inasistencias(ctx: ContextoHerramientas, a: ArgsVacios) -> dict:
    n = cita_service.contar_inasistencias_verificadas(ctx.db, ctx.paciente_id)
    p = _politica_resumida(ctx, "INASISTENCIA")
    return {"inasistencias_verificadas": n, "politica": p, **({} if p else {"nota": "No hay política de inasistencia registrada; no invente consecuencias."})}


# ======================= disponibilidad y citas =======================
class ArgsDisponibilidad(_Args):
    especialidad: str = Field(min_length=2, max_length=150)
    desde: date = Field(description="AAAA-MM-DD")
    hasta: date = Field(description="AAAA-MM-DD, máximo 14 días después de 'desde'")
    profesional: str | None = Field(default=None, max_length=200)
    franja: Literal["manana", "tarde"] | None = Field(default=None, description="SOLO si el paciente pidió expresamente mañana o tarde; si no, omítela")


def consultar_disponibilidad(ctx: ContextoHerramientas, a: ArgsDisponibilidad) -> dict:
    esp = _especialidad(ctx, a.especialidad)
    prof_id = _profesional(ctx, a.profesional).id if a.profesional else None
    hoy = politica_service.hoy_local()
    desde = max(a.desde, hoy)
    hasta = min(a.hasta, desde + timedelta(days=MAX_DIAS_CONSULTA))
    if hasta < desde:
        raise ErrorNegocio("rango_invalido", "Las fechas ya pasaron o el rango es inválido.", 422)
    libres = agenda_service.disponibilidad(ctx.db, desde, hasta, especialidad_id=esp.id, profesional_id=prof_id)
    if a.franja:
        libres = [s for s in libres if (s.inicio.astimezone(_tz()).hour < 12) == (a.franja == "manana")]
    if not libres:
        return {"especialidad": esp.nombre, "total_cupos": 0, "opciones": [],
                "nota": "No hay cupos libres en ese rango. Ofrezca otras fechas o anotar al paciente en lista de espera (transferir a una persona)."}
    por_dia: dict[date, list] = {}
    for s in libres:
        por_dia.setdefault(s.inicio.astimezone(_tz()).date(), []).append(s)
    elegidos = []
    for dia in sorted(por_dia):  # hasta 3 por día, repartidos en la jornada (primero, medio, último) para que haya variedad de horas
        dia_slots = por_dia[dia]
        for i in sorted({0, len(dia_slots) // 2, len(dia_slots) - 1}):
            if len(elegidos) < MAX_CUPOS_OFRECIDOS:
                elegidos.append(dia_slots[i])
    opciones = []
    ctx.ultima_oferta = []
    for s in elegidos:
        ctx.ultimo_cupo += 1
        ctx.cupos[ctx.ultimo_cupo] = CupoOfrecido(s.profesional_id, s.especialidad_id, s.inicio, s.fin, s.profesional, s.sede, s.consultorio, texto_fecha(s.inicio))
        ctx.ultima_oferta.append(ctx.ultimo_cupo)
        opciones.append({"opcion": ctx.ultimo_cupo, "cuando": texto_fecha(s.inicio), "profesional": s.profesional, "sede": s.sede})
    return {"especialidad": esp.nombre, "total_cupos": len(libres), "opciones": opciones,
            "nota": "Ofrezca los horarios diciendo solo la hora (p. ej. «a las 8:20»), no el número de opción; el número solo sirve para agendar." +
                    (" Hay más horarios disponibles; el paciente puede pedir otro día." if len(libres) > len(elegidos) else "")}


def _cupo(ctx, numero: int) -> CupoOfrecido:
    c = ctx.cupos.get(numero)
    if c is None:
        raise ErrorNegocio("cupo_desconocido", "Ese número de cupo no fue ofrecido en esta conversación. Consulte la disponibilidad de nuevo.", 422)
    return c


class ArgsAgendar(_Args):
    cupo: int = Field(ge=1, description="el número de la opción que eligió el paciente (campo 'opcion' de consultar_disponibilidad)")


def resumen_agendar(ctx, a: ArgsAgendar) -> str:
    c = _cupo(ctx, a.cupo)
    return f"Agendar una cita de {_nombre_especialidad(ctx, c.especialidad_id)} con {c.profesional} el {c.texto}, en {c.sede}"


def _nombre_especialidad(ctx, esp_id: int) -> str:
    return ctx.db.get(Especialidad, esp_id).nombre


def agendar_cita(ctx: ContextoHerramientas, a: ArgsAgendar) -> dict:
    c = _cupo(ctx, a.cupo)
    cita = cita_service.agendar(ctx.db, paciente_id=ctx.paciente_id, profesional_id=c.profesional_id, especialidad_id=c.especialidad_id, inicio=c.inicio, actor=cita_service.AGENTE,
                                canal=CanalCita.VOZ if ctx.canal == CanalConversacion.VOZ else CanalCita.AGENTE, conversacion_id=ctx.conversacion_id)
    return {"agendada": True, "codigo_reserva": cita.codigo_reserva, "cuando": c.texto, "profesional": c.profesional, "sede": c.sede, "consultorio": c.consultorio,
            "nota": "Dicte el código de reserva letra por letra y recuerde que puede cancelarla o reprogramarla con ese código."}


class ArgsCitas(_Args):
    incluir_pasadas: bool = False


def consultar_citas(ctx: ContextoHerramientas, a: ArgsCitas) -> dict:
    ahora = datetime.now(timezone.utc)
    filas = paciente_repository.citas_de(ctx.db, ctx.paciente_id, limite=30, institucion_id=ctx.institucion_id)
    citas = [{"codigo_reserva": f.codigo_reserva, "cuando": texto_fecha(f.inicio), "especialidad": f.especialidad, "profesional": f.profesional, "sede": f.sede, "estado": f.estado.value}
             for f in reversed(filas) if a.incluir_pasadas or (f.inicio > ahora and f.estado in cita_service.ACTIVAS)]
    return {"citas": citas[:10]} if citas else {"citas": [], "nota": "El paciente no tiene citas" + ("" if a.incluir_pasadas else " próximas") + "."}


def _cita_propia(ctx, codigo: str):
    c = cita_service.obtener_por_codigo(ctx.db, codigo, solo_paciente_id=ctx.paciente_id)
    if ctx.institucion_id is not None and ctx.db.scalar(select(Sede.institucion_id).where(Sede.id == c.sede_id)) not in (None, ctx.institucion_id):
        raise ErrorNegocio("cita_no_encontrada", "No se encontró la cita.", 404)  # es de otra institución: desde aquí no existe
    return c


class ArgsCancelar(_Args):
    codigo_reserva: str = Field(min_length=4, max_length=12)
    motivo: str | None = Field(default=None, max_length=250)


def resumen_cancelar(ctx, a: ArgsCancelar) -> str:
    c = _cita_propia(ctx, a.codigo_reserva)
    return f"Cancelar la cita {c.codigo_reserva} del {texto_fecha(c.inicio)}"


def cancelar_cita(ctx: ContextoHerramientas, a: ArgsCancelar) -> dict:
    c = _cita_propia(ctx, a.codigo_reserva)
    cita_service.cancelar(ctx.db, c.id, a.motivo, cita_service.AGENTE, solo_paciente_id=ctx.paciente_id)
    return {"cancelada": True, "codigo_reserva": c.codigo_reserva}


class ArgsReprogramar(_Args):
    codigo_reserva: str = Field(min_length=4, max_length=12)
    nuevo_cupo: int = Field(ge=1, description="el número de la opción elegida (campo 'opcion' de consultar_disponibilidad)")


def resumen_reprogramar(ctx, a: ArgsReprogramar) -> str:
    c, n = _cita_propia(ctx, a.codigo_reserva), _cupo(ctx, a.nuevo_cupo)
    return f"Mover la cita {c.codigo_reserva} del {texto_fecha(c.inicio)} al {n.texto} con {n.profesional}"


def reprogramar_cita(ctx: ContextoHerramientas, a: ArgsReprogramar) -> dict:
    c, n = _cita_propia(ctx, a.codigo_reserva), _cupo(ctx, a.nuevo_cupo)
    nueva = cita_service.reprogramar(ctx.db, c.id, n.inicio, cita_service.AGENTE, profesional_id=n.profesional_id, solo_paciente_id=ctx.paciente_id)
    return {"reprogramada": True, "codigo_reserva": nueva.codigo_reserva, "cuando": n.texto, "profesional": n.profesional, "sede": n.sede,
            "nota": "La cita quedó programada y debe confirmarse de nuevo."}


# ======================= solicitudes y transferencia =======================
class ArgsSolicitud(_Args):
    tipo: Literal["QUEJA", "RECLAMO", "SOLICITUD", "SUGERENCIA"]
    descripcion: str = Field(min_length=10, max_length=1000, description="lo que el paciente quiere dejar registrado, en sus palabras")


def crear_solicitud(ctx: ContextoHerramientas, a: ArgsSolicitud) -> dict:
    s = Solicitud(tipo=TipoSolicitud(a.tipo), asunto=a.descripcion[:120], descripcion=a.descripcion, paciente_id=ctx.paciente_id, conversacion_id=ctx.conversacion_id)
    ctx.db.add(s)
    ctx.db.flush()
    auditoria.registrar(ctx.db, accion="solicitud_creada", actor="agente", entidad="solicitud", entidad_id=s.id, detalle={"tipo": a.tipo})
    ctx.db.commit()
    return {"registrada": True, "numero": s.id, "nota": "Una persona de la institución la revisará."}


class ArgsTransferir(_Args):
    motivo: str = Field(min_length=3, max_length=500, description="por qué el paciente necesita a una persona")


def transferir_a_funcionario(ctx: ContextoHerramientas, a: ArgsTransferir) -> dict:
    s = Solicitud(tipo=TipoSolicitud.TRANSFERENCIA, asunto="Atención por una persona", descripcion=a.motivo, paciente_id=ctx.paciente_id, conversacion_id=ctx.conversacion_id,
                  estado=EstadoSolicitud.ABIERTA)
    ctx.db.add(s)
    ctx.db.flush()
    auditoria.registrar(ctx.db, accion="transferencia_solicitada", actor="agente", entidad="solicitud", entidad_id=s.id)
    ctx.db.commit()
    ctx.transferida = True
    return {"transferida": True, "numero": s.id,
            "nota": "Quedó registrada la solicitud para que una persona de la institución continúe la atención. No prometa un tiempo de espera ni diga que ya hay alguien en línea."}


# ======================= registro =======================
_D = {"buscar_paciente": ("Verifica la identidad del paciente con su documento y fecha de nacimiento. Úsala cuando el paciente ya dio los tres datos.", ArgsBuscarPaciente, buscar_paciente, {}),
      "registrar_paciente": ("Registra a un paciente NUEVO. Solo si no existe, tiene los datos completos y autorizó el tratamiento de sus datos.", ArgsRegistrarPaciente, registrar_paciente,
                             {"requiere_confirmacion": True, "resumen": resumen_registrar}),
      "consultar_especialidades": ("Lista las especialidades que ofrece la institución.", ArgsVacios, consultar_especialidades, {}),
      "consultar_profesionales": ("Lista los profesionales de una especialidad.", ArgsProfesionales, consultar_profesionales, {}),
      "consultar_horarios": ("Horarios de atención semanales de una especialidad o profesional (no son cupos libres).", ArgsHorarios, consultar_horarios, {}),
      "consultar_disponibilidad": ("Cupos LIBRES para una especialidad entre dos fechas. Devuelve opciones numeradas para agendar.", ArgsDisponibilidad, consultar_disponibilidad, {}),
      "agendar_cita": ("Agenda la cita en un cupo ofrecido (por su número). Requiere identidad verificada.", ArgsAgendar, agendar_cita,
                       {"requiere_identidad": True, "requiere_confirmacion": True, "resumen": resumen_agendar}),
      "consultar_citas": ("Lista las citas del paciente verificado.", ArgsCitas, consultar_citas, {"requiere_identidad": True}),
      "reprogramar_cita": ("Mueve una cita del paciente a otro cupo ofrecido. Requiere identidad verificada.", ArgsReprogramar, reprogramar_cita,
                           {"requiere_identidad": True, "requiere_confirmacion": True, "resumen": resumen_reprogramar}),
      "cancelar_cita": ("Cancela una cita del paciente por su código de reserva. El motivo es opcional: no lo pidas. Requiere identidad verificada.", ArgsCancelar, cancelar_cita,
                        {"requiere_identidad": True, "requiere_confirmacion": True, "resumen": resumen_cancelar}),
      "consultar_requisitos": ("Requisitos administrativos registrados para una especialidad (o generales).", ArgsRequisitos, consultar_requisitos, {}),
      "consultar_politicas": ("Política vigente de cancelación, reprogramación o inasistencia, tal como la registró la institución.", ArgsPoliticas, consultar_politicas, {}),
      "consultar_inasistencias": ("Cuántas inasistencias verificadas tiene el paciente y qué dice la política. Requiere identidad verificada.", ArgsVacios, consultar_inasistencias, {"requiere_identidad": True}),
      "crear_solicitud": ("Registra una queja, reclamo, solicitud o sugerencia del paciente.", ArgsSolicitud, crear_solicitud, {}),
      "transferir_a_funcionario": ("Pasa la atención a una persona de la institución (cuando el paciente lo pide o no puedes resolver).", ArgsTransferir, transferir_a_funcionario, {})}

for _nombre, (_desc, _modelo, _fn, _extra) in _D.items():
    REGISTRO.registrar(Herramienta(_nombre, _desc, _modelo, _fn, requiere_identidad=_extra.get("requiere_identidad", False),
                                   requiere_confirmacion=_extra.get("requiere_confirmacion", False), resumen=_extra.get("resumen")))

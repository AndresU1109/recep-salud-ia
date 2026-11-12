"""Datos DEMO COMPLETOS y 100 % FICTICIOS para ver el sistema funcionando: sedes, especialidades, profesionales, horarios, pacientes,
citas de la semana en curso, lista de espera, solicitudes, políticas, preguntas frecuentes, documentos para el asistente y usuarios de cada rol.

- Todo lo institucional lleva «demo» o «ficticio/a»; los documentos de los pacientes son 9000000001… y los correos @example.com (dominio reservado).
- Es idempotente: se puede correr varias veces (no duplica). Las citas de la semana se crean una sola vez (marca «[demo]»).
- NUNCA en producción. Uso:  python -m scripts.seed_demo_completo
"""
import random
import secrets
import string
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.database.session import get_session_factory
from app.models import (Cita, ConsentimientoDatos, Consultorio, Documento, Especialidad, ExcepcionAgenda, HorarioProfesional, Institucion, ListaEspera, Paciente, PoliticaInstitucional,
                        PreguntaFrecuente, Profesional, RegistroInasistencia, Requisito, Sede, Servicio, Solicitud, Usuario)
from app.models.enums import (AccionCita, ActorTipo, CanalCita, CategoriaDocumento, EstadoCita, EstadoDocumento, EstadoListaEspera, EstadoSolicitud, Rol, TipoDocumento,
                              TipoPolitica, TipoSolicitud)
from app.schemas.agenda import EstadoSlot
from app.schemas.paciente import ConsentimientoIn
from app.security import crypto
from app.services import agenda_service, cita_service, documento_service, faq_service, paciente_service, politica_service, usuario_service
from scripts.seed_demo import _obtener_o_crear, sembrar as sembrar_base

MARCA = "[demo]"

SEDES = {
    "Sede Demo Centro": {"ciudad": "Ciudad Demo", "direccion": "Calle Ficticia 10 # 5-20", "telefono": "601 000 0001"},
    "Sede Demo Norte": {"ciudad": "Ciudad Demo", "direccion": "Avenida Imaginaria 80 # 45-12", "telefono": "601 000 0002"},
}
CONSULTORIOS = {"Sede Demo Centro": [("Consultorio 101", "1"), ("Consultorio 102", "1"), ("Consultorio 103", "2"), ("Consultorio 104", "2")],
                "Sede Demo Norte": [("Consultorio 201", "2"), ("Consultorio 202", "2"), ("Consultorio 203", "3")]}
ESPECIALIDADES = {
    "Medicina General (demo)": ("Atención inicial y seguimiento de las consultas más frecuentes.", [("Consulta de primera vez", 20), ("Consulta de control", 15)]),
    "Pediatría (demo)": ("Atención de niños y adolescentes.", [("Consulta pediátrica", 30)]),
    "Ginecología y Obstetricia (demo)": ("Salud de la mujer y control prenatal.", [("Consulta ginecológica", 30), ("Control prenatal", 30)]),
    "Odontología (demo)": ("Salud oral general.", [("Valoración odontológica", 30), ("Higiene oral", 30)]),
    "Dermatología (demo)": ("Cuidado de la piel, el cabello y las uñas.", [("Consulta dermatológica", 20)]),
    "Psicología (demo)": ("Acompañamiento psicológico.", [("Sesión de psicología", 45)]),
    "Ortopedia (demo)": ("Huesos, articulaciones y músculos.", [("Consulta de ortopedia", 20)]),
}
# nombre, registro, especialidad, sede, consultorio, [(días 0=lunes, desde, hasta, minutos)]
PROFESIONALES = [
    ("Dra. Prueba Uno (ficticia)", "RM-DEMO-001", "Medicina General (demo)", "Sede Demo Centro", "Consultorio 101", [([0, 1, 2, 3, 4], time(8), time(12), 20)]),
    ("Dr. Prueba Dos (ficticio)", "RM-DEMO-002", "Pediatría (demo)", "Sede Demo Centro", "Consultorio 102", [([0, 1, 2, 3, 4], time(8), time(12), 20)]),
    ("Dr. Andrés Salcedo (ficticio)", "RM-DEMO-003", "Medicina General (demo)", "Sede Demo Centro", "Consultorio 103", [([0, 1, 2, 3, 4], time(14), time(18), 20)]),
    ("Dra. Marcela Quintero (ficticia)", "RM-DEMO-004", "Ginecología y Obstetricia (demo)", "Sede Demo Centro", "Consultorio 104", [([1, 3, 4], time(8), time(13), 30)]),
    ("Dra. Natalia Ospina (ficticia)", "RM-DEMO-005", "Ortopedia (demo)", "Sede Demo Centro", "Consultorio 101", [([0, 2, 3], time(14), time(18), 20)]),
    ("Dr. Julián Paredes (ficticio)", "RO-DEMO-006", "Odontología (demo)", "Sede Demo Norte", "Consultorio 201", [([0, 1, 2, 3, 4], time(8), time(12), 30), ([0, 1, 2, 3, 4], time(14), time(17), 30)]),
    ("Dra. Sofía Londoño (ficticia)", "RM-DEMO-007", "Dermatología (demo)", "Sede Demo Norte", "Consultorio 202", [([0, 2, 4], time(9), time(13), 20)]),
    ("Dra. Paula Herrera (ficticia)", "RM-DEMO-008", "Pediatría (demo)", "Sede Demo Norte", "Consultorio 203", [([0, 1, 2, 3, 4], time(8), time(12), 30)]),
    ("Dr. Camilo Restrepo (ficticio)", "RP-DEMO-009", "Psicología (demo)", "Sede Demo Norte", "Consultorio 203", [([0, 1, 2, 3], time(14), time(18), 45), ([5], time(8), time(12), 45)]),
]
REQUISITOS = {
    "Medicina General (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad del paciente."), ("Carné de la aseguradora", "Si tiene EPS o medicina prepagada, lleve el carné o el número de afiliación."),
                                ("Resultados anteriores", "Si tiene exámenes o fórmulas recientes relacionados con la consulta, llévelos.")],
    "Pediatría (demo)": [("Documento del menor y del acompañante", "Registro civil o tarjeta de identidad del menor y documento del adulto que lo acompaña."), ("Carné de vacunación", "Si lo tiene, llévelo.")],
    "Ginecología y Obstetricia (demo)": [("Documento de identidad", "Cédula de la paciente."), ("Carné prenatal", "Solo para los controles prenatales.")],
    "Odontología (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad.")],
    "Dermatología (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad."), ("Remisión", "Si la aseguradora la exige, lleve la remisión del médico general.")],
    "Psicología (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad. Los menores asisten con un adulto responsable.")],
    "Ortopedia (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad."), ("Imágenes diagnósticas", "Si tiene radiografías o resonancias, llévelas.")],
}
EPS = ["EPS Demo Salud", "EPS Demo Vida", None, "Medicina Prepagada Demo"]
PACIENTES = [  # (tipo, documento, nombres, apellidos, nacimiento, correo?, EPS#)
    ("CC", "9000000001", "Laura Valentina", "Mendoza Demo", date(1991, 3, 14), True, 0), ("CC", "9000000002", "Carlos Andrés", "Rojas Demo", date(1985, 7, 2), True, 1),
    ("CC", "9000000003", "María Fernanda", "Gómez Demo", date(1978, 11, 23), True, 0), ("CC", "9000000004", "Juan David", "Pérez Demo", date(1994, 1, 30), True, 2),
    ("CC", "9000000005", "Ana Sofía", "Castaño Demo", date(2001, 5, 9), True, 3), ("CC", "9000000006", "Luis Eduardo", "Moreno Demo", date(1969, 9, 17), False, 1),
    ("CC", "9000000007", "Diana Carolina", "Vargas Demo", date(1988, 12, 5), True, 0), ("CC", "9000000008", "Jorge Iván", "Ramírez Demo", date(1973, 4, 28), True, 1),
    ("CC", "9000000009", "Paola Andrea", "Silva Demo", date(1996, 8, 19), True, 2), ("CC", "9000000010", "Sebastián", "Torres Demo", date(1999, 2, 11), True, 0),
    ("CC", "9000000011", "Gloria Patricia", "Herrera Demo", date(1957, 6, 3), False, 1), ("CC", "9000000012", "Andrés Felipe", "Ortiz Demo", date(1982, 10, 25), True, 3),
    ("TI", "9100000001", "Mariana", "Cardona Demo", date(2012, 9, 8), True, 0), ("TI", "9100000002", "Samuel", "Quiroga Demo", date(2010, 1, 21), True, 1),
    ("RC", "9200000001", "Isabela", "Naranjo Demo", date(2020, 4, 15), True, 0), ("CE", "9300000001", "Camille", "Dubois Demo", date(1990, 6, 27), True, 2),
    ("CC", "9183746502", "Valentina", "Cruz Demo", date(1993, 8, 21), True, 0),  # sin ceros repetidos: sirve para probar el dictado por voz
]
POLITICAS = [
    (TipoPolitica.CANCELACION, "Cancelación de citas (demo)", {"horas_minimas_cancelar": 4}, "Para cancelar una cita se necesitan al menos 4 horas de anticipación."),
    (TipoPolitica.REPROGRAMACION, "Reprogramación de citas (demo)", {"horas_minimas_reprogramar": 4, "maximo_reprogramaciones": 2}, "Una cita se puede reprogramar hasta 2 veces y con al menos 4 horas de anticipación."),
    (TipoPolitica.INASISTENCIA, "Inasistencias (demo)", {"inasistencias_para_aviso": 3}, "Si no puede asistir, avise con tiempo para que otra persona use el cupo."),
]
FAQS = [
    ("Agendar", "¿Cómo agendo una cita?", "Puede pedirla al asistente virtual, por texto o por voz, o en la recepción de cualquiera de las sedes. Necesita su documento de identidad y su fecha de nacimiento para verificar su identidad."),
    ("Agendar", "¿Qué debo llevar a mi cita?", "Lleve su documento de identidad y, si tiene, el carné de su aseguradora y los resultados de exámenes recientes. Cada especialidad puede pedir algo adicional: pregúntele al asistente."),
    ("Horarios", "¿Atienden los sábados?", "Los sábados atiende solamente Psicología en la Sede Demo Norte, de 8:00 a. m. a 12:00 m. El resto de especialidades atiende de lunes a viernes."),
    ("Horarios", "¿Cuánto dura una consulta?", "Depende de la especialidad: entre 15 y 45 minutos. Medicina General 20, Pediatría 30, Ginecología 30, Odontología 30, Dermatología 20, Ortopedia 20 y Psicología 45."),
    ("Sedes", "¿Dónde están las sedes?", "Sede Demo Centro: Calle Ficticia 10 # 5-20. Sede Demo Norte: Avenida Imaginaria 80 # 45-12. Ambas en Ciudad Demo."),
    ("Cambios", "¿Cómo cancelo o cambio mi cita?", "Pídaselo al asistente o llame a la recepción. Se necesitan al menos 4 horas de anticipación, y una cita se puede reprogramar hasta 2 veces."),
    ("Cambios", "¿Qué pasa si no puedo asistir?", "Avise con tiempo para que otra persona use el cupo. Las inasistencias las revisa una persona del equipo; ninguna se sanciona automáticamente."),
    ("Datos", "¿Qué hacen con mis datos personales?", "Solo se usan para gestionar sus citas y avisarle sobre ellas. Para registrarlo se le pide su autorización de tratamiento de datos, y usted puede decidir si se guarda la transcripción de sus conversaciones."),
    ("Asistente", "¿El asistente me da diagnósticos?", "No. El asistente solo ayuda con citas e información de la institución. Ante una posible emergencia le indicará llamar a la línea 123 o acudir a urgencias."),
    ("Asistente", "¿Cómo hablo con una persona?", "Dígale al asistente «quiero hablar con una persona» y dejará registrada su solicitud para que alguien del equipo lo contacte."),
]
DOCUMENTOS = [
    ("Guía de la primera consulta (demo)", CategoriaDocumento.REQUISITOS, "Dirección médica (demo)", """Guía de la primera consulta

Antes de la cita: tenga a la mano su documento de identidad y el carné de su aseguradora, si lo tiene. Si ya tiene exámenes o fórmulas recientes relacionados con el motivo de la consulta, llévelos.

Llegada: se recomienda llegar 15 minutos antes de la hora de la cita para la verificación de su identidad en recepción.

Menores de edad: deben asistir con un adulto responsable que presente su propio documento de identidad.

Durante la consulta: el profesional le explicará el procedimiento. La institución no entrega orientación médica por el asistente virtual."""),
    ("Sedes, horarios y cómo llegar (demo)", CategoriaDocumento.SERVICIOS, "Administración (demo)", """Sedes y horarios

Sede Demo Centro: Calle Ficticia 10 # 5-20, Ciudad Demo. Atiende Medicina General, Pediatría, Ginecología y Obstetricia y Ortopedia, de lunes a viernes.

Sede Demo Norte: Avenida Imaginaria 80 # 45-12, Ciudad Demo. Atiende Odontología, Dermatología, Pediatría y Psicología. Psicología también atiende los sábados de 8:00 a. m. a 12:00 m.

La recepción de ambas sedes abre de lunes a viernes de 7:30 a. m. a 6:30 p. m."""),
    ("Reglamento de atención al paciente (demo)", CategoriaDocumento.REGLAMENTO, "Gerencia (demo)", """Reglamento de atención al paciente

Las citas se asignan por orden de solicitud y se confirman con un código de reserva de 8 caracteres.

Cancelaciones: se aceptan hasta 4 horas antes de la cita. Reprogramaciones: hasta 2 por cita y con 4 horas de anticipación.

Inasistencias: una inasistencia solo se registra después de que una persona autorizada la verifica. La institución no aplica sanciones automáticas.

Lista de espera: si no hay cupo, el paciente puede quedar en lista de espera. Cuando se libera un cupo se avisa por orden de llegada; el aviso no reserva el cupo."""),
    ("Protocolo ante una posible emergencia (demo)", CategoriaDocumento.PROTOCOLO, "Dirección médica (demo)", """Protocolo ante una posible emergencia

El asistente virtual no atiende emergencias ni da orientación médica. Si una persona describe una situación que pone en riesgo su vida, el asistente le indica de inmediato llamar a la línea de emergencias 123 o acudir al servicio de urgencias más cercano y no continúa con trámites.

La recepción debe avisar al personal asistencial si una persona llega con signos de urgencia."""),
]
SOLICITUDES = [
    (TipoSolicitud.QUEJA, "Demora en la atención en recepción (demo)", "La persona informa que esperó más de 30 minutos antes de ser atendida en la Sede Demo Centro.", EstadoSolicitud.ABIERTA),
    (TipoSolicitud.SUGERENCIA, "Más horarios en la tarde (demo)", "Sugiere abrir más cupos de Medicina General después de las 5:00 p. m.", EstadoSolicitud.EN_PROCESO),
    (TipoSolicitud.TRANSFERENCIA, "Pide hablar con una persona (demo)", "Solicitó hablar con una persona desde el asistente para revisar una cita.", EstadoSolicitud.ABIERTA),
    (TipoSolicitud.RECLAMO, "Cobro duplicado de un copago (demo)", "Reporta que se le cobró dos veces el mismo copago. Se revisó con facturación.", EstadoSolicitud.RESUELTA),
]
ROLES_DEMO = [("demo.recepcion@example.com", "Recepción Demo", Rol.RECEPCIONISTA), ("demo.coordinacion@example.com", "Coordinación Demo", Rol.COORDINADOR),
              ("demo.profesional@example.com", "Profesional Demo", Rol.PROFESIONAL_SALUD), ("demo.consulta@example.com", "Consulta Demo", Rol.CONSULTA)]


def _clave_aleatoria() -> str:
    alfabeto = string.ascii_letters + string.digits
    while True:
        c = "".join(secrets.choice(alfabeto) for _ in range(14))
        if any(x.islower() for x in c) and any(x.isupper() for x in c) and any(x.isdigit() for x in c):
            return c


def _oc(db, modelo, filtro: dict, extra: dict | None = None, inst=None):
    """Como _obtener_o_crear, pero dentro de una institución (los nombres pueden repetirse entre instituciones)."""
    if inst is not None and hasattr(modelo, "institucion_id"):
        filtro = {**filtro, "institucion_id": inst.id}
    return _obtener_o_crear(db, modelo, filtro, extra)


def _catalogo(db, cfg: dict, inst) -> dict:
    sedes = {n: _oc(db, Sede, {"nombre": n}, d, inst) for n, d in cfg["sedes"].items()}
    for n, d in cfg["sedes"].items():  # completa las sedes que ya existían (la base solo tenía ciudad)
        for campo, valor in d.items():
            if not getattr(sedes[n], campo):
                setattr(sedes[n], campo, valor)
    cons = {(s, n): _obtener_o_crear(db, Consultorio, {"sede_id": sedes[s].id, "nombre": n}, {"piso": piso}) for s, lista in cfg["consultorios"].items() for n, piso in lista}
    esp = {}
    for nombre, (desc, servicios) in cfg["especialidades"].items():
        esp[nombre] = _oc(db, Especialidad, {"nombre": nombre}, {"descripcion": desc}, inst)
        for s_nombre, minutos in servicios:
            _obtener_o_crear(db, Servicio, {"especialidad_id": esp[nombre].id, "nombre": s_nombre}, {"duracion_minutos": minutos})
    desde = politica_service.hoy_local() - timedelta(days=14)
    for nombre, registro, e, s, c, jornadas in cfg["profesionales"]:
        prof = _oc(db, Profesional, {"nombre_completo": nombre}, {"registro_profesional": registro}, inst)
        if esp[e] not in prof.especialidades:
            prof.especialidades.append(esp[e])
        if not db.execute(select(HorarioProfesional.id).where(HorarioProfesional.profesional_id == prof.id, HorarioProfesional.especialidad_id == esp[e].id)).first():
            for dias, ini, fin, minutos in jornadas:
                for dia in dias:
                    db.add(HorarioProfesional(profesional_id=prof.id, especialidad_id=esp[e].id, sede_id=sedes[s].id, consultorio_id=cons[(s, c)].id, dia_semana=dia,
                                              hora_inicio=ini, hora_fin=fin, duracion_cita_min=minutos, vigente_desde=desde))
    for e, lista in cfg["requisitos"].items():
        for orden, (titulo, desc) in enumerate(lista):
            _obtener_o_crear(db, Requisito, {"especialidad_id": esp[e].id, "titulo": titulo}, {"descripcion": f"{desc} (demo)", "orden": orden})
    db.commit()
    return {"sedes": sedes, "esp": esp}


def _bloqueos(db, sedes, cfg: dict, inst) -> None:
    """Un mantenimiento la semana próxima y unas vacaciones de un profesional, para ver los cupos bloqueados."""
    hoy = politica_service.hoy_local()
    lunes = hoy - timedelta(days=hoy.weekday()) + timedelta(days=7)
    tz = agenda_service._tz()
    mant_ini = datetime.combine(lunes + timedelta(days=2), time(8), tzinfo=tz)
    _obtener_o_crear(db, ExcepcionAgenda, {"sede_id": sedes[cfg["bloqueo"]["sede"]].id, "inicio": mant_ini}, {"fin": mant_ini + timedelta(hours=4), "motivo": "Mantenimiento de la sede (demo)"})
    prof = _oc(db, Profesional, {"nombre_completo": cfg["bloqueo"]["profesional"]}, None, inst)
    vac_ini = datetime.combine(lunes + timedelta(days=7), time.min, tzinfo=tz)
    _obtener_o_crear(db, ExcepcionAgenda, {"profesional_id": prof.id, "inicio": vac_ini}, {"fin": vac_ini + timedelta(days=5), "motivo": "Vacaciones (demo)"})
    db.commit()


def _pacientes(db) -> list[Paciente]:
    lista = []
    for i, (tipo, doc, nombres, apellidos, nac, con_correo, eps) in enumerate(PACIENTES):
        existente = db.scalar(select(Paciente).where(Paciente.documento_hash == crypto.hash_documento(tipo, doc)))
        if existente is None:
            existente = paciente_service.registrar(db, None, {"tipo_documento": TipoDocumento(tipo), "numero_documento": doc, "nombres": nombres, "apellidos": apellidos, "fecha_nacimiento": nac,
                                                             "telefono": f"300 000 {i + 1:04d}", "email": f"paciente.demo{i + 1:02d}@example.com" if con_correo else None,
                                                             "entidad_aseguradora": EPS[eps]},
                                                   ConsentimientoIn(politica_version="demo-1", aceptado=True, autoriza_transcripcion=i % 3 != 2), canal="DEMO")
        lista.append(existente)
    lista[8].notificaciones_activas = False  # uno que no quiere avisos
    db.commit()
    return lista


def _semana(db, pacientes: list[Paciente], admin: Usuario, inst) -> dict:
    """Citas del lunes al sábado de ESTA semana: las ya pasadas con su desenlace; las futuras agendadas por el servicio (con todas sus reglas)."""
    sedes_ids = {s.id for s in db.scalars(select(Sede).where(Sede.institucion_id == inst.id))}
    if db.scalar(select(Cita.id).where(Cita.notas_administrativas == MARCA, Cita.sede_id.in_(sedes_ids)).limit(1)):
        return {"ya_existian": True}
    rng = random.Random(20261103 + inst.id)
    actor = cita_service.Actor(ActorTipo.USUARIO, admin.id, True)
    hoy = politica_service.hoy_local()
    lunes = hoy - timedelta(days=hoy.weekday())
    slots = [s for s in agenda_service.generar_slots(db, lunes, lunes + timedelta(days=5)) if s.estado in (EstadoSlot.LIBRE, EstadoSlot.PASADO) and s.sede_id in sedes_ids]
    ahora = datetime.now(timezone.utc)
    canales = [CanalCita.WEB, CanalCita.VOZ, CanalCita.AGENTE, CanalCita.TELEFONO, CanalCita.PRESENCIAL]
    cuenta = {"futuras": 0, "atendidas": 0, "canceladas": 0, "inasistencias": 0, "confirmadas": 0}
    verificada_ya = False
    for s in slots:
        if rng.random() > 0.38:
            continue
        for p in rng.sample(pacientes, 5):  # si un paciente ya tiene otra cita a esa hora, se prueba con otro
            canal = rng.choice(canales)
            try:
                if s.inicio > ahora:
                    c = cita_service.agendar(db, paciente_id=p.id, profesional_id=s.profesional_id, especialidad_id=s.especialidad_id, inicio=s.inicio, actor=actor, canal=canal, sede_id=s.sede_id, notas=MARCA)
                    cuenta["futuras"] += 1
                    azar = rng.random()
                    if azar < 0.35:
                        cita_service.confirmar(db, c.id, actor)
                        cuenta["confirmadas"] += 1
                    elif azar < 0.43:
                        cita_service.cancelar(db, c.id, "Cancelada por el paciente (demo)", actor)
                        cuenta["canceladas"] += 1
                else:
                    c = Cita(codigo_reserva=cita_service._codigo(), paciente_id=p.id, profesional_id=s.profesional_id, especialidad_id=s.especialidad_id, sede_id=s.sede_id, consultorio_id=s.consultorio_id,
                             inicio=s.inicio, fin=s.fin, estado=EstadoCita.PROGRAMADA, canal=canal, notas_administrativas=MARCA, creada_por_usuario_id=admin.id)
                    with db.begin_nested():
                        db.add(c)
                        db.flush()
                    cita_service._historial(db, c, AccionCita.CREADA, None, actor, {"canal": canal.value})
                    azar = rng.random()
                    anterior = c.estado
                    if azar < 0.70:
                        c.estado = EstadoCita.ATENDIDA
                        cita_service._historial(db, c, AccionCita.ATENDIDA, anterior, actor)
                        cuenta["atendidas"] += 1
                    elif azar < 0.85:
                        c.estado, c.cancelada_en, c.motivo_cancelacion = EstadoCita.CANCELADA, s.inicio - timedelta(days=1), "Cancelada por el paciente (demo)"
                        cita_service._historial(db, c, AccionCita.CANCELADA, anterior, actor, {"motivo": c.motivo_cancelacion})
                        cuenta["canceladas"] += 1
                    else:
                        c.estado = EstadoCita.INASISTENCIA
                        cita_service._historial(db, c, AccionCita.INASISTENCIA, anterior, actor)
                        db.add(RegistroInasistencia(cita_id=c.id, paciente_id=p.id, verificada=not verificada_ya, verificada_por=None if verificada_ya else admin.id,
                                                    verificada_en=None if verificada_ya else ahora, observacion="Registro de la demo"))
                        verificada_ya = True  # la primera queda verificada y las demás pendientes: así se ve la pantalla de Inasistencias con ambos casos
                        cuenta["inasistencias"] += 1
                    db.commit()
                break
            except (ErrorNegocio, IntegrityError):
                db.rollback()
    return cuenta


def _espera_y_solicitudes(db, pacientes, esp) -> None:
    hoy = politica_service.hoy_local()
    for idx, e in ((1, "Dermatología (demo)"), (4, "Ortopedia (demo)"), (6, "Pediatría (demo)")):
        if not db.scalar(select(ListaEspera.id).where(ListaEspera.paciente_id == pacientes[idx].id, ListaEspera.especialidad_id == esp[e].id)):
            db.add(ListaEspera(paciente_id=pacientes[idx].id, especialidad_id=esp[e].id, desde=hoy, hasta=hoy + timedelta(days=14), estado=EstadoListaEspera.ACTIVA))
    for i, (tipo, asunto, desc, estado) in enumerate(SOLICITUDES):
        if not db.scalar(select(Solicitud.id).where(Solicitud.asunto == asunto)):
            db.add(Solicitud(tipo=tipo, asunto=asunto, descripcion=desc, estado=estado, paciente_id=pacientes[i].id,
                             resolucion="Resuelto con facturación (demo)." if estado == EstadoSolicitud.RESUELTA else None,
                             cerrada_en=datetime.now(timezone.utc) if estado == EstadoSolicitud.RESUELTA else None))
    db.commit()


def _politicas(db, admin, cfg: dict, inst) -> None:
    for tipo, titulo, parametros, mensaje in cfg["politicas"]:
        if not db.scalar(select(PoliticaInstitucional.id).where(PoliticaInstitucional.titulo == titulo, PoliticaInstitucional.institucion_id == inst.id)):
            politica_service.crear(db, admin, {"tipo": TipoPolitica(tipo), "titulo": titulo, "parametros": parametros, "mensaje_paciente": mensaje, "institucion_id": inst.id})


def _conocimiento(db, admin, cfg: dict, inst) -> list[str]:
    """FAQ y documentos de la institución. Se indexan con el modelo de embeddings; si Ollama está apagado quedan por indexar (se avisa)."""
    avisos = []
    for orden, (categoria, pregunta, respuesta) in enumerate(cfg["faqs"]):
        if not db.scalar(select(PreguntaFrecuente.id).where(PreguntaFrecuente.pregunta == pregunta, PreguntaFrecuente.institucion_id == inst.id)):
            faq_service.crear(db, admin, {"pregunta": pregunta, "respuesta": respuesta, "categoria": categoria, "orden": orden, "institucion_id": inst.id})
    for titulo, categoria, fuente, texto in cfg["documentos"]:
        if db.scalar(select(Documento.id).where(Documento.titulo == titulo, Documento.version == "1.0", Documento.institucion_id == inst.id)):
            continue
        doc, _ = documento_service.crear(db, admin, {"titulo": titulo, "categoria": CategoriaDocumento(categoria), "fuente": fuente, "version": "1.0", "institucion_id": inst.id,
                                                     "vigente_desde": politica_service.hoy_local() - timedelta(days=30)}, texto=texto)
        _, a = documento_service.cambiar_estado(db, admin, doc.id, EstadoDocumento.ACTIVO)
        avisos += a
    sin_indexar = db.scalar(select(Documento.id).where(Documento.indexado_en.is_(None), Documento.estado == EstadoDocumento.ACTIVO).limit(1))
    if sin_indexar:
        avisos.append("Hay documentos activos sin indexar (¿Ollama apagado?). Enciéndelo y pulsa «Reindexar» en Conocimiento → Documentos.")
    return avisos


def _usuarios(db) -> list[tuple[str, str, str]]:
    nuevos = []
    for correo, nombre, rol in ROLES_DEMO:
        try:
            clave = _clave_aleatoria()
            usuario_service.crear(db, None, email=correo, nombre=nombre, rol=rol, clave=clave)
            nuevos.append((correo, rol.value, clave))
        except ErrorNegocio:
            db.rollback()  # ya existía
    return nuevos


LEGADO = {
    "nombre": "Clínica Santa Aurora (ficticia)", "tipo": "Clínica general", "ciudad": "Ciudad Demo",
    "descripcion": "Clínica de mediana complejidad con dos sedes: medicina general, pediatría, ginecología, odontología, dermatología, psicología y ortopedia.",
    "sedes": SEDES, "consultorios": CONSULTORIOS, "especialidades": ESPECIALIDADES, "profesionales": PROFESIONALES, "requisitos": REQUISITOS, "politicas": POLITICAS,
    "faqs": FAQS, "documentos": DOCUMENTOS, "bloqueo": {"sede": "Sede Demo Norte", "profesional": "Dra. Sofía Londoño (ficticia)"},
}
NOMBRE_ANTIGUO = "Institución de Salud (configurar en el panel)"


def _institucion(db, cfg: dict, renombrar_desde: str | None = None) -> Institucion:
    """Crea la institución (o la encuentra). La del sistema de siempre, con su nombre de relleno, se RENOMBRA en vez de duplicarla."""
    inst = db.scalar(select(Institucion).where(Institucion.nombre == cfg["nombre"]))
    if inst is None and renombrar_desde:
        inst = db.scalar(select(Institucion).where(Institucion.nombre == renombrar_desde))
        if inst is not None:
            inst.nombre = cfg["nombre"]
    if inst is None:
        inst = Institucion(nombre=cfg["nombre"])
        db.add(inst)
    inst.tipo, inst.ciudad, inst.descripcion = cfg["tipo"], cfg["ciudad"], cfg["descripcion"]
    db.commit()
    return inst


def _adoptar_huerfanos(db, inst) -> None:
    """Lo que se creó antes de que existieran las instituciones (sin institución) pasa a la primera, para que no quede «compartido» con todas."""
    for modelo in (Sede, Especialidad, Profesional, Documento, PoliticaInstitucional, PreguntaFrecuente):
        db.query(modelo).filter(modelo.institucion_id.is_(None)).update({"institucion_id": inst.id})
    db.commit()


def sembrar_todo() -> None:
    if get_settings().app_env == "production":
        raise SystemExit("Los datos demo no se cargan en producción.")
    from scripts.instituciones_demo import NUEVAS

    sembrar_base()
    with get_session_factory()() as db:
        admin = db.scalars(select(Usuario).where(Usuario.rol == Rol.ADMINISTRADOR, Usuario.activo).order_by(Usuario.id)).first()
        if admin is None:
            raise SystemExit("Primero crea un administrador:  python -m scripts.crear_admin --email tu@correo.co --nombre \"Tu Nombre\"")
        legado = _institucion(db, LEGADO, renombrar_desde=NOMBRE_ANTIGUO)
        _adoptar_huerfanos(db, legado)
        pacientes = _pacientes(db)
        resumen, avisos = {}, []
        for cfg in [LEGADO, *NUEVAS]:
            inst = legado if cfg is LEGADO else _institucion(db, cfg)
            cat = _catalogo(db, cfg, inst)
            _bloqueos(db, cat["sedes"], cfg, inst)
            _politicas(db, admin, cfg, inst)
            resumen[inst.nombre] = _semana(db, pacientes, admin, inst)
            if cfg is LEGADO:
                _espera_y_solicitudes(db, pacientes, cat["esp"])
            avisos += _conocimiento(db, admin, cfg, inst)
        credenciales = _usuarios(db)
        total = lambda m: db.query(m).count()  # noqa: E731
        print("\nDatos demo listos:")
        for nombre, modelo in (("instituciones", Institucion), ("sedes", Sede), ("consultorios", Consultorio), ("especialidades", Especialidad), ("profesionales", Profesional), ("horarios", HorarioProfesional),
                               ("pacientes", Paciente), ("citas", Cita), ("lista de espera", ListaEspera), ("solicitudes", Solicitud), ("políticas", PoliticaInstitucional),
                               ("preguntas frecuentes", PreguntaFrecuente), ("documentos", Documento)):
            print(f"  {nombre:22} {total(modelo)}")
        print("\n  Por institución (citas de la semana):")
        for nombre, r in resumen.items():
            print(f"    {nombre:48}", "ya existían (no se duplicaron)" if r.get("ya_existian") else r)
        for a in dict.fromkeys(avisos):
            print("  AVISO:", a)
        if credenciales:
            print("\nUsuarios demo (contraseña temporal; se pide cambiarla al entrar; se muestra UNA vez):")
            for correo, rol, clave in credenciales:
                print(f"  {correo:34} {rol:18} {clave}")


if __name__ == "__main__":
    sembrar_todo()

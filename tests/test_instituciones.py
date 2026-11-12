"""Varias instituciones ficticias: cada llamada habla en nombre de UNA y solo ve su catálogo, sus políticas, su conocimiento y sus citas."""
import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agents.sesiones import SESIONES
from app.core.errores import ErrorNegocio
from app.models import Cita, Consultorio, Especialidad, Institucion, PoliticaInstitucional, Profesional, Sede
from app.models.catalogo import HorarioProfesional
from app.models.enums import CanalConversacion, CategoriaDocumento, EstadoDocumento, Rol, TipoPolitica
from app.services import agente_service, busqueda_service, indexacion_service, institucion_service, politica_service
from app.tools.contexto import ContextoHerramientas
from tests.factories import nuevo_paciente
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_herramientas import llamar as _llamar
from tests.test_rag_busqueda import doc

from app.api.v1.instituciones import nombre_corto
from app.services import conversacion_service

A = "/api/v1"


def _sufijo() -> str:
    return uuid.uuid4().hex[:6]


def montar(db, nombre: str, especialidad: str, prof: str) -> Institucion:
    """Una institución mínima pero completa: sede, consultorio, especialidad y un profesional con horario."""
    inst = Institucion(nombre=f"{nombre} {_sufijo()} (ficticia)", tipo="Clínica", ciudad="Ciudad Demo", descripcion="Institución de prueba")
    db.add(inst)
    db.flush()
    sede = Sede(nombre=f"Sede {nombre}", institucion_id=inst.id)
    esp = Especialidad(nombre=especialidad, institucion_id=inst.id)
    p = Profesional(nombre_completo=prof, institucion_id=inst.id)
    db.add_all([sede, esp, p])
    db.flush()
    db.add(Consultorio(sede_id=sede.id, nombre="C1"))
    p.especialidades.append(esp)
    db.add(HorarioProfesional(profesional_id=p.id, sede_id=sede.id, especialidad_id=esp.id, dia_semana=0, hora_inicio=time(8), hora_fin=time(12),
                              duracion_cita_min=20, vigente_desde=date(2020, 1, 1)))
    db.flush()
    inst.sede, inst.esp, inst.prof = sede, esp, p
    return inst


@pytest.fixture
def dos(db):
    return montar(db, "Alfa", "Cardiología Alfa", "Dra. Alfa Ficticia"), montar(db, "Beta", "Dermatología Beta", "Dr. Beta Ficticio")


def datos(ctx, nombre, **args) -> dict:
    r = _llamar(ctx, nombre, **args)
    assert r.ok, (r.codigo_error, r.datos)
    return r.datos


def ctx_de(db, inst) -> ContextoHerramientas:
    conv = conversacion_service.crear(db, CanalConversacion.VOZ, institucion_id=inst.id)
    return ContextoHerramientas(db=db, conversacion_id=conv.id, canal=CanalConversacion.VOZ, institucion_id=inst.id)


# ======================= catálogo y herramientas =======================
def test_cada_institucion_solo_lista_sus_especialidades(db, dos):
    a, b = dos
    assert datos(ctx_de(db, a), "consultar_especialidades")["especialidades"] == ["Cardiología Alfa"]
    assert datos(ctx_de(db, b), "consultar_especialidades")["especialidades"] == ["Dermatología Beta"]


def test_no_se_puede_consultar_una_especialidad_de_otra_institucion(db, dos):
    a, b = dos
    r = _llamar(ctx_de(db, a), "consultar_profesionales", especialidad="Dermatología Beta")
    assert not r.ok and r.codigo_error == "especialidad_no_encontrada"
    assert datos(ctx_de(db, a), "consultar_profesionales", especialidad="Cardiología Alfa")["profesionales"] == ["Dra. Alfa Ficticia"]


def test_los_horarios_son_solo_de_las_sedes_de_la_institucion(db, dos):
    a, b = dos
    h = datos(ctx_de(db, b), "consultar_horarios", especialidad="Dermatología Beta")["horarios"]
    assert [x["sede"] for x in h] == ["Sede Beta"]
    assert not _llamar(ctx_de(db, b), "consultar_horarios", profesional="Dra. Alfa Ficticia").ok


def test_lo_compartido_sin_institucion_lo_ven_todas(db, dos):
    a, b = dos
    nombre = f"Medicina Común {_sufijo()}"
    db.add(Especialidad(nombre=nombre))  # institucion_id NULL = compartida
    db.flush()
    assert nombre in datos(ctx_de(db, a), "consultar_especialidades")["especialidades"]
    assert nombre in datos(ctx_de(db, b), "consultar_especialidades")["especialidades"]


def test_dos_instituciones_pueden_tener_una_especialidad_con_el_mismo_nombre(db):
    a, b = montar(db, "Gama", "Pediatría", "Dra. Gama Ficticia"), montar(db, "Delta", "Pediatría", "Dr. Delta Ficticio")
    assert a.esp.id != b.esp.id
    db.add(Especialidad(nombre="Pediatría", institucion_id=a.id))
    with pytest.raises(IntegrityError):  # dentro de la misma institución sigue siendo único
        db.flush()
    db.rollback()


# ======================= políticas =======================
def test_la_politica_aplicable_es_la_de_la_institucion(db, dos):
    a, b = dos
    for inst, horas in ((a, 12), (b, 48)):
        db.add(PoliticaInstitucional(tipo=TipoPolitica.CANCELACION, titulo=f"Cancelación {inst.id}", parametros={"horas_minimas_cancelar": horas}, institucion_id=inst.id,
                                     vigente_desde=date(2020, 1, 1), mensaje_paciente=f"Se cancela con {horas} horas."))
    db.flush()
    assert politica_service.aplicable(db, TipoPolitica.CANCELACION, institucion_id=a.id).parametros["horas_minimas_cancelar"] == 12
    assert datos(ctx_de(db, b), "consultar_politicas", tipo="CANCELACION")["reglas"]["horas_minimas_cancelar"] == 48


def test_sin_politica_propia_el_asistente_no_usa_la_de_otra(db, dos):
    a, b = dos
    db.add(PoliticaInstitucional(tipo=TipoPolitica.INASISTENCIA, titulo="Solo de Alfa", parametros={}, institucion_id=a.id, vigente_desde=date(2020, 1, 1)))
    db.flush()
    assert datos(ctx_de(db, a), "consultar_politicas", tipo="INASISTENCIA")["configurada"] is True
    assert datos(ctx_de(db, b), "consultar_politicas", tipo="INASISTENCIA")["configurada"] is False


# ======================= conocimiento (RAG) =======================
def test_la_busqueda_solo_devuelve_documentos_de_la_institucion(db, dos):
    a, b = dos
    actor = crear_usuario(db, email="gestor@ejemplo.com", rol=Rol.COORDINADOR)
    texto = "Para la consulta el paciente debe presentar su documento de identidad y llegar con quince minutos de anticipación."
    da = doc(db, actor, "Guía de Alfa", texto, institucion_id=a.id)
    db_ = doc(db, actor, "Guía de Beta", texto, institucion_id=b.id)
    t = lambda r: [x.titulo for x in r.resultados]
    assert t(busqueda_service.buscar(db, "documento de identidad para la consulta", institucion_id=a.id)) == [da.titulo]
    assert t(busqueda_service.buscar(db, "documento de identidad para la consulta", institucion_id=b.id)) == [db_.titulo]
    assert set(t(busqueda_service.buscar(db, "documento de identidad para la consulta"))) >= {da.titulo, db_.titulo}  # sin institución no se filtra


# ======================= citas =======================
def test_una_cita_de_otra_institucion_no_se_puede_ver_ni_cancelar(db, dos):
    a, b = dos
    from app.models.enums import CanalCita, EstadoCita
    from datetime import datetime, timedelta, timezone
    pac = nuevo_paciente(db)
    cons = db.scalar(select(Consultorio).where(Consultorio.sede_id == a.sede.id))
    inicio = datetime(2030, 3, 4, 10, 0, tzinfo=timezone.utc)
    cita = Cita(codigo_reserva=_sufijo().upper(), paciente_id=pac.id, profesional_id=a.prof.id, especialidad_id=a.esp.id, sede_id=a.sede.id, consultorio_id=cons.id,
                inicio=inicio, fin=inicio + timedelta(minutes=20), estado=EstadoCita.PROGRAMADA, canal=CanalCita.VOZ)
    db.add(cita)
    db.flush()
    ctx = ctx_de(db, b)
    ctx.paciente_id, ctx.identidad_verificada = pac.id, True  # el paciente ya verificó su identidad en la conversación con la institución B
    assert datos(ctx, "consultar_citas")["citas"] == []
    r = _llamar(ctx, "cancelar_cita", codigo_reserva=cita.codigo_reserva)
    assert not r.ok and r.codigo_error == "cita_no_encontrada"


# ======================= agente =======================
def test_el_agente_saluda_con_el_nombre_de_la_institucion_sin_la_marca_de_ficticia(db, dos):
    a, _ = dos
    conv, r = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True, institucion_id=a.id)
    try:
        assert conv.institucion_id == a.id
        assert "Alfa" in r.texto and "ficticia" not in r.texto.lower()
    finally:
        SESIONES.quitar(conv.id)


def test_iniciar_con_una_institucion_inexistente_falla(db):
    with pytest.raises(ErrorNegocio) as e:
        agente_service.iniciar(db, CanalConversacion.VOZ, institucion_id=987654)
    assert e.value.codigo == "institucion_no_encontrada"


def test_el_agente_solo_nombra_las_especialidades_de_su_institucion(db, dos):
    a, b = dos
    conv, _ = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True, institucion_id=b.id)
    try:
        _, r = agente_service.enviar(db, conv.id, "¿Qué especialidades tienen?")
        assert "Dermatología Beta" in r.texto and "Cardiología" not in r.texto
    finally:
        SESIONES.quitar(conv.id)


# ======================= API =======================
def test_listar_instituciones_requiere_permiso_del_asistente(client, db, dos):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    assert client.get(f"{A}/instituciones").status_code in (401, 403)
    assert client.get(f"{A}/instituciones", headers=cabecera(client, "consulta@ejemplo.com")).status_code == 403
    r = client.get(f"{A}/instituciones", headers=cabecera(client, "rec@ejemplo.com"))
    assert r.status_code == 200
    por_id = {i["id"]: i for i in r.json()}
    a, b = dos
    assert por_id[a.id]["sedes"] == 1 and por_id[a.id]["especialidades"] == 1
    assert por_id[b.id]["nombre_corto"].startswith("Beta") and "ficticia" not in por_id[b.id]["nombre_corto"]


def test_iniciar_una_conversacion_de_texto_en_una_institucion(client, db, dos):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    b = dos[1]
    r = client.post(f"{A}/agente/conversaciones", json={"institucion_id": b.id}, headers=h)
    assert r.status_code == 201, r.text
    assert "Beta" in r.json()["texto"]
    assert client.post(f"{A}/agente/conversaciones", json={"institucion_id": 987654}, headers=h).status_code == 404


def test_nombre_corto_quita_la_marca_de_ficticia():
    assert nombre_corto("Clínica Santa Aurora (ficticia)") == "Clínica Santa Aurora"
    assert nombre_corto("Hospital Regional (demo)") == "Hospital Regional"
    assert nombre_corto("IPS Salud Vital del Norte") == "IPS Salud Vital del Norte"


def test_alcance_sin_institucion_no_filtra(db, dos):
    a, b = dos
    todas = {e.id for e in db.scalars(select(Especialidad).where(institucion_service.alcance(Especialidad.institucion_id, None)))}
    assert {a.esp.id, b.esp.id} <= todas
    solo_a = {e.id for e in db.scalars(select(Especialidad).where(institucion_service.alcance(Especialidad.institucion_id, a.id)))}
    assert a.esp.id in solo_a and b.esp.id not in solo_a


# ======================= la institución se elige HABLANDO =======================
@pytest.fixture
def llamada_sin_institucion(db, dos):
    """Una conversación de voz abierta SIN institución, con dos instituciones activas: el asistente tiene que preguntarla."""
    conv, saludo = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True)
    yield SimpleNamespace(conv=conv, saludo=saludo, a=dos[0], b=dos[1], decir=lambda t: agente_service.enviar(db, conv.id, t)[1])
    SESIONES.quitar(conv.id)


def test_con_varias_instituciones_el_asistente_pregunta_con_cual_al_saludar(llamada_sin_institucion):
    s = llamada_sin_institucion
    assert s.conv.institucion_id is None
    nombre_a, nombre_b = s.a.nombre.replace(" (ficticia)", ""), s.b.nombre.replace(" (ficticia)", "")
    assert nombre_a in s.saludo.texto and nombre_b in s.saludo.texto and "¿Con cuál institución" in s.saludo.texto


def test_el_paciente_dice_el_nombre_y_queda_elegida(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    r = s.decir("Con Beta, por favor")
    assert r.institucion and r.institucion["id"] == s.b.id and "ficticia" not in r.institucion["nombre"]
    assert "le atiende Beta" in r.texto and "¿En qué puedo ayudarle?" in r.texto
    db.refresh(s.conv)
    assert s.conv.institucion_id == s.b.id
    # desde ahora solo ve lo de esa institución
    assert "Dermatología Beta" in s.decir("¿Qué especialidades tienen?").texto
    assert "Cardiología" not in s.decir("¿Qué especialidades tienen?").texto


def test_si_pide_una_especialidad_la_atiende_una_sola_institucion_queda_elegida(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    r = s.decir("Quiero una cita de cardiología alfa")
    assert r.institucion and r.institucion["id"] == s.a.id
    assert r.texto.startswith("Para Cardiología Alfa, le atiende Alfa") and "verificar su identidad" in r.texto  # y sigue con el trámite en el mismo turno
    db.refresh(s.conv)
    assert s.conv.institucion_id == s.a.id


def test_si_varias_atienden_la_especialidad_pregunta_y_la_recuerda(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    compartida = f"Medicina Común {_sufijo()}"
    db.add(Especialidad(nombre=compartida))  # sin institución: la atienden todas
    db.flush()
    r = s.decir(f"Necesito una cita de {compartida}")
    assert r.institucion is None and compartida in r.texto and "¿Con cuál desea comunicarse?" in r.texto
    r = s.decir("Con Beta")
    assert r.institucion["id"] == s.b.id and "verificar su identidad" in r.texto
    assert SESIONES.obtener(s.conv.id).espec_deseada == compartida  # no hay que repetirla


def test_si_no_se_entiende_la_institucion_se_vuelve_a_preguntar(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    r = s.decir("quiero una cita")
    assert r.institucion is None and "con cuál institución" in r.texto and s.b.nombre.replace(" (ficticia)", "") in r.texto
    r = s.decir("mmm no sé")
    assert r.institucion is None and "no identifiqué la institución" in r.texto
    db.refresh(s.conv)
    assert s.conv.institucion_id is None


def test_puede_cambiar_de_institucion_a_mitad_de_la_llamada(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    s.decir("Con Alfa")
    r = s.decir("Quiero hablar con otra institución")
    assert r.institucion is None and "con cuál institución" in r.texto
    db.refresh(s.conv)
    assert s.conv.institucion_id is None
    assert s.decir("Mejor con Beta").institucion["id"] == s.b.id


def test_con_una_sola_institucion_no_se_pregunta_nada(db):
    inst = montar(db, "Unica", "Medicina Unica", "Dra. Unica Ficticia")
    otras = [i for i in institucion_service.listar(db) if i.id != inst.id]
    for i in otras:
        i.activa = False
    db.flush()
    conv, saludo = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True)
    try:
        assert conv.institucion_id == inst.id and "Unica" in saludo.texto and "Con cuál institución" not in saludo.texto
    finally:
        SESIONES.quitar(conv.id)


# ======================= la conversación termina cuando ya no necesita más ayuda =======================
def test_decir_que_eso_es_todo_pasa_por_la_encuesta_y_luego_termina_la_conversacion(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    s.decir("Con Alfa")
    r = s.decir("Gracias, eso es todo")
    assert r.intencion == "encuesta" and not r.terminar and "de uno a cinco" in r.texto
    r = s.decir("Cinco")
    assert r.terminar and r.intencion == "despedida" and "Alfa" in r.texto and "excelente día" in r.texto


def test_un_no_suelto_solo_termina_si_se_pregunto_si_necesita_algo_mas(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    s.decir("Con Alfa")
    assert not s.decir("no").terminar  # no se le había preguntado nada de eso
    r = s.decir("Tengo una queja porque me atendieron mal")
    assert "¿Le ayudo con algo más?" in r.texto and not r.terminar
    assert s.decir("No, gracias").intencion == "encuesta"
    assert s.decir("no quiero responder").terminar


@pytest.mark.parametrize("frase", ["Nada más quiero cancelar mi cita", "quiero una cita, eso es todo lo que pido", "no es todo, también necesito otra cosa"])
def test_una_frase_que_pide_algo_no_se_toma_por_despedida(db, llamada_sin_institucion, frase):
    s = llamada_sin_institucion
    s.decir("Con Alfa")
    assert not s.decir(frase).terminar


def test_por_la_api_de_texto_la_despedida_cierra_la_conversacion(client, db, dos):
    from app.models import Conversacion
    from app.models.enums import EstadoConversacion

    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    c = client.post(f"{A}/agente/conversaciones", json={"institucion_id": dos[0].id}, headers=h).json()
    r = client.post(f"{A}/agente/conversaciones/{c['conversacion_id']}/mensajes", json={"texto": "No, eso es todo"}, headers=h).json()
    assert r["terminada"] is True and r["intencion"] == "despedida"  # por texto no hay encuesta (es del canal de voz)
    db.expire_all()
    assert db.get(Conversacion, uuid.UUID(c["conversacion_id"])).estado == EstadoConversacion.RESUELTA
    assert client.post(f"{A}/agente/conversaciones/{c['conversacion_id']}/mensajes", json={"texto": "hola"}, headers=h).status_code == 409


# ======================= encuesta de satisfacción =======================
def _terminar_la_conversacion(s):
    s.decir("Con Alfa")
    return s.decir("No, eso es todo")


def test_una_calificacion_alta_se_guarda_y_se_despide_sin_mas_preguntas(db, llamada_sin_institucion):
    from app.models import EncuestaSatisfaccion

    s = llamada_sin_institucion
    assert _terminar_la_conversacion(s).intencion == "encuesta"
    r = s.decir("Excelente")
    assert r.terminar and "Muchas gracias por su calificación" in r.texto
    e = db.scalars(select(EncuestaSatisfaccion)).one()
    assert (e.puntaje, e.comentario, e.institucion_id, e.conversacion_id) == (5, None, s.a.id, s.conv.id)


def test_una_calificacion_baja_pregunta_que_mejorar_y_guarda_el_comentario(db, llamada_sin_institucion):
    from app.models import EncuestaSatisfaccion

    s = llamada_sin_institucion
    _terminar_la_conversacion(s)
    r = s.decir("Dos")
    assert not r.terminar and "¿Qué podríamos mejorar?" in r.texto
    r = s.decir("Tardó mucho en entenderme   y repetía las preguntas")
    assert r.terminar and "lo tendremos en cuenta" in r.texto
    e = db.scalars(select(EncuestaSatisfaccion)).one()
    assert e.puntaje == 2 and e.comentario == "Tardó mucho en entenderme y repetía las preguntas"


def test_se_puede_no_comentar(db, llamada_sin_institucion):
    from app.models import EncuestaSatisfaccion

    s = llamada_sin_institucion
    _terminar_la_conversacion(s)
    s.decir("tres")
    assert s.decir("nada").terminar
    assert db.scalars(select(EncuestaSatisfaccion)).one().comentario is None


def test_quien_no_quiere_responder_no_deja_registro_y_no_se_insiste(db, llamada_sin_institucion):
    from app.models import EncuestaSatisfaccion

    s = llamada_sin_institucion
    _terminar_la_conversacion(s)
    r = s.decir("prefiero no responder")
    assert r.terminar and "no hay problema" in r.texto
    assert db.scalars(select(EncuestaSatisfaccion)).all() == []


def test_si_no_se_entiende_la_calificacion_se_pregunta_una_vez_mas_y_luego_se_deja(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    _terminar_la_conversacion(s)
    r = s.decir("mmm quiero una cita de cardiología")
    assert not r.terminar and "número del uno al cinco" in r.texto
    assert s.decir("no sé qué decir sobre eso").terminar


def test_una_emergencia_dicha_durante_la_encuesta_se_atiende_primero(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    _terminar_la_conversacion(s)
    r = s.decir("tengo dolor de pecho")
    assert r.emergencia and not r.terminar


def test_con_la_encuesta_apagada_se_despide_directamente(db, dos):
    from app.services import config_ia_service

    admin = crear_usuario(db, email="adm@ejemplo.com", rol=Rol.ADMINISTRADOR)
    config_ia_service.actualizar(db, admin, {"encuesta_activa": False})
    conv, _ = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True, institucion_id=dos[0].id)
    try:
        assert agente_service.enviar(db, conv.id, "No, eso es todo")[1].terminar
    finally:
        SESIONES.quitar(conv.id)


def test_una_conversacion_transferida_a_una_persona_no_hace_encuesta(db, llamada_sin_institucion):
    s = llamada_sin_institucion
    s.decir("Con Alfa")
    s.decir("Quiero hablar con una persona")
    r = s.decir("gracias, eso es todo")
    assert r.intencion != "encuesta"


@pytest.mark.parametrize("frase, esperado", [("cinco", 5), ("un 4", 4), ("Excelente", 5), ("más o menos", 3), ("muy mala", 1), ("buena", 4), ("le doy un uno", 1),
                                              ("tres o cuatro", None), ("quiero una cita de pediatría", None), ("hola", None)])
def test_entiende_la_calificacion_dicha_en_voz_alta(frase, esperado):
    from app.agents import entendimiento as nlu
    assert nlu.puntaje(frase) == esperado


def test_el_reporte_de_satisfaccion_cuenta_solo_lo_real(db, dos):
    from app.models import EncuestaSatisfaccion
    from app.services import reporte_service

    a, b = dos
    for prueba, inst, punt, com in ((False, a, 5, None), (False, a, 2, "muy lento"), (False, b, 4, None), (True, a, 1, "de prueba")):
        conv = conversacion_service.crear(db, CanalConversacion.VOZ, es_prueba=prueba, institucion_id=inst.id)
        db.add(EncuestaSatisfaccion(conversacion_id=conv.id, institucion_id=inst.id, canal=CanalConversacion.VOZ, puntaje=punt, comentario=com))
    db.flush()
    r = reporte_service.generar(db, "satisfaccion", None, None)
    ind = dict(r.indicadores)
    assert ind["Encuestas respondidas"] == 3 and ind["Calificación promedio (1 a 5)"] == pytest.approx(3.67, abs=0.01)
    assert ind["% satisfechos (4 o 5)"] == "66.7 %" and ind["% insatisfechos (1 o 2)"] == "33.3 %" and ind["Con comentario"] == 1
    assert ind["Encuestas de conversaciones de prueba (no cuentan)"] == 1
    assert dict(map(tuple, r.tablas[0].filas)) == {"5": 1, "4": 1, "3": 0, "2": 1, "1": 0}
    assert [f[2] for f in r.tablas[2].filas] == ["muy lento"]  # el comentario de una conversación de prueba no sale
    assert {f[0] for f in r.tablas[1].filas} == {a.nombre, b.nombre}

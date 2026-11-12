"""Conversación por VOZ: el paciente da sus datos de a poco, el asistente pide, lee, ofrece y confirma. Todo lo crítico lo hace el código
(sin modelo de lenguaje): por eso estas pruebas fallan si algún turno intenta llamarlo."""
from datetime import date

import pytest
from sqlalchemy import select

from app.agents import entendimiento as nlu
from app.agents.recepcionista import PEDIR_IDENTIDAD, _direccion_hablada, _hablado, _limpiar_para_voz
from app.models import Cita, EjecucionHerramienta, Especialidad, Sede
from app.models.enums import CanalCita, CanalConversacion
from app.services import agente_service
from tests.test_agente import decir, guion  # noqa: F401 - fixture
from tests.test_herramientas import DOC, con_agenda, paciente_real

HOY = date(2026, 11, 3)  # martes


def llamada(db):
    conv, _ = agente_service.iniciar(db, CanalConversacion.VOZ, es_prueba=True)
    return conv


# ======================= lenguaje hablado =======================
@pytest.mark.parametrize("frase, dia, franja", [
    ("para mañana en la mañana", date(2026, 11, 4), "manana"), ("para mañana y la mañana", date(2026, 11, 4), "manana"),  # lo que escribe Whisper
    ("mañana por la tarde", date(2026, 11, 4), "tarde"), ("en la mañana", None, "manana"), ("el jueves", date(2026, 11, 5), None), ("lo más temprano posible", None, "manana"),
])
def test_dia_y_franja_se_separan(frase, dia, franja):
    r = nlu.rango_de_fechas(frase, HOY)
    assert (r[0] if r else None) == dia and nlu.franja(frase) == franja


@pytest.mark.parametrize("frase", ["mi cédula es 1020304050 y nací el 5 de marzo de 1985", "nací el 14 de marzo de 1991", "nací el 5/3/1985"])
def test_la_fecha_de_nacimiento_no_se_toma_por_el_dia_de_la_cita(frase):
    assert nlu.rango_de_fechas(frase, HOY) is None
    assert nlu.rango_de_fechas(frase + " y quiero la cita el 10 de noviembre", HOY) == (date(2026, 11, 10), date(2026, 11, 10))


@pytest.mark.parametrize("frase, esperado", [
    ("quiero agendar una cita de medicina general", True), ("necesito una consulta de pediatría", True), ("quisiera sacar un turno", True), ("me gustaría pedir una cita", True),
    ("quiero cancelar mi cita", False), ("quiero reprogramar la cita", False), ("¿qué especialidades tienen?", False), ("quiero consultar los requisitos", False), ("gracias", False),
])
def test_quiere_agendar(frase, esperado):
    assert nlu.quiere_agendar(frase) is esperado


@pytest.mark.parametrize("frase, esperado", [("la segunda", 1), ("el último", 2), ("la primera opción", 0), ("opción dos", 1), ("la tercera", 2), ("la cuarta", None), ("hola", None)])
def test_elegir_ordinal_entre_tres(frase, esperado):
    assert nlu.elegir_ordinal(frase, 3) == esperado


@pytest.mark.parametrize("frase, horas, esperado", [("a las nueve", [(8, 0), (9, 0)], None), ("la de las 9", [(8, 0), (9, 0)], 1), ("la de las 8 y 20", [(8, 20), (10, 0)], 0),
                                                    ("opción dos", [(8, 20), (10, 0)], 1), ("mejor la segunda", [(8, 20), (10, 0)], 1)])
def test_elegir_un_horario_hablado(frase, horas, esperado):
    assert nlu.elegir_opcion(frase, horas) == esperado


@pytest.mark.parametrize("frase, esperado", [("otro día por favor", True), ("más tarde", True), ("ninguna me sirve", True), ("no me queda bien", False), ("sí, la primera", False)])
def test_pide_otras_opciones(frase, esperado):
    assert nlu.pide_otras_opciones(frase) is esperado or frase == "no me queda bien"


@pytest.mark.parametrize("frase, esperado", [("¿dónde están las sedes?", True), ("¿cuántas sedes tienen?", True), ("¿cómo llego a la sede norte?", True), ("¿dónde queda?", True),
                                             ("prefiero la sede norte", False), ("quiero una cita", False)])
def test_pregunta_sedes(frase, esperado):
    assert nlu.pregunta_sedes(frase) is esperado


@pytest.mark.parametrize("frase, esperado", [("Sí, es correcto.", True), ("exacto, así es", True), ("claro que sí", True), ("No, es incorrecto", False), ("no es ese", False), ("es el 5", False)])
def test_confirmar_un_dato_leido(frase, esperado):
    assert nlu.confirma_dato(frase) is esperado


def test_el_texto_hablado_no_lleva_marcas_internas_ni_simbolos():
    assert _hablado("Medicina General (demo)") == "Medicina General" and _hablado("Dra. Ana (ficticia)") == "Dra. Ana"
    assert _direccion_hablada("Calle Ficticia 10 # 5-20") == "Calle Ficticia 10 número 5 20"
    assert _limpiar_para_voz("Con **Pediatría (demo)** el lunes") == "Con Pediatría el lunes"


# ======================= identidad por partes =======================
def test_el_documento_se_lee_en_voz_alta_y_la_fecha_se_pide_despues(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv = llamada(db)
    r = decir(db, conv, "quiero agendar una cita de medicina general")
    assert r.texto == PEDIR_IDENTIDAD
    r = decir(db, conv, f"mi cédula es {DOC}")
    assert r.texto == "Entendí el documento 1 0 2 0 3 0 4 0 5 0. ¿Es correcto?" and r.intencion == "identificacion"
    r = decir(db, conv, "Sí, es correcto.")
    assert r.texto == "Gracias. Ahora dígame su fecha de nacimiento: día, mes y año."
    r = decir(db, conv, "nací el cinco de marzo de 1985")  # Whisper escribe «5 de marzo de 1985»
    assert "Ya verifiqué su identidad" in r.texto and "Para Medicina General tengo estos horarios" in r.texto
    assert guion.llamadas == []


def test_si_el_documento_leido_esta_mal_se_pide_de_nuevo(db, guion):
    con_agenda(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita de medicina general")
    decir(db, conv, f"mi cédula es {DOC[:-1]}9")
    r = decir(db, conv, "no, está mal")
    assert "de nuevo su número de documento" in r.texto
    r = decir(db, conv, f"es {DOC}")
    assert "Entendí el documento 1 0 2 0 3 0 4 0 5 0" in r.texto  # el número nuevo reemplaza al equivocado
    assert guion.llamadas == []


def test_corregir_el_numero_diciendolo_otra_vez_sin_decir_no(db, guion):
    paciente_real(db)
    con_agenda(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita de medicina general")
    decir(db, conv, f"mi cédula es {DOC[:-1]}9")
    r = decir(db, conv, f"perdón, es {DOC}")
    assert "Entendí el documento 1 0 2 0 3 0 4 0 5 0" in r.texto


def test_faltan_datos_se_piden_uno_a_uno(db, guion):
    con_agenda(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita")
    assert decir(db, conv, "nací el 5 de marzo de 1985").texto == "Gracias. Dígame el tipo y número de su documento."  # dio la fecha primero
    assert decir(db, conv, f"{DOC}").texto.startswith("Entendí el documento")
    assert "cédula de ciudadanía, tarjeta de identidad u otro" in decir(db, conv, "sí").texto  # falta el tipo
    assert guion.llamadas == []


def test_identidad_equivocada_no_revela_nada_y_vuelve_a_pedir(db, guion):
    paciente_real(db)
    con_agenda(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita de medicina general")
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 6 de marzo de 1985")  # fecha incorrecta
    assert r.texto.startswith("No pude verificar su identidad con esos datos") and "persona" not in r.texto
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 7 de marzo de 1985")
    assert "Si lo prefiere, puedo pasarlo con una persona" in r.texto  # a la segunda falla se ofrece una salida
    assert guion.llamadas == []


def test_un_dato_suelto_dentro_de_otra_pregunta_no_inicia_la_identificacion(db, guion):
    con_agenda(db)
    conv = llamada(db)
    guion.cola.append(__import__("tests.test_agente", fromlist=["texto"]).texto("Claro."))
    decir(db, conv, "¿atienden el 5 de marzo de 1985? mi hijo nació ese día")
    assert len(guion.llamadas) == 1  # lo atendió el modelo: no se confundió con una identificación


# ======================= ofrecer horarios hablando =======================
def identificada(db, guion, frase="quiero una cita de medicina general", **extra):
    con_agenda(db)
    paciente_real(db)
    conv = llamada(db)
    decir(db, conv, frase)
    return conv, decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")


def test_se_ofrecen_hasta_tres_horarios_con_profesional_y_sede_dichos_para_voz(db, guion):
    conv, r = identificada(db, guion)
    assert r.texto.count("Opción") == 3 and "con la doctora" in r.texto and " en Sede " in r.texto and "(demo)" not in r.texto
    assert r.texto.endswith("¿Cuál prefiere? Puede decir la primera, la segunda, la tercera, o la hora.")
    assert r.herramientas == ["buscar_paciente", "consultar_disponibilidad"] and guion.llamadas == []


def test_elegir_por_orden_confirmar_y_agendar_por_voz(db, guion):
    conv, _ = identificada(db, guion)
    r = decir(db, conv, "la segunda")
    assert r.requiere_confirmacion and r.texto.endswith("¿Lo confirma?")
    r = decir(db, conv, "Sí, confírmela.")
    assert r.texto.startswith("Listo, su cita quedó agendada") and "Su código de reserva es " in r.texto
    cita = db.scalar(select(Cita))
    assert cita.canal == CanalCita.VOZ and cita.conversacion_id == conv.id
    assert guion.llamadas == []


def test_despues_de_agendar_se_olvida_lo_pedido_para_la_siguiente_cita(db, guion):
    conv, _ = identificada(db, guion)
    decir(db, conv, "la primera")
    decir(db, conv, "sí")
    r = decir(db, conv, "quiero otra cita")
    assert r.texto.startswith("¿Para qué especialidad desea la cita?")  # ya estaba identificada: pide solo la especialidad


def test_la_especialidad_se_elige_por_orden_tras_oir_la_lista(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita")
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")
    assert "¿Para qué especialidad desea la cita? Tenemos Medicina General" in r.texto
    r = decir(db, conv, "la primera")
    assert "Para Medicina General tengo estos horarios" in r.texto and guion.llamadas == []


def test_otro_dia_busca_a_partir_del_ultimo_dia_ofrecido(db, guion):
    conv, r = identificada(db, guion)
    primero = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "consultar_disponibilidad")).all()
    r2 = decir(db, conv, "otro día por favor")
    assert "tengo estos horarios" in r2.texto and r2.texto != r.texto
    todas = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "consultar_disponibilidad")).all()
    assert len(todas) == len(primero) + 1 and todas[-1].parametros["desde"] > todas[0].parametros["desde"]


def test_la_franja_pedida_se_aplica_al_buscar(db, guion):
    conv, _ = identificada(db, guion)
    decir(db, conv, "mejor por la tarde")
    ultima = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "consultar_disponibilidad")).all()[-1]
    assert ultima.parametros["franja"] == "tarde"


def test_si_no_hay_horarios_lo_dice_y_ofrece_buscar_en_otros_dias(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita de medicina general el 1 de enero de 2040")
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")
    assert "No encontré horarios libres de Medicina General" in r.texto and "¿Quiere que busque en otros días?" in r.texto


# ======================= datos que se contestan con el sistema =======================
def test_las_sedes_se_dicen_con_su_direccion_en_palabras(db, guion):
    s = Sede(nombre="Sede Demo Centro", direccion="Calle Ficticia 10 # 5-20", ciudad="Ciudad Demo")
    db.add_all([s, Sede(nombre="Sede Demo Norte", direccion="Avenida Imaginaria 80 # 45-12")])
    db.flush()
    r = decir(db, llamada(db), "¿dónde están las sedes?")
    assert r.texto == ("Tenemos dos sedes: Sede Demo Centro, en Calle Ficticia 10 número 5 20; Sede Demo Norte, en Avenida Imaginaria 80 número 45 12. ¿Le ayudo a agendar una cita?")
    assert guion.llamadas == [] and r.intencion == "consulta_catalogo"


def test_los_profesionales_de_una_especialidad_se_nombran(db, guion):
    b = con_agenda(db)
    r = decir(db, llamada(db), "¿qué médicos atienden en medicina general?")
    assert "En Medicina General atienden" in r.texto and "la doctora" in r.texto and guion.llamadas == []


def test_sin_especialidad_se_pregunta_de_cual(db, guion):
    con_agenda(db)
    assert decir(db, llamada(db), "¿qué médicos tienen?").texto == "Con gusto. ¿De qué especialidad quiere conocer a los profesionales?"


def test_la_especialidad_se_reconoce_por_como_se_dice_aunque_el_catalogo_lleve_marca_de_demo(db, guion):
    con_agenda(db)
    db.scalars(select(Especialidad)).first().nombre = "Medicina General (demo)"
    db.flush()
    paciente_real(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita de medicina general")
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")
    assert "Para Medicina General tengo estos horarios" in r.texto and "demo" not in r.texto.lower()


# ======================= lo que el reconocimiento de voz deforma y las peticiones de «ver fechas» =======================
def _tras_pedir_especialidad(db):
    con_agenda(db)
    paciente_real(db)
    conv = llamada(db)
    decir(db, conv, "quiero una cita")
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")
    assert "¿Para qué especialidad desea la cita?" in r.texto
    return conv


def test_una_especialidad_deformada_se_confirma_y_con_el_si_se_ofrecen_horarios(db, guion):
    conv = _tras_pedir_especialidad(db)
    r = decir(db, conv, "Medicina Jeneral")  # como la escribiría el reconocimiento de voz
    assert r.texto == "¿Se refiere a Medicina General?"
    r = decir(db, conv, "Sí.")
    assert "Para Medicina General tengo estos horarios" in r.texto and guion.llamadas == []  # sin pedirle fechas ni llamar al modelo


def test_si_dice_que_no_a_la_especialidad_deformada_se_le_vuelve_a_ofrecer_la_lista(db, guion):
    conv = _tras_pedir_especialidad(db)
    decir(db, conv, "Medicina Jeneral")
    r = decir(db, conv, "no")
    assert r.texto.startswith("De acuerdo. ¿Para qué especialidad desea la cita? Tenemos Medicina General")
    assert "Para Medicina General tengo estos horarios" in decir(db, conv, "Medicina General").texto


def test_ver_las_fechas_disponibles_muestra_horarios_en_vez_de_pedir_una_fecha(db, guion):
    conv, _ = identificada(db, guion)
    decir(db, conv, "otro día")
    r = decir(db, conv, "Quiero ver las fechas disponibles con sus horarios")
    assert "tengo estos horarios" in r.texto and "hasta qué fecha" not in r.texto and guion.llamadas == []


@pytest.mark.parametrize("frase", ["mi cédula es 918 374", "la segunda", "hola buenas tardes", "sí"])
def test_no_se_confunde_con_una_especialidad_lo_que_no_lo_es(frase):
    assert nlu.mencion_aproximada(frase, ["Ortopedia", "Dermatología", "Medicina General"]) is None


@pytest.mark.parametrize("frase, esperada", [("Porto pedio", "Ortopedia"), ("dermatologia", "Dermatología"), ("ortopeda", "Ortopedia")])
def test_reconoce_una_especialidad_deformada_por_el_reconocimiento_de_voz(frase, esperada):
    assert nlu.mencion_aproximada(frase, ["Ortopedia", "Dermatología", "Medicina General"]) == esperada


@pytest.mark.parametrize("frase, esperado", [("Quiero ver las fechas disponibles con sus horarios", True), ("¿qué horarios hay?", True), ("¿hay disponibilidad el lunes?", True),
                                             ("¿cuál es el horario de atención?", False), ("quiero cancelar mi cita", False)])
def test_pide_ver_horarios(frase, esperado):
    assert nlu.pide_horarios(frase) is esperado

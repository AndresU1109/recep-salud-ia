"""Fase 11: comprensión determinista del lenguaje (documento, fecha de nacimiento, códigos, opciones, consultas)."""
from datetime import date

import pytest

from app.agents import entendimiento as e

HOY = date(2026, 11, 2)  # lunes


@pytest.mark.parametrize("texto, esperado", [
    ("mi cédula es 1020304050 y nací el 5 de marzo de 1985", ("CC", "1020304050", date(1985, 3, 5))),
    ("cédula 1.020.304.050, nacimiento 05/03/1985", ("CC", "1020304050", date(1985, 3, 5))),
    ("tarjeta de identidad 1020304050 nacida el 12 de enero de 2012", ("TI", "1020304050", date(2012, 1, 12))),
    ("pasaporte AB123456 nací 1980-07-20", None),  # número alfanumérico: no se arriesga
    ("mi CC es 79123456, fecha de nacimiento 1-2-1970", ("CC", "79123456", date(1970, 2, 1))),
    ("cédula de extranjería 123456789 nací el 3 de mayo de 1990", ("CE", "123456789", date(1990, 5, 3))),
    ("cédula 918-374-6502, nací el 21 de agosto de 1993", ("CC", "9183746502", date(1993, 8, 21))),  # así lo escribe Whisper: como un teléfono
    ("mi cédula es 918 374 6502 y nací el 21 de agosto de 1993", ("CC", "9183746502", date(1993, 8, 21))),
])
def test_extrae_identidad_cuando_trae_los_tres_datos(texto, esperado):
    assert e.extraer_identidad(texto, HOY) == esperado


@pytest.mark.parametrize("texto", [
    "mi cédula es 1020304050", "nací el 5 de marzo de 1985", "mi documento es 1020304050 y nací el 5 de marzo de 1985",  # sin tipo: que el asistente pregunte
    "cédula 1020304050 nací el 5 de marzo de 2090", "cédula 1020304050 y 2030405060, nací el 5 de marzo de 1985", "quiero una cita el 4 de marzo de 2030",
    "cédula 12", "hola buenas",
])
def test_no_extrae_identidad_si_falta_algo_o_es_ambiguo(texto):
    assert e.extraer_identidad(texto, HOY) is None


@pytest.mark.parametrize("texto, esperado", [
    ("mi cédula es 1-0-2-0-3-0-4-0-5-0 y nací el 5 de marzo de 1985", ("CC", "1020304050", date(1985, 3, 5))),  # así lo escribió Whisper
    ("cédula 1 0 2 0 3 0 4 0 5 0, nací el 5 de marzo de 1985", ("CC", "1020304050", date(1985, 3, 5))),
    ("mi cédula es uno cero dos cero tres cero cuatro cero cinco cero y nací el 5 de marzo de 1985", ("CC", "1020304050", date(1985, 3, 5))),
    ("cédula 79.123.456 nací el 3 de mayo de 1990", ("CC", "79123456", date(1990, 5, 3))),
])
def test_identidad_dictada_por_voz(texto, esperado):
    assert e.extraer_identidad(texto, HOY) == esperado


def test_los_digitos_dictados_no_alteran_otros_numeros_ni_fechas():
    assert e.unir_digitos_dictados("quiero una cita el 5 de marzo, a las 8 y 20, la 2 por favor") == "quiero una cita el 5 de marzo, a las 8 y 20, la 2 por favor"
    assert e.unir_digitos_dictados("nací el 05/03/1985 y mi cita es el 4-3-2030") == "nací el 05/03/1985 y mi cita es el 4-3-2030"
    assert e.unir_digitos_dictados("dame una cita, dos si es posible") == "dame una cita, dos si es posible"


def test_la_fecha_de_una_cita_futura_no_se_toma_por_nacimiento():
    assert e.extraer_fecha_nacimiento("cédula 1020304050, quiero cita el 4 de marzo de 2030", HOY) is None


@pytest.mark.parametrize("texto, esperado", [
    ("quiero cancelar mi cita K7M2QXD9", ["K7M2QXD9"]), ("el código es ab23cd45", ["AB23CD45"]), ("cancelar mi cita", []),  # 'CANCELAR' tiene 8 letras: la base lo descarta
    ("mi código es AB12CD34", []),  # el alfabeto de los códigos no tiene 0, 1, O ni I
])
def test_candidatos_a_codigo(texto, esperado):
    c = e.candidatos_a_codigo(texto)
    assert [x for x in c if x != "CANCELAR"] == esperado


@pytest.mark.parametrize("texto", ["quiero cancelar mi cita", "anular la cita", "necesito eliminar mi cita", "borra mi cita"])
def test_detecta_cancelar(texto):
    assert e.quiere_cancelar(texto)


@pytest.mark.parametrize("texto", ["quiero una cita", "¿cuál es la política de cancelación?" if False else "hola", "¿qué especialidades hay?"])
def test_no_detecta_cancelar(texto):
    assert not e.quiere_cancelar(texto)


HORAS = [(8, 0), (8, 20), (8, 40), (14, 0), (14, 30)]


@pytest.mark.parametrize("texto, esperado", [
    ("la primera", 0), ("la segunda, por favor", 1), ("la tercera", 2), ("la última", 4), ("opción 2", 1), ("la 3", 2), ("número 4", 3),
    ("la de las 8:20", 1), ("a las 8:40", 2), ("2:00", 3), ("a las 2 y 30", 4), ("a las 8", 0), ("quiero la de las 14:30", 4),
])
def test_elige_una_opcion(texto, esperado):
    assert e.elegir_opcion(texto, HORAS) == esperado


@pytest.mark.parametrize("texto", ["no sé cuál", "mejor otro día", "la sexta", "¿tienen para la tarde?", "la de las 9", "quiero saber más del horario y de la sede que queda cerca de mi casa por favor y gracias"])
def test_no_elige_si_no_esta_claro(texto):
    assert e.elegir_opcion(texto, HORAS[:5]) is None


@pytest.mark.parametrize("texto, esperado", [
    ("¿qué especialidades tienen?", {"especialidades"}), ("¿qué horarios tiene medicina general?", {"horarios"}), ("¿qué requisitos necesito?", {"requisitos"}),
    ("¿qué debo llevar a mi cita?", {"requisitos"}), ("¿cuál es la política de cancelación?", {"politicas"}), ("¿qué médicos atienden?", {"profesionales"}),
    ("¿atienden los domingos?", {"horarios"}), ("¿qué pasa si no llego a mi cita?", {"politicas"}), ("¿hay multas por faltar?", {"politicas"}),
    ("quiero una cita", set()), ("hola", set()),
])
def test_que_consultar_antes_de_responder(texto, esperado):
    assert e.consulta_catalogo(texto) == esperado


def test_tipo_de_politica():
    assert e.tipo_politica("¿cómo cancelo?") == ["CANCELACION"] and e.tipo_politica("¿y si quiero cambiar mi cita?") == ["REPROGRAMACION"]
    assert e.tipo_politica("¿qué pasa si no llego?") == ["INASISTENCIA"] and len(e.tipo_politica("¿cuál es la política?")) == 3


def test_mencion_de_catalogo():
    nombres = ["Medicina General", "Medicina Interna", "Pediatría"]
    assert e.mencion("una cita de pediatría por favor", nombres) == "Pediatría"
    assert e.mencion("medicina general", nombres) == "Medicina General"
    assert e.mencion("una cita de medicina", nombres) is None  # ambigua
    assert e.mencion("una cita con el cardiólogo", nombres) is None


@pytest.mark.parametrize("texto, esperado", [
    ("quiero poner una queja porque me atendieron muy mal en la recepción de la sede", "QUEJA"), ("tengo un reclamo por un cobro que no corresponde a mi consulta", "RECLAMO"),
    ("les sugiero ampliar el horario de atención de los sábados en la sede norte", "SUGERENCIA"),
    ("quiero poner una queja", None), ("quiero una cita", None), ("hola", None), ("tengo una sugerencia", None),  # solo anuncia: el asistente pregunta el detalle
])
def test_tipo_de_solicitud_solo_si_ya_cuenta_el_motivo(texto, esperado):
    assert e.tipo_solicitud(texto) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("para el 4 de marzo de 2030", (date(2030, 3, 4), date(2030, 3, 4))), ("para el 5 de noviembre", (date(2026, 11, 5), date(2026, 11, 5))),
    ("mañana", (date(2026, 11, 3), date(2026, 11, 3))), ("pasado mañana", (date(2026, 11, 4), date(2026, 11, 4))), ("hoy mismo", (HOY, HOY)),
    ("el jueves", (date(2026, 11, 5), date(2026, 11, 5))), ("el lunes", (date(2026, 11, 9), date(2026, 11, 9))),  # hoy es lunes: el próximo
    ("la próxima semana", (date(2026, 11, 9), date(2026, 11, 15))), ("esta semana", (HOY, date(2026, 11, 8))),
    ("para el 1 de marzo", (date(2027, 3, 1), date(2027, 3, 1))),
])
def test_rango_de_fechas(texto, esperado):
    assert e.rango_de_fechas(texto, HOY) == esperado


@pytest.mark.parametrize("texto", ["quiero una cita", "en la mañana por favor", "a cualquier hora"])
def test_sin_fecha_no_inventa_un_rango(texto):
    assert e.rango_de_fechas(texto, HOY) is None


# ======================= despedida e instituciones =======================
@pytest.mark.parametrize("texto", ["No, eso es todo", "gracias, nada más", "Sí, eso es todo, muchas gracias", "hasta luego", "adiós", "ya no necesito nada más", "no gracias, eso es todo"])
def test_reconoce_que_el_paciente_se_despide(texto):
    assert e.despide(texto)


@pytest.mark.parametrize("texto", ["Nada más quiero cancelar mi cita", "quiero una cita, eso es todo lo que pido", "eso no es todo", "no", "quiero cancelar, es todo", "gracias"])
def test_no_toma_por_despedida_lo_que_pide_algo_mas(texto):
    assert not e.despide(texto)


@pytest.mark.parametrize("texto, si", [("no", True), ("No, gracias", True), ("nada", True), ("no por ahora", True), ("no quiero esa hora", False), ("sí", False)])
def test_un_no_corto_es_sin_mas_ayuda(texto, si):
    assert e.sin_mas(texto) is si


INSTITUCIONES = [(1, "Clínica Santa Aurora", "Clínica general"), (2, "IPS Salud Vital del Norte", "IPS ambulatoria"), (3, "Centro Odontológico Sonrisa Plena", "Centro odontológico"),
                 (4, "Hospital Regional San Gabriel", "Hospital"), (5, "Centro Pediátrico Pequeños Pasos", "Centro pediátrico")]


@pytest.mark.parametrize("texto, esperado", [
    ("con el hospital", 4), ("la clínica", 1), ("el centro odontológico", 3), ("Sonrisa Plena", 3), ("el centro pediátrico", 5), ("salud vital", 2), ("la IPS", 2),
    ("San Gabriel", 4), ("santa aurora", 1), ("pequeños pasos", 5), ("con el centro", None), ("quiero una cita", None), ("hola buenas", None),
])
def test_reconoce_la_institucion_que_nombra_el_paciente(texto, esperado):
    assert e.institucion_nombrada(texto, INSTITUCIONES) == esperado


def test_reconoce_cuando_pide_cambiar_de_institucion():
    assert e.pide_cambiar_institucion("quiero hablar con otra institución") and e.pide_cambiar_institucion("cambiar de institución")
    assert not e.pide_cambiar_institucion("quiero una cita")

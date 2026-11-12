"""Fase 11: reglas deterministas del asistente (emergencias, orientación médica, pedir una persona, confirmaciones)."""
import pytest

from app.agents import seguridad as s


@pytest.mark.parametrize("frase", [
    "tengo dolor de pecho", "me duele mucho el pecho y no puedo respirar", "mi mamá tiene un dolor fuerte en el pecho", "creo que me está dando un infarto",
    "me falta el aire", "mi hijo no puede respirar", "se desmayó mi papá", "perdió el conocimiento", "tiene una convulsión", "sangrado abundante",
    "no para de sangrar", "creo que es un derrame", "se envenenó con algo", "me estoy muriendo", "tuvimos un accidente grave", "voy a tener el bebé, estoy en labor de parto",
    "quiero suicidarme", "pienso en quitarme la vida", "me quiero hacer daño", "SE ESTÁ AHOGANDO!!", "dolor   de   pecho", "tiene la cara caída y no puede hablar",
])
def test_detecta_emergencias(frase):
    assert s.es_emergencia(frase), frase


@pytest.mark.parametrize("frase", [
    "quiero agendar una cita", "¿dónde queda urgencias?", "¿cuál es el horario de urgencias?", "no es una emergencia, solo quiero cambiar mi cita", "hola buenos días",
    "necesito una cita de cardiología", "¿qué requisitos hay para pediatría?", "mi cita es el lunes", "quiero cancelar mi cita", "gracias",
    "el parqueadero está lleno", "tengo una queja del servicio", "¿atienden los domingos?", "me parece que el trámite es lento", "mi cuenta está desactivada",
])
def test_no_confunde_consultas_administrativas_con_emergencias(frase):
    assert not s.es_emergencia(frase), frase


@pytest.mark.parametrize("frase", [
    "¿qué me puedo tomar para el dolor de cabeza?", "me duele la cabeza", "tengo fiebre desde ayer", "¿qué medicamento me sirve para la gripa?", "¿cuántas pastillas debo tomar?",
    "¿qué enfermedad puede ser?", "necesito que me recete algo", "¿cuál es la dosis de acetaminofén?", "mi hijo tiene fiebre, ¿qué hago?", "¿es grave si tengo tos hace una semana?",
    "tengo diarrea", "¿puedo tomar ibuprofeno?", "me duele el estómago", "diagnóstico", "¿es normal que me duela la espalda?",
])
def test_detecta_peticiones_de_orientacion_medica(frase):
    assert s.pide_orientacion_medica(frase), frase


@pytest.mark.parametrize("frase", [
    "quiero una cita con medicina general", "¿qué especialidades tienen?", "necesito cancelar mi cita", "¿cuál es la política de cancelación?", "quiero la receta de arepas",
    "¿tienen consulta de dolor?", "mi documento es 123", "¿cuándo entregan los resultados?", "hola", "quiero agendar con el doctor Pérez",
])
def test_no_confunde_consultas_administrativas_con_orientacion_medica(frase):
    assert not s.pide_orientacion_medica(frase), frase


@pytest.mark.parametrize("frase", [
    "quiero hablar con una persona", "pásame con una persona por favor", "necesito hablar con un funcionario", "¿me puede atender un asesor?", "quiero que me atienda un humano",
    "comuníqueme con recepción", "prefiero hablar con alguien de la institución", "no quiero hablar con un robot", "persona real por favor", "transfiéreme con un operador",
])
def test_detecta_peticiones_de_hablar_con_una_persona(frase):
    assert s.pide_humano(frase), frase


@pytest.mark.parametrize("frase", [
    "quiero una cita", "mi persona de contacto es mi hermana", "la persona que me atendió fue amable", "necesito cancelar", "hola, buenas", "¿cuántas personas atienden?",
])
def test_no_confunde_menciones_a_personas_con_pedir_una_persona(frase):
    assert not s.pide_humano(frase), frase


@pytest.mark.parametrize("frase", ["sí", "Sí.", "si, confirmo", "claro", "dale", "de acuerdo", "OK", "correcto", "perfecto", "por favor", "listo", "sí por favor",
                                   "Sí, confírmela", "sí, confírmelo por favor", "agéndela", "hágalo", "sí, cancélela", "confirmada"])
def test_afirmaciones(frase):
    assert s.es_afirmativo(frase) and not s.es_negativo(frase), frase


@pytest.mark.parametrize("frase", ["no", "No, gracias", "mejor no", "espera", "cancela eso", "todavía no", "no por ahora"])
def test_negaciones(frase):
    assert s.es_negativo(frase) and not s.es_afirmativo(frase), frase


@pytest.mark.parametrize("frase", ["sí pero a las tres", "no sé cuál elegir", "quiero la de las 9", "¿y si mejor otro día?", "sí, pero cámbiala para el jueves", "claro, mi cédula es 1023456789"])
def test_una_frase_con_condiciones_no_cuenta_como_confirmacion_simple(frase):
    assert not s.es_afirmativo(frase), frase


@pytest.mark.parametrize("frase", ["sí, confírmela pero para el martes", "confírmela y también cancele la otra", "sí agéndela con otro doctor", "hágalo mañana"])
def test_una_confirmacion_con_cambios_no_cuenta(frase):
    assert not s.es_afirmativo(frase), frase


def test_cortesias():
    for f in ("hola", "Buenos días", "gracias", "muchas gracias", "eso es todo"):
        assert s.solo_cortesia(f)
    for f in ("hola quiero una cita", "gracias, ¿y el horario?"):
        assert not s.solo_cortesia(f)


def test_los_mensajes_fijos_dan_instrucciones_seguras():
    assert "123" in s.MENSAJE_EMERGENCIA and "urgencias" in s.MENSAJE_EMERGENCIA
    assert "no puedo dar orientación médica" in s.MENSAJE_SIN_ORIENTACION_MEDICA and "agendar" in s.MENSAJE_SIN_ORIENTACION_MEDICA


def test_normalizar():
    assert s.normalizar("  ¡DOLOR   de Pecho!  ") == "dolor de pecho"
    assert s.normalizar("Cédula, niño") == "cedula nino"


def test_no_responder_no_es_una_emergencia_pero_no_responde_si_lo_es():
    from app.agents.seguridad import es_emergencia

    assert not es_emergencia("prefiero no responder") and not es_emergencia("no quiero responder la encuesta")
    assert es_emergencia("mi papá no responde") and es_emergencia("no reacciona")

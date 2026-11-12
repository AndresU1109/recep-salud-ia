"""Fase 11: el agente completo con un LLM guionado (determinista). El comportamiento con el modelo real se mide en test_agente_real."""
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.agents import prompts, seguridad
from app.agents.recepcionista import PEDIR_IDENTIDAD, MAX_PASOS
from app.agents.sesiones import SESIONES
from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.llm import cliente as modulo_cliente
from app.llm.tipos import LlamadaHerramienta, RespuestaLLM
from app.models import Cita, EjecucionHerramienta, Mensaje, MensajeFuente, Solicitud
from app.models.enums import CanalConversacion, EstadoConversacion, ResultadoHerramienta, Rol, TipoSolicitud
from app.services import agente_service, cita_service, config_ia_service
from tests.test_agenda import horario
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_herramientas import DOC, con_agenda, paciente_real
from tests.test_rag_busqueda import CANCELAR, doc

A = "/api/v1"


def texto(t: str) -> RespuestaLLM:
    return RespuestaLLM(t, [], "modelo-guion", 100, 20, 5)


def llamar(nombre: str, **args) -> RespuestaLLM:
    return RespuestaLLM("", [LlamadaHerramienta(nombre, args, "id1")], "modelo-guion", 100, 10, 5)


class Guion:
    """LLM de mentira: devuelve las respuestas en orden y guarda lo que recibió. Un ErrorNegocio en la cola se lanza."""

    def __init__(self, *respuestas):
        self.cola = list(respuestas)
        self.llamadas: list[tuple] = []

    def chat(self, mensajes, herramientas=None, opciones=None):
        self.llamadas.append((list(mensajes), herramientas, opciones))
        assert self.cola, "El agente llamó al LLM más veces de las previstas"
        r = self.cola.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def cerrar(self):
        pass


@pytest.fixture
def guion(monkeypatch):
    g = Guion()
    monkeypatch.setattr(modulo_cliente, "obtener_cliente", lambda modelo=None: g)
    return g


def iniciar(db, es_prueba=True, canal=CanalConversacion.TEXTO):
    return agente_service.iniciar(db, canal, es_prueba=es_prueba)


def decir(db, conv, mensaje):
    return agente_service.enviar(db, conv.id, mensaje)[1]


def identificar(db, guion, conv, **extra):
    """La identidad se verifica de forma DETERMINISTA (código, no modelo) y la respuesta también es del código: el LLM no interviene."""
    r = decir(db, conv, f"mi cédula es {DOC} y nací el 5 de marzo de 1985")
    assert "Ya verifiqué su identidad" in r.texto and "buscar_paciente" in r.herramientas
    return r


# ======================= saludo =======================
def test_el_saludo_identifica_a_la_institucion_y_al_asistente(db):
    conv, r = iniciar(db)
    assert f"Soy el asistente virtual de {get_settings().institution_name}" in r.texto and r.texto.endswith("¿En qué puedo ayudarle?") and r.intencion == "saludo"
    m = db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id)).one()
    assert m.contenido == r.texto and m.intencion == "saludo"


def test_el_saludo_usa_el_nombre_y_el_mensaje_configurados(db):
    admin = crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    config_ia_service.actualizar(db, admin, {"nombre_asistente": "Sofía"})
    assert f"Soy Sofía, asistente virtual de {get_settings().institution_name}" in iniciar(db)[1].texto
    config_ia_service.actualizar(db, admin, {"mensaje_bienvenida": "Bienvenido a nuestra institución, ¿cómo le ayudo?"})
    assert iniciar(db)[1].texto == "Bienvenido a nuestra institución, ¿cómo le ayudo?"


# ======================= reglas deterministas (sin LLM) =======================
@pytest.mark.parametrize("frase", ["tengo dolor de pecho y no puedo respirar", "mi papá se desmayó", "quiero quitarme la vida"])
def test_una_emergencia_se_atiende_sin_llamar_al_modelo(db, guion, frase):
    conv, _ = iniciar(db)
    r = decir(db, conv, frase)
    assert r.emergencia and r.texto == seguridad.MENSAJE_EMERGENCIA and r.intencion == "emergencia" and guion.llamadas == []
    assert db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id, Mensaje.intencion == "emergencia")).first() is not None


@pytest.mark.parametrize("frase", ["¿qué me puedo tomar para el dolor de cabeza?", "tengo fiebre desde ayer", "¿cuál es la dosis de ibuprofeno?"])
def test_no_se_da_orientacion_medica_ni_se_llama_al_modelo(db, guion, frase):
    conv, _ = iniciar(db)
    r = decir(db, conv, frase)
    assert r.texto == seguridad.MENSAJE_SIN_ORIENTACION_MEDICA and r.intencion == "orientacion_medica" and guion.llamadas == []


def test_pedir_a_una_persona_registra_la_transferencia_y_cierra_los_tramites(db, guion):
    conv, _ = iniciar(db)
    r = decir(db, conv, "quiero hablar con una persona, por favor")
    assert r.transferida and r.intencion == "transferencia" and guion.llamadas == [] and "no puedo prometerle" in r.texto
    s = db.scalars(select(Solicitud).where(Solicitud.conversacion_id == conv.id)).one()
    assert s.tipo == TipoSolicitud.TRANSFERENCIA
    db.refresh(conv)
    assert conv.estado == EstadoConversacion.TRANSFERIDA
    r2 = decir(db, conv, "hola, ¿sigue ahí?")  # ya no atiende trámites
    assert r2.transferida and "ya quedó registrada" in r2.texto and guion.llamadas == []


def test_una_queja_con_motivo_se_registra_sin_depender_del_modelo(db, guion):
    conv, _ = iniciar(db)
    r = decir(db, conv, "quiero poner una queja porque me atendieron muy mal en la recepción de la sede")
    s = db.scalars(select(Solicitud).where(Solicitud.conversacion_id == conv.id)).one()
    assert s.tipo == TipoSolicitud.QUEJA and "me atendieron muy mal" in s.descripcion and guion.llamadas == []
    assert r.intencion == "solicitud" and r.herramientas == ["crear_solicitud"] and f"número {s.id}" in r.texto and "queja" in r.texto


def test_anunciar_una_queja_sin_detalle_deja_que_el_asistente_pregunte(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(texto("Con gusto. ¿Qué ocurrió?"))
    r = decir(db, conv, "quiero poner una queja")
    assert db.scalar(select(Solicitud)) is None and r.texto == "Con gusto. ¿Qué ocurrió?"


def test_texto_vacio_o_muy_largo(db, guion):
    conv, _ = iniciar(db)
    assert decir(db, conv, "   ").intencion == "no_entendido" and guion.llamadas == []
    guion.cola.append(texto("Entendido."))
    decir(db, conv, "a" * 5000)
    ultimo_usuario = [m for m in guion.llamadas[0][0] if m.rol == "user"][-1]
    assert len(ultimo_usuario.contenido) == 800


# ======================= el prompt y lo que ve el modelo =======================
def test_el_modelo_recibe_reglas_fecha_ejemplos_herramientas_y_opciones(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(texto("Con gusto le ayudo."))
    decir(db, conv, "necesito ayuda con algo")
    mensajes, herramientas, opciones = guion.llamadas[0]
    sistema = mensajes[0].contenido
    assert mensajes[0].rol == "system" and "REGLAS" in sistema and "no diagnosticas" in sistema and get_settings().institution_name in sistema
    assert date.today().isoformat() in sistema or datetime.now().date().isoformat() in sistema
    assert "Identidad NO verificada" in sistema and "INFORMACIÓN INSTITUCIONAL" in sistema
    assert any(m.rol == "tool" for m in mensajes) and mensajes[-1].rol == "user" and mensajes[-1].contenido == "necesito ayuda con algo"  # ejemplos + mensaje real al final
    assert len(herramientas) == 15 and opciones.razonamiento is False and opciones.temperatura == get_settings().llm_temperatura


def test_el_estado_de_identidad_llega_al_prompt(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv, _ = iniciar(db)
    identificar(db, guion, conv)
    guion.cola.append(texto("Claro."))
    decir(db, conv, "gracias")
    assert "Identidad verificada: Ana" in guion.llamadas[-1][0][0].contenido


def test_las_cortesias_no_disparan_busqueda_documental_pero_las_preguntas_si(db, guion):
    actor = crear_usuario(db, email="gestor@ejemplo.com", rol=Rol.COORDINADOR)
    doc(db, actor, "Política de citas", CANCELAR)
    conv, _ = iniciar(db)
    guion.cola += [texto("Buenas."), texto("Puede cancelar con la anticipación que defina la institución.")]
    decir(db, conv, "hola")
    assert "Ningún documento institucional" in guion.llamadas[0][0][0].contenido
    r = decir(db, conv, "¿cómo puedo cancelar una cita médica con anticipación?")
    sistema = guion.llamadas[1][0][0].contenido
    assert "[1] Política de citas" in sistema and "anticipación" in sistema
    assert any(m.rol == "tool" and '"configurada": false' in m.contenido for m in guion.llamadas[1][0])  # además se consultó la política (no hay ninguna) y se le entregó al modelo
    assert r.intencion == "informacion" and r.fuentes and r.fuentes[0]["titulo"] == "Política de citas"
    assert db.scalars(select(MensajeFuente)).all()


def test_sin_documento_relevante_el_prompt_indica_que_no_hay_informacion(db, guion):
    actor = crear_usuario(db, email="gestor@ejemplo.com", rol=Rol.COORDINADOR)
    doc(db, actor, "Política de citas", CANCELAR)
    conv, _ = iniciar(db)
    guion.cola.append(texto("No tengo esa información."))
    r = decir(db, conv, "receta tradicional de arepas con queso")
    assert "Ningún documento institucional" in guion.llamadas[0][0][0].contenido and r.fuentes == []


def test_si_los_embeddings_caen_el_asistente_sigue_funcionando(db, guion, embedder_falso):
    embedder_falso.caido = True
    conv, _ = iniciar(db)
    guion.cola.append(texto("Con gusto."))
    assert decir(db, conv, "¿qué requisitos hay?").texto == "Con gusto."


# ======================= herramientas dentro del bucle =======================
def test_el_bucle_ejecuta_la_herramienta_y_responde_con_su_resultado(db, guion):
    con_agenda(db)
    conv, _ = iniciar(db)
    guion.cola += [llamar("consultar_especialidades"), texto("Atendemos Medicina General.")]
    r = decir(db, conv, "quiero saber qué opciones hay por aquí")  # no activa la consulta previa: el modelo decide pedir la herramienta
    assert r.texto == "Atendemos Medicina General." and r.herramientas == ["consultar_especialidades"] and r.intencion == "consulta_catalogo"
    segunda = guion.llamadas[1][0]
    assert segunda[-2].rol == "assistant" and segunda[-2].llamadas and segunda[-1].rol == "tool" and "Medicina General" in segunda[-1].contenido
    e = db.scalars(select(EjecucionHerramienta)).one()
    assert e.nombre == "consultar_especialidades" and e.resultado == ResultadoHerramienta.EXITO and e.mensaje_id is not None


def test_las_especialidades_se_dicen_con_datos_del_sistema_sin_llamar_al_modelo(db, guion):
    con_agenda(db)
    conv, _ = iniciar(db)
    r = decir(db, conv, "¿qué especialidades tienen?")
    assert r.texto.startswith("Atendemos Medicina General") and "¿Para cuál le gustaría una cita?" in r.texto
    assert r.herramientas == ["consultar_especialidades"] and r.intencion == "consulta_catalogo" and guion.llamadas == []
    assert db.scalars(select(EjecucionHerramienta)).one().nombre == "consultar_especialidades"


def test_las_consultas_de_catalogo_se_traen_antes_de_responder_y_se_le_dan_al_modelo(db, guion):
    con_agenda(db)
    conv, _ = iniciar(db)
    guion.cola.append(texto("Atiende de ocho a doce."))
    r = decir(db, conv, "¿cuáles son los horarios de medicina general?")
    mensajes = guion.llamadas[0][0]
    # el resultado llega como un intercambio real de herramienta, justo después de la pregunta (no en el prompt de sistema, donde el modelo lo ignoraba)
    assert mensajes[-3].rol == "user" and mensajes[-2].llamadas[0].nombre == "consultar_horarios" and mensajes[-1].rol == "tool" and "Medicina General" in mensajes[-1].contenido
    assert r.herramientas == ["consultar_horarios"] and r.intencion == "consulta_catalogo" and len(guion.llamadas) == 1
    assert db.scalars(select(EjecucionHerramienta)).one().nombre == "consultar_horarios"


def test_pedir_una_cita_primero_pide_la_identidad_y_recuerda_lo_dicho(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv, _ = iniciar(db)
    r = decir(db, conv, "quiero una cita de medicina general el lunes 4 de marzo de 2030")
    assert r.texto == PEDIR_IDENTIDAD and guion.llamadas == []  # la especialidad y la fecha NO se pierden: se retoman al identificarse
    r = identificar(db, guion, conv)
    assert "Para Medicina General tengo estos horarios" in r.texto and "Opción uno" in r.texto and "8:00 a. m." in r.texto and "¿Cuál prefiere?" in r.texto
    assert guion.llamadas == [] and r.herramientas == ["buscar_paciente", "consultar_disponibilidad"]
    consulta = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "consultar_disponibilidad")).one()
    assert consulta.parametros == {"especialidad": "Medicina General", "desde": "2030-03-04", "hasta": "2030-03-04"}


def test_sin_fecha_se_buscan_los_proximos_dias(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv, _ = iniciar(db)
    decir(db, conv, "quiero una cita de medicina general")
    r = identificar(db, guion, conv)
    consulta = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "consultar_disponibilidad")).one()
    hoy = datetime.now().date()
    assert consulta.parametros["desde"] == hoy.isoformat() and (date.fromisoformat(consulta.parametros["hasta"]) - hoy).days == 6
    assert "Para Medicina General tengo estos horarios" in r.texto and "Opción uno" in r.texto  # el horario semanal de la prueba tiene cupos dentro de esos días


def test_las_ejecuciones_se_registran_sin_datos_personales(db, guion):
    paciente_real(db)
    conv, _ = iniciar(db)
    identificar(db, guion, conv)
    e = db.scalars(select(EjecucionHerramienta).where(EjecucionHerramienta.nombre == "buscar_paciente")).one()
    assert e.parametros == {"tipo_documento": "CC", "numero_documento": "[omitido]", "fecha_nacimiento": "[omitido]"} and DOC not in str(e.parametros)
    assert guion.llamadas == [] and DOC not in str([m.contenido for m in db.scalars(select(Mensaje)) if m.contenido and "cédula es" not in m.contenido])  # el modelo ni interviene
    db.refresh(conv)
    assert conv.identidad_verificada and conv.paciente_id is not None


def test_una_herramienta_rechazada_se_le_explica_al_modelo_para_que_corrija(db, guion):
    conv, _ = iniciar(db)
    guion.cola += [llamar("agendar_cita", cupo=1), texto("Primero necesito su documento y fecha de nacimiento. ¿Me los indica?")]
    r = decir(db, conv, "dime la del lunes")
    resultado = guion.llamadas[1][0][-1].contenido
    assert "identidad_no_verificada" in resultado and "buscar_paciente" in resultado and r.requiere_confirmacion is False
    assert db.scalars(select(EjecucionHerramienta)).one().resultado == ResultadoHerramienta.RECHAZADA


@pytest.mark.parametrize("llamada", [llamar("ejecutar_sql", consulta="DROP TABLE pacientes"), llamar("consultar_citas", paciente_id=1), llamar("cancelar_cita", codigo_reserva="X", motivo=3, extra="y")])
def test_el_modelo_no_puede_ejecutar_lo_que_no_esta_permitido(db, guion, llamada):
    conv, _ = iniciar(db)
    guion.cola += [llamada, texto("No pude hacerlo.")]
    decir(db, conv, "haz algo raro")
    assert '"ok": false' in guion.llamadas[1][0][-1].contenido
    assert db.scalars(select(EjecucionHerramienta)).one().resultado == ResultadoHerramienta.RECHAZADA


def test_el_bucle_se_detiene_tras_el_maximo_de_pasos(db, guion):
    conv, _ = iniciar(db)
    guion.cola += [llamar("consultar_especialidades")] * (MAX_PASOS + 2)
    r = decir(db, conv, "dime las especialidades siempre")
    assert len(guion.llamadas) == MAX_PASOS and "pasarlo con una persona" in r.texto


def test_si_el_modelo_falla_se_responde_con_honestidad_y_la_conversacion_sigue(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(ErrorNegocio("llm_no_disponible", "El modelo de lenguaje no está disponible: sin conexión.", 503))
    r = decir(db, conv, "necesito información")
    assert r.intencion == "error_agente" and "problema técnico" in r.texto
    db.refresh(conv)
    assert conv.estado == EstadoConversacion.ACTIVA
    guion.cola.append(texto("Ya estoy de vuelta."))
    assert decir(db, conv, "¿hola?").texto == "Ya estoy de vuelta."


def test_se_bloquea_una_respuesta_que_da_orientacion_medica(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(texto("Le recomiendo tomar 500 mg de acetaminofén cada ocho horas."))
    r = decir(db, conv, "necesito información")
    assert r.texto == seguridad.MENSAJE_SIN_ORIENTACION_MEDICA and r.intencion == "orientacion_medica"


def test_la_respuesta_se_limpia_para_voz(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(texto("**Claro**, estas son las opciones:\n- Opción uno\n- Opción dos\n# Fin"))
    r = decir(db, conv, "¿qué hay?")
    assert "*" not in r.texto and "#" not in r.texto and "\n" not in r.texto and r.texto.startswith("Claro")
    guion.cola.append(texto("palabra " * 300))
    assert len(decir(db, conv, "háblame mucho").texto) <= 701


# ======================= confirmación antes de modificar =======================
def preparar_reserva(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv, _ = iniciar(db)
    identificar(db, guion, conv)
    guion.cola += [texto("Hay cupo a las 8, a las 10 y a las 11 y 40 de la mañana. ¿Cuál prefiere?")]  # la disponibilidad ya vino precargada
    decir(db, conv, "quiero una cita de medicina general el lunes 4 de marzo de 2030")
    r = decir(db, conv, "la segunda")  # elegir un horario es determinista: queda pendiente de confirmación sin pasar por el modelo
    return conv, r


def test_agendar_pide_confirmacion_y_solo_se_ejecuta_con_un_si(db, guion):
    conv, r = preparar_reserva(db, guion)
    assert r.requiere_confirmacion and "¿Lo confirma?" in r.texto and "10:00" in r.texto and db.scalar(select(Cita)) is None  # todavía NO se agendó
    llamadas_antes = len(guion.llamadas)
    r = decir(db, conv, "sí, confirmo")
    assert len(guion.llamadas) == llamadas_antes  # la ejecución es determinista: no se llama al modelo
    cita = db.scalar(select(Cita))
    assert cita is not None and cita.codigo_reserva and r.herramientas == ["agendar_cita"] and r.intencion == "agendar_cita"
    assert "Listo, su cita quedó agendada para el lunes 4 de marzo a las 10:00" in r.texto and " ".join(cita.codigo_reserva) in r.texto and cita.conversacion_id == conv.id


def test_un_no_cancela_la_confirmacion_pendiente(db, guion):
    conv, _ = preparar_reserva(db, guion)
    r = decir(db, conv, "no")
    assert "no hice ningún cambio" in r.texto and db.scalar(select(Cita)) is None
    guion.cola.append(texto("¿Qué desea confirmar?"))
    decir(db, conv, "sí")  # un «sí» posterior ya no ejecuta nada: llega al modelo como cualquier mensaje
    assert db.scalar(select(Cita)) is None


def test_decir_otra_cosa_descarta_la_confirmacion_pendiente(db, guion):
    conv, _ = preparar_reserva(db, guion)
    guion.cola += [texto("Claro, ¿qué día prefiere?"), texto("¿Qué desea confirmar?")]
    decir(db, conv, "mejor el martes")
    assert db.scalar(select(Cita)) is None
    decir(db, conv, "sí")  # la confirmación anterior ya no vale
    assert db.scalar(select(Cita)) is None


def test_un_si_con_condiciones_no_cuenta_como_confirmacion(db, guion):
    conv, _ = preparar_reserva(db, guion)
    guion.cola.append(texto("Entendido, ¿a qué hora prefiere?"))
    decir(db, conv, "sí pero a las nueve")
    assert db.scalar(select(Cita)) is None


def test_el_modelo_ve_la_reserva_hecha_en_los_turnos_siguientes(db, guion):
    conv, _ = preparar_reserva(db, guion)
    decir(db, conv, "sí")
    guion.cola.append(texto("Su código quedó en la confirmación."))
    decir(db, conv, "gracias, ¿me repite el código?")
    historial = " ".join(m.contenido for m in guion.llamadas[-1][0] if m.rol == "assistant")
    assert "Listo, su cita quedó agendada" in historial


def test_confirmacion_cuando_el_cupo_se_ocupa_antes_de_confirmar(db, guion):
    from app.models import Profesional
    from app.models.enums import CanalCita
    from app.services import cita_service
    from tests.test_agenda import local

    conv, _ = preparar_reserva(db, guion)
    otro = paciente_real(db, doc="7777777", nacimiento=date(1992, 2, 2), nombres="Luis")
    prof = db.scalar(select(Profesional))
    # otra persona toma el cupo de las 10:00 justo antes de que este paciente confirme
    cita_service.agendar(db, paciente_id=otro.id, profesional_id=prof.id, especialidad_id=prof.especialidades[0].id, inicio=local(10),
                         actor=cita_service.Actor(cita_service.ActorTipo.USUARIO, None, False), canal=CanalCita.WEB)
    r = decir(db, conv, "sí")
    assert "acaba de ocuparse" in r.texto
    assert [c.paciente_id for c in db.scalars(select(Cita))] == [otro.id]  # solo quedó la cita de la otra persona


def test_cancelar_tambien_exige_confirmacion_y_no_toca_citas_ajenas(db, guion):
    conv, _ = preparar_reserva(db, guion)
    decir(db, conv, "sí")
    cita = db.scalar(select(Cita))
    llamadas_antes = len(guion.llamadas)
    r = decir(db, conv, f"quiero cancelar mi cita {cita.codigo_reserva}")  # por código: determinista, sin modelo
    assert r.requiere_confirmacion and "Cancelar la cita" in r.texto and "¿Lo confirma?" in r.texto and len(guion.llamadas) == llamadas_antes
    db.refresh(cita)
    assert cita.estado.value == "PROGRAMADA"
    r = decir(db, conv, "sí")
    db.refresh(cita)
    assert cita.estado.value == "CANCELADA" and "quedó cancelada" in r.texto and len(guion.llamadas) == llamadas_antes
    # un código que no es suyo (o no existe): misma respuesta, sin revelar nada, sin confirmación pendiente
    r = decir(db, conv, "cancela la ZZZZ9999")
    assert r.requiere_confirmacion is False and "No encontré una cita suya con ese código" in r.texto


def test_cancelar_o_agendar_sin_identificarse_pide_la_identidad(db, guion):
    con_agenda(db)
    conv, _ = iniciar(db)
    r = decir(db, conv, "quiero cancelar mi cita AB23CD45")
    assert "verificar su identidad" in r.texto and r.requiere_confirmacion is False and guion.llamadas == []
    r = decir(db, conv, "quiero una cita de medicina general el lunes 4 de marzo de 2030")
    assert "verificar su identidad" in r.texto and r.requiere_confirmacion is False and guion.llamadas == []


def test_la_cita_se_agenda_hablando_de_principio_a_fin_sin_el_modelo(db, guion):
    con_agenda(db)
    paciente_real(db)
    conv, _ = iniciar(db)
    decir(db, conv, "quiero una cita de medicina general el lunes 4 de marzo de 2030")
    r = identificar(db, guion, conv)  # se identifica y se le ofrecen los horarios
    assert "Opción uno" in r.texto
    r = decir(db, conv, "la primera")
    assert r.requiere_confirmacion and "8:00" in r.texto and "¿Lo confirma?" in r.texto
    assert db.scalar(select(Cita)) is None
    decir(db, conv, "sí")
    assert db.scalar(select(Cita)).inicio.astimezone().hour in (8, 13)  # 8:00 hora de Bogotá


def test_reprogramar_por_codigo_y_elegir_el_nuevo_horario(db, guion):
    b = con_agenda(db)
    ana = paciente_real(db)
    conv, _ = iniciar(db)
    identificar(db, guion, conv)
    from app.models.enums import CanalCita
    from tests.test_agenda import local

    cita = cita_service.agendar(db, paciente_id=ana.id, profesional_id=b.prof1.id, especialidad_id=b.esp.id, inicio=local(8),  # por el servicio: el código real es de 8 caracteres
                                actor=cita_service.Actor(cita_service.ActorTipo.USUARIO, None, False), canal=CanalCita.WEB)
    guion.cola.append(texto("Claro, ¿para qué fecha quiere moverla?"))
    r = decir(db, conv, f"quiero reprogramar mi cita {cita.codigo_reserva}")
    assert r.requiere_confirmacion is False
    guion.cola.append(texto("Tengo a las 8:20, a las 10 y a las 11 y 40."))
    decir(db, conv, "para el lunes 4 de marzo de 2030")
    assert guion.llamadas[-1][0][-2].llamadas[0].nombre == "consultar_disponibilidad"  # usó la especialidad de la propia cita
    r = decir(db, conv, "la segunda")
    assert r.requiere_confirmacion and "Mover la cita" in r.texto
    r = decir(db, conv, "sí")
    db.refresh(cita)
    assert cita.reprogramaciones == 1 and cita.inicio == local(10) and "quedó para el lunes 4 de marzo a las 10:00" in r.texto


def test_el_llm_no_puede_decir_que_ya_hizo_algo_ni_que_identifico_a_alguien(db, guion):
    conv, _ = iniciar(db)
    guion.cola.append(texto("Hola Ana, ya la identifiqué. ¿En qué le ayudo?"))
    r = decir(db, conv, "quiero ayuda con mis citas")
    assert "verificar su identidad" in r.texto and "identifiqué" not in r.texto
    guion.cola.append(texto("Listo, su cita quedó cancelada."))
    assert "Todavía no he realizado ningún cambio" in decir(db, conv, "cuéntame algo").texto
    guion.cola.append(texto("Voy a cancelar su cita. ¿Lo confirma?"))
    assert "Todavía no he realizado ningún cambio" in decir(db, conv, "dime algo más").texto  # pidió confirmar algo que no está pendiente


# ======================= registro de pacientes por el asistente =======================
def test_registro_por_el_asistente_exige_politica_configurada_y_confirmacion(db, guion):
    conv, _ = iniciar(db)
    registro = dict(tipo_documento="CC", numero_documento="5544332211", nombres="Carlos", apellidos="Ficticio Nuevo", fecha_nacimiento="1990-07-20", autoriza_tratamiento_datos=True)
    guion.cola += [llamar("registrar_paciente", **registro), texto("¿Autoriza a la institución el tratamiento de sus datos personales y confirma el registro?")]
    r = decir(db, conv, "soy nuevo, quiero registrarme; autorizo el tratamiento de mis datos")
    assert r.requiere_confirmacion
    r = decir(db, conv, "sí")  # sin versión de política configurada no se registra nada
    assert "no está habilitado" in r.texto and "persona" in r.texto
    from app.models import Paciente
    assert db.scalar(select(Paciente)) is None


def test_registro_habilitado_crea_al_paciente_con_su_consentimiento(db, guion):
    admin = crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    config_ia_service.actualizar(db, admin, {"politica_datos_version": "v-2026"})
    conv, _ = iniciar(db)
    registro = dict(tipo_documento="CC", numero_documento="5544332211", nombres="Carlos", apellidos="Ficticio Nuevo", fecha_nacimiento="1990-07-20", autoriza_tratamiento_datos=True)
    guion.cola += [llamar("registrar_paciente", **registro), texto("¿Autoriza el tratamiento de sus datos y confirma el registro?")]
    decir(db, conv, "soy nuevo, quiero registrarme; autorizo el tratamiento de mis datos")
    r = decir(db, conv, "sí")
    assert "quedó registrado" in r.texto and "Carlos" in r.texto
    from app.models import ConsentimientoDatos, Paciente
    assert db.scalar(select(Paciente)).nombres == "Carlos" and db.scalar(select(ConsentimientoDatos)).politica_version == "v-2026"


# ======================= persistencia y privacidad =======================
def test_conversaciones_reales_no_guardan_el_texto_sin_autorizacion(db, guion):
    conv, saludo = iniciar(db, es_prueba=False)
    assert conv.guarda_transcripcion is False
    guion.cola.append(texto("Con gusto."))
    decir(db, conv, "hola, me llamo Ana y mi correo es ana@ejemplo.com")
    msgs = db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id).order_by(Mensaje.id)).all()
    assert len(msgs) == 3 and all(m.contenido is None for m in msgs) and msgs[1].rol.value == "PACIENTE"


def test_las_conversaciones_de_prueba_si_guardan_el_texto(db, guion):
    conv, _ = iniciar(db, es_prueba=True)
    guion.cola.append(texto("Con gusto."))
    decir(db, conv, "hola")
    assert [m.contenido for m in db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id).order_by(Mensaje.id))][1:] == ["hola", "Con gusto."]


def test_el_texto_se_guarda_desde_que_el_paciente_autoriza_la_transcripcion(db, guion):
    con_agenda(db)
    paciente_real(db, transcripcion=True)
    conv, _ = iniciar(db, es_prueba=False)
    identificar(db, guion, conv)
    db.refresh(conv)
    assert conv.guarda_transcripcion is True
    guion.cola.append(texto("Claro."))
    decir(db, conv, "quiero saber un horario")
    ultimos = db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id).order_by(Mensaje.id.desc()).limit(2)).all()
    assert all(m.contenido for m in ultimos)
    primeros = db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id).order_by(Mensaje.id).limit(2)).all()
    assert all(m.contenido is None for m in primeros[1:])  # lo anterior a la autorización no se guardó


def test_la_intencion_de_cada_mensaje_queda_registrada(db, guion):
    con_agenda(db)
    conv, _ = iniciar(db)
    guion.cola += [texto("Medicina General."), texto("Con gusto.")]
    decir(db, conv, "¿qué especialidades hay?")
    decir(db, conv, "tengo dolor de pecho")
    pacientes = [m.intencion for m in db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv.id, Mensaje.rol == "PACIENTE").order_by(Mensaje.id))]
    assert pacientes == ["consulta_catalogo", "emergencia"]


# ======================= sesiones y API =======================
def test_sesion_expirada_o_conversacion_cerrada(db, guion):
    conv, _ = iniciar(db)
    SESIONES.quitar(conv.id)
    with pytest.raises(ErrorNegocio) as e:
        agente_service.enviar(db, conv.id, "hola")
    assert e.value.codigo == "sesion_expirada" and e.value.status == 409
    conv2, _ = iniciar(db)
    agente_service.finalizar(db, conv2.id)
    with pytest.raises(ErrorNegocio) as e:
        agente_service.enviar(db, conv2.id, "hola")
    assert e.value.codigo == "conversacion_cerrada"
    with pytest.raises(ErrorNegocio) as e:
        agente_service.enviar(db, uuid.uuid4(), "hola")
    assert e.value.codigo == "conversacion_no_encontrada"


def test_api_conversacion_completa_y_permisos(client, db, guion):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    h, hr = cabecera(client, "coord@ejemplo.com"), cabecera(client, "rec@ejemplo.com")
    assert client.post(f"{A}/agente/conversaciones", headers=hr, json={}).status_code == 403
    assert client.post(f"{A}/agente/conversaciones", json={}).status_code == 401
    r = client.post(f"{A}/agente/conversaciones", headers=h, json={})
    assert r.status_code == 201 and get_settings().institution_name in r.json()["texto"]
    cid = r.json()["conversacion_id"]
    guion.cola.append(texto("Con gusto le ayudo."))
    m = client.post(f"{A}/agente/conversaciones/{cid}/mensajes", headers=h, json={"texto": "necesito ayuda"})
    assert m.status_code == 200 and m.json()["texto"] == "Con gusto le ayudo." and m.json()["intencion"] == "conversacion"
    assert client.post(f"{A}/agente/conversaciones/{cid}/mensajes", headers=hr, json={"texto": "x"}).status_code == 403
    assert client.post(f"{A}/agente/conversaciones/{cid}/mensajes", headers=h, json={"texto": "   "}).status_code == 422
    assert client.post(f"{A}/agente/conversaciones/{cid}/mensajes", headers=h, json={"texto": "a" * 1001}).status_code == 422
    t = client.get(f"{A}/agente/conversaciones/{cid}", headers=h).json()
    assert [x["rol"] for x in t] == ["AGENTE", "PACIENTE", "AGENTE"] and t[1]["contenido"] == "necesito ayuda"
    assert client.post(f"{A}/agente/conversaciones/{cid}/finalizar", headers=h).status_code == 204
    assert client.post(f"{A}/agente/conversaciones/{cid}/mensajes", headers=h, json={"texto": "hola"}).json()["error"]["codigo"] == "conversacion_cerrada"
    assert client.post(f"{A}/agente/conversaciones/{uuid.uuid4()}/mensajes", headers=h, json={"texto": "hola"}).status_code == 404


def test_las_conversaciones_de_prueba_quedan_marcadas(client, db):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    cid = client.post(f"{A}/agente/conversaciones", headers=h, json={}).json()["conversacion_id"]
    from app.models import Conversacion
    assert db.get(Conversacion, uuid.UUID(cid)).es_prueba is True


def test_los_ejemplos_del_prompt_usan_herramientas_que_existen_con_argumentos_validos():
    from app.tools import REGISTRO

    for m in prompts.ejemplos():
        for ll in m.llamadas:
            h, args, rechazo = REGISTRO.preparar(type("C", (), {"identidad_verificada": True})(), ll.nombre, ll.argumentos)
            assert rechazo is None, (ll.nombre, ll.argumentos)
    assert horario and EstadoConversacion

"""Fase 14: interfaz de voz por WebSocket. Protocolo, seguridad, turnos, interrupción y cierre con STT/TTS/agente falsos; y un recorrido real al final."""
import io
import json
import re
import shutil
import subprocess
import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.agents.recepcionista import Respuesta
from app.agents.sesiones import SESIONES
from app.core.config import get_settings
from app.models import AuditoriaLog, Conversacion
from app.models.enums import CanalConversacion, EstadoConversacion, Rol
from app.services import agente_service, conversacion_service
from app.stt import transcriptor as tr
from app.stt.transcriptor import ResultadoSTT
from app.tts import servicio as modulo_tts
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_tts import MotorFalso, servicio as servicio_tts

URL = "/api/v1/voz/ws"


def pcm(segundos: float, amplitud: float = 0.3) -> bytes:
    t = np.arange(int(16000 * segundos)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * amplitud * 32767).astype("<i2").tobytes()


def stt_resultado(texto="", confianza=0.8, silencio=False, segundos=2.0) -> ResultadoSTT:
    return ResultadoSTT(texto=texto, confianza=confianza, silencio=silencio, motivo="silencio" if silencio else None, baja_confianza=bool(texto) and confianza < 0.35,
                        duracion_audio_s=segundos, duracion_proceso_ms=300, dispositivo="cpu")


class STTGuion:
    """Transcriptor de mentira: devuelve los resultados en orden y recuerda lo que recibió."""

    def __init__(self, *resultados):
        self.cola, self.recibido = list(resultados), []

    def transcribir(self, datos, formato="auto", idioma=None):
        self.recibido.append((len(datos), formato))
        return self.cola.pop(0)


class MotorLento(MotorFalso):
    """Cada frase tarda un poco: da tiempo a interrumpir en medio de una respuesta."""

    def sintetizar(self, texto, velocidad):
        time.sleep(0.25)
        return super().sintetizar(texto, velocidad)


class Sesion:
    """Envoltura del cliente de pruebas con lectura por eventos."""

    def __init__(self, ws):
        self.ws, self.eventos, self.audios = ws, [], []

    def enviar(self, **m):
        self.ws.send_json(m)

    def leer(self):
        e = self.ws.receive_json()
        if e["tipo"] == "audio":
            self.audios.append((e, self.ws.receive_bytes()))
        self.eventos.append(e)
        return e

    def hasta(self, tipo, **filtro):
        while True:
            e = self.leer()
            if e["tipo"] == tipo and all(e.get(k) == v for k, v in filtro.items()):
                return e

    def hasta_fin_de_respuesta(self):
        """Hasta el aviso de fin de respuesta y el «escuchando» que siempre le sigue (para no dejar eventos sueltos)."""
        fin = self.hasta("fin_respuesta")
        assert self.leer() == {"tipo": "estado", "valor": "escuchando"}
        return fin

    def tipos(self):
        return [e["tipo"] for e in self.eventos]


@pytest.fixture
def entorno(client, db, monkeypatch):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    motor = MotorFalso("piper")
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(motor))
    stt = STTGuion()
    monkeypatch.setattr(tr, "obtener_transcriptor", lambda: stt)
    s = SimpleNamespace(client=client, db=db, motor=motor, stt=stt, token=cabecera(client, "coord@ejemplo.com")["Authorization"].split()[1],
                        token_rec=cabecera(client, "rec@ejemplo.com")["Authorization"].split()[1])
    return s


@pytest.fixture
def llamada(entorno):
    """Una llamada ya autenticada y con el saludo recibido por completo."""
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.listo = s.hasta("listo")
        s.hasta_fin_de_respuesta()
        s.eventos.clear()
        s.audios.clear()
        yield s


def conversacion(entorno) -> Conversacion:
    entorno.db.expire_all()
    return entorno.db.scalars(select(Conversacion).order_by(Conversacion.iniciada_en.desc())).first()


def esperar(condicion, segundos=10.0):
    fin = time.monotonic() + segundos
    while time.monotonic() < fin:
        if condicion():
            return True
        time.sleep(0.05)
    return False


# ======================= seguridad de la conexión =======================
def test_el_primer_mensaje_debe_ser_la_autenticacion(entorno):
    with entorno.client.websocket_connect(URL) as ws:
        ws.send_json({"tipo": "texto", "texto": "hola"})
        assert ws.receive_json()["codigo"] == "no_autenticado"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 1008
    assert conversacion(entorno) is None


@pytest.mark.parametrize("token", ["no-es-un-token", "", 123])
def test_un_token_invalido_no_abre_la_llamada(entorno, token):
    with entorno.client.websocket_connect(URL) as ws:
        ws.send_json({"tipo": "auth", "token": token})
        assert ws.receive_json()["tipo"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert conversacion(entorno) is None


def test_sin_permiso_no_se_abre_y_queda_auditado(entorno):
    crear_usuario(entorno.db, email="solo.lee@ejemplo.com", rol=Rol.CONSULTA)  # un rol sin ningún permiso del asistente
    token = cabecera(entorno.client, "solo.lee@ejemplo.com")["Authorization"].split()[1]
    with entorno.client.websocket_connect(URL) as ws:
        ws.send_json({"tipo": "auth", "token": token})
        assert ws.receive_json()["codigo"] == "sin_permiso"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert entorno.db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "acceso_denegado")).one().detalle["canal"] == "voz"


def test_no_espera_para_siempre_a_que_se_autentique(entorno, monkeypatch):
    monkeypatch.setattr(get_settings(), "voz_espera_auth_s", 0.3)
    with entorno.client.websocket_connect(URL) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 1008


def test_un_origen_ajeno_es_rechazado_pero_el_propio_pasa(entorno):
    with pytest.raises(WebSocketDisconnect):
        with entorno.client.websocket_connect(URL, headers={"origin": "https://sitio-malo.example"}):
            pass
    with entorno.client.websocket_connect(URL, headers={"origin": "http://testserver"}) as ws:
        ws.send_json({"tipo": "auth", "token": "x"})
        assert ws.receive_json()["tipo"] == "error"  # llegó hasta la autenticación


def test_un_token_en_la_url_no_sirve_de_nada(entorno, monkeypatch):
    """El token solo se acepta dentro del primer mensaje: en la URL quedaría en los registros de acceso."""
    monkeypatch.setattr(get_settings(), "voz_espera_auth_s", 0.3)
    with entorno.client.websocket_connect(f"{URL}?token={entorno.token}") as ws:
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert conversacion(entorno) is None


def test_quien_atiende_pacientes_abre_una_conversacion_real_y_quien_solo_prueba_no(entorno):
    """«real» solo lo concede el permiso de atender; un coordinador/recepcionista la abre real, y pedir «real» sin permiso se ignora."""
    for token, pide_real, esperado_prueba in ((entorno.token_rec, True, False), (entorno.token, False, True), (entorno.token, True, False)):
        with entorno.client.websocket_connect(URL) as ws:
            s = Sesion(ws)
            s.enviar(tipo="auth", token=token, real=pide_real)
            listo = s.hasta("listo")
            assert listo["es_prueba"] is esperado_prueba
            s.hasta_fin_de_respuesta()
            s.enviar(tipo="finalizar")
            s.hasta("cerrada")
        entorno.db.expire_all()
        guardada = entorno.db.get(Conversacion, uuid.UUID(listo["conversacion_id"]))  # por id: dentro de una prueba todas comparten la misma hora de creación
        assert guardada.es_prueba is esperado_prueba and guardada.canal == CanalConversacion.VOZ


# ======================= el saludo y los mensajes de texto =======================
def test_al_conectar_llega_el_saludo_hablado(entorno):
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        listo = s.hasta("listo")
        assert listo["entrada"] == {"formato": "pcm16", "frecuencia": 16000, "canales": 1} and listo["max_segundos"] == 30 and listo["es_prueba"] is True
        respuesta = s.hasta("respuesta")
        assert respuesta["intencion"] == "saludo" and "asistente" in respuesta["texto"].lower()
        s.hasta_fin_de_respuesta()
        assert s.tipos()[:3] == ["listo", "respuesta", "estado"] and s.eventos[2]["valor"] == "hablando"
        assert s.audios, "el saludo debe llegar con audio"
        for cabecera_audio, wav_bytes in s.audios:
            with wave.open(io.BytesIO(wav_bytes)) as w:
                assert w.getframerate() == cabecera_audio["frecuencia"] == 22050 and w.getnchannels() == 1
        assert [c["indice"] for c, _ in s.audios] == list(range(len(s.audios)))
        assert " ".join(c["frase"] for c, _ in s.audios).split()[:3] == respuesta["texto"].split()[:3]
    conv = conversacion(entorno)
    assert conv.canal == CanalConversacion.VOZ and conv.es_prueba is True


def test_un_mensaje_de_texto_se_responde_con_voz(llamada, entorno):
    llamada.enviar(tipo="texto", texto="Necesito hablar con una persona")
    r = llamada.hasta("respuesta")
    assert r["transferida"] is True and r["intencion"] == "transferencia"
    llamada.hasta_fin_de_respuesta()
    assert llamada.audios and "pensando" in [e.get("valor") for e in llamada.eventos if e["tipo"] == "estado"]


def test_la_emergencia_se_dice_completa_con_el_123_pronunciable(llamada):
    llamada.enviar(tipo="texto", texto="Mi papá se desmayó y no respira")
    r = llamada.hasta("respuesta")
    assert r["emergencia"] is True and "123" in r["texto"]
    llamada.hasta_fin_de_respuesta()
    dicho = " ".join(c["frase"] for c, _ in llamada.audios)
    assert "uno dos tres" in dicho and "123" not in dicho  # los motores leerían «ciento veintitrés»


@pytest.mark.parametrize("mensaje, codigo", [({"tipo": "texto", "texto": ""}, "texto_invalido"), ({"tipo": "texto", "texto": "x" * 1001}, "texto_invalido"),
                                              ({"tipo": "texto", "texto": 5}, "texto_invalido"), ({"tipo": "inventado"}, "mensaje_desconocido"), ({"sin_tipo": 1}, "mensaje_desconocido")])
def test_mensajes_invalidos_dan_error_y_la_llamada_sigue(llamada, mensaje, codigo):
    llamada.ws.send_json(mensaje)
    assert llamada.leer()["codigo"] == codigo
    llamada.enviar(tipo="ping")
    assert llamada.leer() == {"tipo": "pong"}


def test_json_roto_o_gigante_no_tumba_la_llamada(llamada):
    llamada.ws.send_text("{esto no es json")
    assert llamada.leer()["codigo"] == "mensaje_invalido"
    llamada.ws.send_text("[1, 2]")
    assert llamada.leer()["codigo"] == "mensaje_invalido"
    llamada.ws.send_text('{"tipo":"ping","relleno":"' + "x" * 5000 + '"}')
    assert llamada.leer()["codigo"] == "mensaje_invalido"
    llamada.enviar(tipo="ping")
    assert llamada.leer()["tipo"] == "pong"


# ======================= turnos de audio =======================
def test_audio_de_la_voz_al_asistente_y_de_vuelta_a_la_voz(llamada, entorno):
    entorno.stt.cola.append(stt_resultado("Quiero hablar con una persona"))
    for trozo in (pcm(0.5), pcm(0.5), pcm(0.5)):  # fragmentos como los que manda el micrófono
        llamada.ws.send_bytes(trozo)
    llamada.enviar(tipo="fin_audio")
    t = llamada.hasta("transcripcion")
    assert t["texto"] == "Quiero hablar con una persona" and t["silencio"] is False and t["baja_confianza"] is False
    r = llamada.hasta("respuesta")
    assert r["transferida"] is True
    llamada.hasta_fin_de_respuesta()
    assert entorno.stt.recibido == [(48000, "pcm16")]  # 1.5 s de PCM16 a 16 kHz, interpretado como PCM crudo
    assert [e.get("valor") for e in llamada.eventos if e["tipo"] == "estado"][:3] == ["transcribiendo", "pensando", "hablando"]
    assert llamada.audios


def test_el_audio_con_numero_impar_de_bytes_no_rompe_nada(llamada, entorno):
    entorno.stt.cola.append(stt_resultado("Necesito hablar con una persona"))
    llamada.ws.send_bytes(pcm(1.0) + b"\x01")
    llamada.enviar(tipo="fin_audio")
    llamada.hasta("respuesta", intencion="transferencia")
    assert entorno.stt.recibido == [(32000, "pcm16")]


def test_fin_de_audio_sin_audio_no_hace_nada(llamada, entorno):
    llamada.enviar(tipo="fin_audio")
    llamada.enviar(tipo="ping")
    assert llamada.leer()["tipo"] == "pong" and entorno.stt.recibido == []


@pytest.mark.parametrize("resultado", [stt_resultado("", 0.0, silencio=True), stt_resultado("hmm", 0.1), stt_resultado("   ", 0.9)])
def test_si_no_se_entendio_se_pide_repetir_y_el_asistente_no_actua(llamada, entorno, resultado):
    entorno.stt.cola.append(resultado)
    llamada.ws.send_bytes(pcm(1.0))
    llamada.enviar(tipo="fin_audio")
    r = llamada.hasta("respuesta")
    assert r["intencion"] == "no_entendido" and "repetirlo" in r["texto"] and not r["herramientas"]
    llamada.hasta_fin_de_respuesta()
    assert llamada.audios and "pensando" not in [e.get("valor") for e in llamada.eventos if e["tipo"] == "estado"]
    mensajes = [m.contenido for m in conversacion_service.mensajes(entorno.db, conversacion(entorno).id)]
    assert all("hmm" not in (c or "") for c in mensajes)  # lo dudoso ni siquiera llega al historial


def test_tras_varios_intentos_seguidos_sugiere_escribir_o_pedir_una_persona(llamada, entorno):
    entorno.stt.cola.extend([stt_resultado("", 0, silencio=True)] * 3)
    textos = []
    for _ in range(3):
        llamada.ws.send_bytes(pcm(1.0))
        llamada.enviar(tipo="fin_audio")
        textos.append(llamada.hasta("respuesta")["texto"])
        llamada.hasta_fin_de_respuesta()
    assert "repetirlo" in textos[0] and "repetirlo" in textos[1] and "escribirme" in textos[2] and "persona" in textos[2]


def test_un_audio_entendido_reinicia_la_cuenta_de_no_entendidos(llamada, entorno):
    entorno.stt.cola.extend([stt_resultado("", 0, silencio=True), stt_resultado("Necesito hablar con una persona"), stt_resultado("", 0, silencio=True)])
    for _ in range(3):
        llamada.ws.send_bytes(pcm(1.0))
        llamada.enviar(tipo="fin_audio")
        llamada.hasta("respuesta")
        llamada.hasta_fin_de_respuesta()
    assert [e["texto"] for e in llamada.eventos if e["tipo"] == "respuesta"][-1].startswith("Disculpe")


def test_audio_demasiado_largo_se_rechaza_y_la_siguiente_intervencion_funciona(llamada, entorno, monkeypatch):
    monkeypatch.setattr(get_settings(), "stt_max_segundos", 2)
    llamada.ws.send_bytes(pcm(1.5))
    llamada.ws.send_bytes(pcm(1.5))  # 3 s en total: se pasa
    llamada.ws.send_bytes(pcm(0.5))
    llamada.enviar(tipo="fin_audio")
    assert llamada.leer()["codigo"] == "audio_muy_largo"
    entorno.stt.cola.append(stt_resultado("Necesito hablar con una persona"))
    llamada.ws.send_bytes(pcm(1.0))
    llamada.enviar(tipo="fin_audio")
    llamada.hasta("respuesta", intencion="transferencia")
    assert entorno.stt.recibido == [(32000, "pcm16")]  # no quedaron restos del audio rechazado


def test_un_fallo_del_transcriptor_se_informa_y_la_llamada_sigue(llamada, entorno, monkeypatch):
    from app.core.errores import ErrorNegocio

    class Roto:
        def transcribir(self, *a, **k):
            raise ErrorNegocio("audio_invalido", "No se pudo leer el audio.", 422)

    monkeypatch.setattr(tr, "obtener_transcriptor", lambda: Roto())
    llamada.ws.send_bytes(pcm(1.0))
    llamada.enviar(tipo="fin_audio")
    assert llamada.hasta("error")["codigo"] == "audio_invalido"
    assert llamada.leer() == {"tipo": "estado", "valor": "escuchando"}


# ======================= interrupciones y turnos simultáneos =======================
LARGA = "Primera frase de la respuesta. Segunda frase de la respuesta. Tercera frase de la respuesta. Cuarta frase de la respuesta. Quinta frase de la respuesta."


@pytest.fixture
def respuesta_larga(monkeypatch):
    def hacer(emergencia=False):
        monkeypatch.setattr(agente_service, "enviar", lambda db, conv_id, texto: (None, Respuesta(LARGA, "informacion", emergencia=emergencia)))

    return hacer


def test_interrumpir_corta_la_respuesta_en_la_proxima_frase(llamada, entorno, monkeypatch, respuesta_larga):
    respuesta_larga()
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorLento("piper")))
    llamada.enviar(tipo="texto", texto="cuéntame")
    llamada.hasta("audio")  # ya está hablando
    llamada.enviar(tipo="interrumpir")
    fin = llamada.hasta_fin_de_respuesta()
    assert fin["interrumpida"] is True and fin["frases"] < 5 and "interrumpida" in llamada.tipos()


def test_despues_de_interrumpir_se_puede_hablar_de_inmediato(llamada, entorno, monkeypatch, respuesta_larga):
    respuesta_larga()
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorLento("piper")))
    llamada.enviar(tipo="texto", texto="cuéntame")
    llamada.hasta("audio")
    llamada.enviar(tipo="interrumpir")  # el paciente habla encima: corta y manda su audio
    entorno.stt.cola.append(stt_resultado("Necesito hablar con una persona"))
    llamada.ws.send_bytes(pcm(1.0))
    llamada.enviar(tipo="fin_audio")
    t = llamada.hasta("transcripcion")  # el turno nuevo se procesó apenas se cortó la respuesta anterior
    assert t["texto"] == "Necesito hablar con una persona" and entorno.stt.recibido == [(32000, "pcm16")]
    cortes = [e for e in llamada.eventos if e["tipo"] == "fin_respuesta"]
    assert len(cortes) == 1 and cortes[0]["interrumpida"] is True and cortes[0]["frases"] < 5  # la primera respuesta quedó cortada, antes de la transcripción
    llamada.hasta("respuesta")
    assert llamada.hasta("fin_respuesta")["interrumpida"] is False  # y la segunda se dijo completa


def test_un_aviso_de_emergencia_no_se_puede_interrumpir(llamada, entorno, monkeypatch, respuesta_larga):
    respuesta_larga(emergencia=True)
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorLento("piper")))
    llamada.enviar(tipo="texto", texto="ayuda")
    llamada.hasta("audio")
    llamada.enviar(tipo="interrumpir")
    fin = llamada.hasta_fin_de_respuesta()
    assert fin["interrumpida"] is False and fin["frases"] == 5


def test_pedir_otro_turno_mientras_responde_da_ocupado_sin_cortar_nada(llamada, entorno, monkeypatch, respuesta_larga):
    respuesta_larga()
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorLento("piper")))
    llamada.enviar(tipo="texto", texto="cuéntame")
    llamada.hasta("audio")
    llamada.enviar(tipo="texto", texto="otra cosa")
    assert llamada.hasta("error")["codigo"] == "ocupado"
    assert llamada.hasta_fin_de_respuesta()["frases"] == 5


def test_sin_voz_disponible_el_texto_llega_igual(llamada, entorno, monkeypatch):
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorFalso("piper", disponible=False)))
    llamada.enviar(tipo="texto", texto="Necesito hablar con una persona")
    assert llamada.hasta("respuesta")["transferida"] is True
    assert llamada.hasta("error")["codigo"] == "tts_no_disponible"
    assert llamada.hasta_fin_de_respuesta()["frases"] == 0 and llamada.audios == []


# ======================= cierre y auditoría =======================
def test_finalizar_cierra_la_conversacion_como_resuelta(entorno):
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.hasta_fin_de_respuesta()
        s.enviar(tipo="finalizar")
        assert s.hasta("cerrada")["motivo"] == "finalizada_por_el_usuario"
    assert esperar(lambda: conversacion(entorno).estado == EstadoConversacion.RESUELTA)
    assert SESIONES.obtener(conversacion(entorno).id) is None  # la memoria de la sesión se libera


def test_colgar_sin_despedirse_deja_la_conversacion_abandonada(entorno):
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.hasta_fin_de_respuesta()
    assert esperar(lambda: conversacion(entorno).estado == EstadoConversacion.ABANDONADA)


def test_por_inactividad_se_cuelga(entorno, monkeypatch):
    monkeypatch.setattr(get_settings(), "voz_inactividad_s", 0.5)
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.hasta_fin_de_respuesta()
        assert s.hasta("error")["codigo"] == "inactividad"
        assert s.hasta("cerrada")["motivo"] == "inactividad"
    assert esperar(lambda: conversacion(entorno).estado == EstadoConversacion.ABANDONADA)


def test_el_ping_mantiene_viva_la_llamada(entorno, monkeypatch):
    monkeypatch.setattr(get_settings(), "voz_inactividad_s", 0.6)
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.hasta_fin_de_respuesta()
        for _ in range(4):
            time.sleep(0.3)
            s.enviar(tipo="ping")
            assert s.hasta("pong")
        s.enviar(tipo="finalizar")
        assert s.hasta("cerrada")["motivo"] == "finalizada_por_el_usuario"


def test_si_la_sesion_del_asistente_caduco_se_avisa_y_se_cuelga(llamada, entorno):
    SESIONES.quitar(conversacion(entorno).id)
    llamada.enviar(tipo="texto", texto="hola otra vez")
    assert llamada.hasta("error")["codigo"] == "sesion_expirada"
    assert llamada.hasta("cerrada")["motivo"] == "sesion_terminada"


def test_la_auditoria_guarda_metricas_y_nunca_lo_dicho(llamada, entorno):
    entorno.stt.cola.append(stt_resultado("Necesito hablar con una persona, mi cédula es 1020304050", segundos=3.0))
    llamada.ws.send_bytes(pcm(1.0))
    llamada.enviar(tipo="fin_audio")
    llamada.hasta_fin_de_respuesta()
    llamada.enviar(tipo="finalizar")
    llamada.hasta("cerrada")
    assert esperar(lambda: any(a.accion == "voz_sesion_cerrada" for a in entorno.db.scalars(select(AuditoriaLog))))
    entorno.db.expire_all()
    registros = {a.accion: a for a in entorno.db.scalars(select(AuditoriaLog))}
    assert {"voz_sesion_iniciada", "stt_transcribir", "voz_sesion_cerrada"} <= set(registros)
    assert set(registros["voz_sesion_cerrada"].detalle) == {"turnos", "audio_s", "motivo"} and registros["voz_sesion_cerrada"].detalle["audio_s"] == 3.0
    assert "1020304050" not in str([a.detalle for a in registros.values()])


def test_dos_llamadas_a_la_vez_no_se_mezclan(entorno):
    with entorno.client.websocket_connect(URL) as ws1, entorno.client.websocket_connect(URL) as ws2:
        a, b = Sesion(ws1), Sesion(ws2)
        # En producción cada llamada tiene su propia sesión de BD; aquí las pruebas comparten una sola, así que las operaciones con la BD se turnan.
        a.enviar(tipo="auth", token=entorno.token)
        ida = a.hasta("listo")["conversacion_id"]
        a.hasta_fin_de_respuesta()
        b.enviar(tipo="auth", token=entorno.token)
        idb = b.hasta("listo")["conversacion_id"]
        assert ida != idb
        b.hasta_fin_de_respuesta()
        a.enviar(tipo="texto", texto="Necesito hablar con una persona")
        assert a.hasta("respuesta", intencion="transferencia")
        a.hasta_fin_de_respuesta()
        b.enviar(tipo="texto", texto="Mi papá se desmayó y no respira")
        r = b.hasta("respuesta", intencion="emergencia")
        assert r["emergencia"] is True and r["transferida"] is False
        assert "transferencia" not in [e.get("intencion") for e in b.eventos]  # lo de la llamada A no se filtra a la B


def test_el_hilo_de_la_llamada_no_bloquea_el_servidor(entorno, monkeypatch):
    """Mientras una síntesis larga ocupa su hilo, otra petición HTTP debe responder enseguida."""
    monkeypatch.setattr(modulo_tts, "obtener_tts", lambda: servicio_tts(MotorLento("piper")))
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        s.hasta("listo")
        t = time.perf_counter()
        assert entorno.client.get("/api/v1/health").status_code == 200
        assert time.perf_counter() - t < 1.0
        s.hasta_fin_de_respuesta()


# ======================= recorrido real: voz sintetizada → Whisper → asistente → Piper → Whisper =======================
@pytest.fixture(scope="module")
def frases_habladas(tmp_path_factory):
    from tests import audio_utils as au

    if not au.hay_voz_sapi() or not au.hay_ffmpeg():
        pytest.skip("Falta la voz de Windows (Microsoft Sabina) o ffmpeg para generar el audio de entrada")
    carpeta = tmp_path_factory.mktemp("voz")
    dichas = {"persona": "Necesito hablar con una persona de la institución", "emergencia": "Mi papá se desmayó y no respira"}
    return {k: au.pcm16_16k(au.sintetizar_wav(v, carpeta / f"{k}.wav"), carpeta / f"{k}.pcm") for k, v in dichas.items()}


@pytest.fixture
def llamada_real(client, db):
    from app.stt.transcriptor import Transcriptor
    from app.tts.motores import MotorPiper

    if not Transcriptor(get_settings()).estado()["disponible"]:
        pytest.skip("No está el modelo de voz a texto")
    if not MotorPiper(get_settings()).disponible():
        pytest.skip("No está la voz de Piper")
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    token = cabecera(client, "coord@ejemplo.com")["Authorization"].split()[1]
    with client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=token)
        s.hasta("listo")
        s.hasta_fin_de_respuesta()
        s.eventos.clear()
        s.audios.clear()
        yield s


def _trozos(pcm_bytes: bytes, ms: int = 100):
    paso = 16 * 2 * ms
    return [pcm_bytes[i:i + paso] for i in range(0, len(pcm_bytes), paso)]


def _dicho_por_el_asistente(s: Sesion) -> str:
    """Lo que dice el audio recibido, reconocido por Whisper (comprueba que lo hablado coincide con el texto)."""
    from app.stt.transcriptor import Transcriptor

    partes = [wav for _, wav in s.audios]
    primero = wave.open(io.BytesIO(partes[0]))
    salida = io.BytesIO()
    with wave.open(salida, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(primero.getframerate())
        for p in partes:
            with wave.open(io.BytesIO(p)) as o:
                w.writeframes(o.readframes(o.getnframes()))
    return Transcriptor(get_settings()).transcribir(salida.getvalue()).texto


@pytest.mark.stt
@pytest.mark.tts
def test_real_de_la_voz_del_paciente_a_la_voz_del_asistente(llamada_real, frases_habladas):
    t0 = time.perf_counter()
    for trozo in _trozos(frases_habladas["persona"]):
        llamada_real.ws.send_bytes(trozo)
    llamada_real.enviar(tipo="fin_audio")
    transcripcion = llamada_real.hasta("transcripcion")
    primer_audio = llamada_real.hasta("audio")
    latencia = time.perf_counter() - t0
    respuesta = next(e for e in llamada_real.eventos if e["tipo"] == "respuesta")
    llamada_real.hasta_fin_de_respuesta()
    print(f"\n  fin de la frase del paciente → primer audio del asistente: {latencia:.1f} s · entendió: «{transcripcion['texto']}» (confianza {transcripcion['confianza']})")
    assert {"persona", "hablar"} <= {p.strip(".,¡!¿?").lower() for p in transcripcion["texto"].split()}
    assert respuesta["transferida"] is True and primer_audio["motor"] == "piper"
    assert any(p in _dicho_por_el_asistente(llamada_real).lower() for p in ("persona", "funcionario", "transfer", "comunic"))


@pytest.mark.stt
@pytest.mark.tts
def test_real_la_emergencia_dicha_en_voz_se_contesta_con_el_123_hablado(llamada_real, frases_habladas):
    for trozo in _trozos(frases_habladas["emergencia"]):
        llamada_real.ws.send_bytes(trozo)
    llamada_real.enviar(tipo="fin_audio")
    r = llamada_real.hasta("respuesta")
    assert r["emergencia"] is True and "123" in r["texto"]
    llamada_real.hasta_fin_de_respuesta()
    dicho = _dicho_por_el_asistente(llamada_real).lower()
    assert re.search(r"123|1\W*2\W*3|uno\W+dos\W+tres", dicho), dicho  # Whisper lo escribe «1-2-3»: se dijo «uno dos tres», no «ciento veintitrés»
    assert "ciento" not in dicho


# ======================= elegir la institución al abrir la llamada =======================
def test_la_llamada_se_abre_con_la_institucion_elegida(entorno):
    from app.models import Institucion
    inst = Institucion(nombre="Clínica Elegida Prueba (ficticia)", tipo="Clínica", ciudad="Ciudad Demo")
    entorno.db.add(inst)
    entorno.db.flush()
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token, institucion_id=inst.id)
        listo = s.hasta("listo")
        assert listo["institucion"] == {"id": inst.id, "nombre": "Clínica Elegida Prueba", "tipo": "Clínica"}  # sin la marca de ficticia
        saludo = s.hasta("respuesta")
        assert "Clínica Elegida Prueba" in saludo["texto"] and "ficticia" not in saludo["texto"].lower()
    assert conversacion(entorno).institucion_id == inst.id


@pytest.mark.parametrize("valor", [987654, "uno", True, 1.5])
def test_una_institucion_invalida_no_abre_la_llamada(entorno, valor):
    with entorno.client.websocket_connect(URL) as ws:
        ws.send_json({"tipo": "auth", "token": entorno.token, "institucion_id": valor})
        assert ws.receive_json()["tipo"] == "error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert conversacion(entorno) is None


# ======================= institución elegida hablando y cierre al despedirse =======================
def test_sin_institucion_la_llamada_la_pregunta_y_avisa_cuando_queda_elegida(entorno):
    from app.models import Institucion
    a = Institucion(nombre="Clínica Alfa Prueba (ficticia)", tipo="Clínica")
    b = Institucion(nombre="Hospital Beta Prueba (ficticio)", tipo="Hospital")
    entorno.db.add_all([a, b])
    entorno.db.flush()
    with entorno.client.websocket_connect(URL) as ws:
        s = Sesion(ws)
        s.enviar(tipo="auth", token=entorno.token)
        assert s.hasta("listo")["institucion"] is None  # todavía no hay institución: se elige conversando
        assert "¿Con cuál institución desea comunicarse?" in s.hasta("respuesta")["texto"]
        s.hasta_fin_de_respuesta()
        s.enviar(tipo="texto", texto="Con el hospital Beta")
        elegida = s.hasta("institucion")
        assert elegida["institucion"] == {"id": b.id, "nombre": "Hospital Beta Prueba", "tipo": "Hospital"}
        assert "le atiende Hospital Beta Prueba" in s.hasta("respuesta")["texto"]
    assert conversacion(entorno).institucion_id == b.id


def _hasta_la_despedida(llamada):
    """Dice «no, eso es todo», contesta la encuesta con un 5 y devuelve la respuesta de despedida."""
    llamada.enviar(tipo="texto", texto="No, eso es todo")
    r = llamada.hasta("respuesta")
    assert r["intencion"] == "encuesta" and r["terminar"] is False and "de uno a cinco" in r["texto"]
    llamada.hasta_fin_de_respuesta()
    llamada.enviar(tipo="texto", texto="Cinco")
    return llamada.hasta("respuesta")


def test_al_confirmar_que_no_necesita_mas_ayuda_la_llamada_pregunta_la_encuesta_y_se_cierra_sola(entorno, llamada):
    r = _hasta_la_despedida(llamada)
    assert r["terminar"] is True and r["intencion"] == "despedida" and "Hasta luego" in r["texto"]
    fin = llamada.hasta("fin_respuesta")
    assert fin["frases"] >= 1 and not fin["interrumpida"]  # la despedida se dijo completa
    assert llamada.hasta("fin_llamada")["motivo"] == "sin_mas_ayuda"
    assert llamada.hasta("cerrada")["motivo"] == "finalizada_por_el_asistente"
    with pytest.raises(WebSocketDisconnect):
        llamada.ws.receive_json()
    assert esperar(lambda: conversacion(entorno).estado == EstadoConversacion.RESUELTA)
    from app.models import EncuestaSatisfaccion
    entorno.db.expire_all()
    assert entorno.db.scalars(select(EncuestaSatisfaccion)).one().puntaje == 5


def test_la_despedida_no_se_corta_aunque_el_paciente_hable_encima(entorno, llamada):
    _hasta_la_despedida(llamada)
    llamada.enviar(tipo="interrumpir")
    assert llamada.hasta("fin_respuesta")["interrumpida"] is False


# ======================= detector de voz del navegador (JS puro, se prueba con node) =======================
DETECTOR_JS = (Path(__file__).resolve().parent.parent / "frontend" / "panel" / "js" / "voz" / "detector.js").as_uri()
GUION_NODE = """
import { DetectorDeVoz } from "%s";
const f = new Int16Array(320);
const correr = (rmss, opc = {}, d = new DetectorDeVoz()) => rmss.map((r) => d.procesar(f, r, opc)?.accion ?? null);
const rep = (v, n) => Array(n).fill(v);
const calib = rep(0.002, 25);
const salida = {};
salida.normal = correr([...calib, ...rep(0.002, 10), ...rep(0.1, 20), ...rep(0.002, 50)]);
salida.golpe = correr([...calib, ...rep(0.1, 5), ...rep(0.002, 30)]);
salida.encima_corto = correr([...calib, ...rep(0.05, 10), ...rep(0.002, 5)], { encima: true });
salida.encima_largo = correr([...calib, ...rep(0.05, 40)], { encima: true });
const frase_con_pausas = [...rep(0.08, 6), ...rep(0.002, 2)];  // como una frase real: palabras con pausas cortas entre ellas
salida.pausas_normal = correr([...calib, ...frase_con_pausas, ...frase_con_pausas, ...frase_con_pausas, ...rep(0.002, 50)]);
salida.pausas_encima = correr([...calib, ...frase_con_pausas, ...frase_con_pausas, ...frase_con_pausas, ...frase_con_pausas], { encima: true });
salida.ruido_con_picos = correr([...calib, ...rep([0.08, 0.002, 0.002, 0.002], 20).flat()], { encima: true });
salida.bloqueado = correr([...calib, ...rep(0.1, 20)], { bloqueado: true });
salida.ruido_alto = correr([...rep(0.03, 25), ...rep(0.03, 30), ...rep(0.15, 12)]);
const d = new DetectorDeVoz(2);  // máximo 2 s = 100 cuadros
salida.maximo = correr([...calib, ...rep(0.1, 200)], {}, d);
const e = new DetectorDeVoz();
const pre = [...calib, ...rep(0.002, 30)].map((r) => e.procesar(f, r));
const inicio = [...rep(0.1, 8)].map((r) => e.procesar(f, r)).find(Boolean);
salida.cuadros_al_iniciar = inicio.cuadros.length;
console.log(JSON.stringify(salida));
""" % DETECTOR_JS


@pytest.fixture(scope="module")
def detector():
    if shutil.which("node") is None:
        pytest.skip("No hay node para probar el JavaScript")
    r = subprocess.run(["node", "--input-type=module", "-e", GUION_NODE], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_detector_abre_con_voz_sostenida_y_cierra_con_silencio(detector):
    acciones = detector["normal"]
    assert acciones.count("inicio") == 1 and acciones.count("fin") == 1 and acciones.index("inicio") < acciones.index("fin")
    assert "cuadro" in acciones  # entre el inicio y el fin se siguen enviando cuadros


def test_detector_ignora_un_golpe_corto(detector):
    assert set(detector["golpe"]) == {None}  # 100 ms de ruido fuerte no son una intervención


def test_para_hablar_encima_del_asistente_se_exige_mas_tiempo_y_volumen(detector):
    assert "inicio" not in detector["encima_corto"] and "inicio" in detector["encima_largo"]


def test_una_frase_con_pausas_entre_palabras_tambien_abre_la_intervencion_incluso_encima_del_asistente(detector):
    assert "inicio" in detector["pausas_normal"] and "inicio" in detector["pausas_encima"]  # antes solo abría con 280 ms de voz SEGUIDA
    assert "inicio" not in detector["ruido_con_picos"]  # picos sueltos de ruido (1 de cada 4 cuadros) siguen sin contar como voz


def test_mientras_el_servidor_procesa_no_se_abre_otra_intervencion(detector):
    assert set(detector["bloqueado"]) == {None}


def test_el_umbral_se_adapta_al_ruido_de_fondo(detector):
    assert detector["ruido_alto"][25:55] == [None] * 30 and "inicio" in detector["ruido_alto"][55:]  # un cuarto ruidoso no se toma por voz


def test_una_intervencion_no_pasa_del_maximo(detector):
    assert "fin" in detector["maximo"] and detector["maximo"].index("fin") - detector["maximo"].index("inicio") <= 101


def test_al_iniciar_se_incluye_el_comienzo_de_la_frase(detector):
    assert detector["cuadros_al_iniciar"] == 15  # 0,3 s anteriores a la detección


def test_los_archivos_de_voz_se_sirven_al_navegador(client):
    for ruta in ("/panel/js/voz/captura.worklet.js", "/panel/js/voz/cliente.js", "/panel/js/voz/detector.js"):
        r = client.get(ruta)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"], ruta
    assert "microphone=(self)" in client.get("/panel/").headers["permissions-policy"]  # el micrófono solo se permite a esta misma página

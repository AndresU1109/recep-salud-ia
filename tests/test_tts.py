"""Fase 13: texto a voz. Preparación del texto, servicio (motores, caché, límites), API, y motores reales con ida y vuelta por Whisper."""
import io
import threading
import wave
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.errores import ErrorNegocio
from app.models import AuditoriaLog
from app.models.enums import Rol
from app.tts import servicio as modulo_servicio
from app.tts.motores import Audio, MotorPiper, MotorSapi
from app.tts.preparacion import dividir_en_frases, preparar_para_voz
from app.tts.servicio import ServicioTTS
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"


# ======================= preparación del texto =======================
@pytest.mark.parametrize("entrada, esperado", [
    ("a las 10:00 a. m.", "a las 10 de la mañana"), ("a las 8:20 a. m.", "a las 8 y 20 de la mañana"), ("a las 3:00 p. m.", "a las 3 de la tarde"),
    ("a las 8:05 p. m.", "a las 8 y 5 de la noche"), ("a las 12:00 p. m.", "a las 12 del mediodía"), ("a las 11:40 a.m.", "a las 11 y 40 de la mañana"),
    ("a las 14:30", "a las 14 y 30"), ("a las 14:00", "a las 14 en punto"),
    ("Llame al 123 ahora", "Llame al uno dos tres ahora"), ("código 123456", "código 123456"), ("Dra. Ficticia Uno", "doctora Ficticia Uno"),
    ("Dr. Prueba Dos", "doctor Prueba Dos"), ("queja No. 7", "queja número 7"),
    ("**Hola**, _buenas_ `tardes`", "Hola, buenas tardes"), ("- uno\n- dos", "uno dos"), ("Listo 😀 gracias 🎉", "Listo gracias"),
    ("Código K 7 M 2 Q X D 9", "Código K 7 M 2 Q X D 9"), ("  mucho   espacio \n\n aquí ", "mucho espacio aquí"), ("", ""),
])
def test_preparar_para_voz(entrada, esperado):
    assert preparar_para_voz(entrada) == esperado


def test_la_frase_completa_del_asistente_queda_pronunciable():
    t = preparar_para_voz("Listo, su cita quedó agendada para el lunes 4 de marzo a las 10:00 a. m. con Dra. Ficticia Uno. Su código de reserva es K 7 M 2 Q X D 9.")
    assert t == "Listo, su cita quedó agendada para el lunes 4 de marzo a las 10 de la mañana con doctora Ficticia Uno. Su código de reserva es K 7 M 2 Q X D 9."


@pytest.mark.parametrize("texto", [
    "Una sola frase.", "Primera frase. Segunda frase. Tercera frase.", "¿Pregunta uno? ¡Exclamación! Afirmación.", "palabra " * 120,
    "Una frase larguísima sin ningún punto que sigue y sigue y sigue " * 8 + ".",
])
def test_dividir_en_frases_conserva_todo_y_respeta_el_largo(texto):
    frases = dividir_en_frases(texto, 80)
    assert frases and all(f and len(f) <= 80 for f in frases)
    assert " ".join(frases).split() == texto.split()


def test_las_frases_cortan_en_puntuacion():
    assert dividir_en_frases("Hola. ¿Cómo está? Bien, gracias.", 20) == ["Hola.", "¿Cómo está?", "Bien, gracias."]


# ======================= servicio con motores falsos =======================
def wav(segundos: float, frecuencia: int = 22050) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(frecuencia)
        w.writeframes(b"\x00\x00" * int(frecuencia * segundos))
    return b.getvalue()


class MotorFalso:
    def __init__(self, nombre, disponible=True, falla=False):
        self.nombre, self._disponible, self.falla, self.llamadas = nombre, disponible, falla, []

    def disponible(self):
        return self._disponible

    def sintetizar(self, texto, velocidad):
        self.llamadas.append((texto, velocidad))
        if self.falla:
            raise ErrorNegocio("tts_no_disponible", "El servicio de voz no está disponible: falló.", 503)
        s = len(texto) * 0.05 / velocidad
        return Audio(wav(s), 22050, s, self.nombre)


def servicio(*motores, **ajustes) -> ServicioTTS:
    return ServicioTTS(Settings(_env_file=None, **ajustes), list(motores))


def test_auto_prefiere_piper_y_cae_a_la_voz_de_windows():
    piper, sapi = MotorFalso("piper"), MotorFalso("sapi")
    assert servicio(piper, sapi).motor() is piper
    assert servicio(MotorFalso("piper", disponible=False), sapi).motor() is sapi


def test_sin_ninguna_voz_el_error_es_claro():
    with pytest.raises(ErrorNegocio) as e:
        servicio(MotorFalso("piper", False), MotorFalso("sapi", False)).sintetizar("hola")
    assert e.value.codigo == "tts_no_disponible" and e.value.status == 503 and "Piper" in e.value.mensaje


def test_motor_explicito_no_cambia_por_otro():
    piper, sapi = MotorFalso("piper"), MotorFalso("sapi")
    s = servicio(piper, sapi, tts_motor="sapi")
    s.sintetizar("hola mundo")
    assert sapi.llamadas and not piper.llamadas
    with pytest.raises(ErrorNegocio):
        servicio(MotorFalso("piper", False), sapi, tts_motor="piper").sintetizar("hola")  # pidió Piper y no está: no se usa otra voz en silencio


def test_se_lee_el_texto_ya_preparado():
    m = MotorFalso("piper")
    servicio(m).sintetizar("Cita a las 10:00 a. m. con Dra. Ficticia")
    assert m.llamadas[0][0] == "Cita a las 10 de la mañana con doctora Ficticia"


def test_lo_repetido_sale_de_la_cache_y_cambiar_la_velocidad_la_invalida():
    m = MotorFalso("piper")
    s = servicio(m)
    a1 = s.sintetizar("Buenos días. ¿En qué puedo ayudarle?")
    a2 = s.sintetizar("Buenos días. ¿En qué puedo ayudarle?")
    assert len(m.llamadas) == 2 and a1.wav == a2.wav  # dos frases, cada una sintetizada una sola vez
    s.sintetizar("Buenos días. ¿En qué puedo ayudarle?", velocidad=0.8)
    assert len(m.llamadas) == 4


def test_la_cache_tiene_limite():
    m = MotorFalso("piper")
    s = servicio(m, tts_cache_entradas=2)
    for t in ("uno uno uno", "dos dos dos", "tres tres tres"):
        s.sintetizar(t)
    assert s.estado()["en_cache"] == 2
    s.sintetizar("uno uno uno")  # ya fue desalojada: se vuelve a sintetizar
    assert len(m.llamadas) == 4


def test_limites_de_texto():
    s = servicio(MotorFalso("piper"), tts_max_caracteres=50)
    for texto, codigo in (("", "texto_vacio"), ("   ", "texto_vacio"), ("😀 **", "texto_vacio"), ("x" * 51, "texto_muy_largo")):
        with pytest.raises(ErrorNegocio) as e:
            s.sintetizar(texto)
        assert e.value.codigo == codigo and e.value.status == 422


def test_varias_frases_se_unen_en_un_solo_wav_valido():
    s = servicio(MotorFalso("piper"))
    a = s.sintetizar("Primera frase. Segunda frase. Tercera frase.")
    with wave.open(io.BytesIO(a.wav)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 22050
        assert w.getnframes() / 22050 == pytest.approx(a.duracion_s, abs=0.01)
    assert [f for f, _ in s.sintetizar_frases("Primera frase. Segunda frase.")] == ["Primera frase.", "Segunda frase."]


def test_un_fallo_del_motor_se_propaga_como_error_claro():
    with pytest.raises(ErrorNegocio) as e:
        servicio(MotorFalso("piper", falla=True)).sintetizar("hola")
    assert e.value.codigo == "tts_no_disponible"


def test_sintetizar_en_paralelo():
    s = servicio(MotorFalso("piper"))
    salidas = []
    hilos = [threading.Thread(target=lambda i=i: salidas.append(s.sintetizar(f"frase número {i} de la prueba").duracion_s)) for i in range(8)]
    [h.start() for h in hilos]
    [h.join(20) for h in hilos]
    assert len(salidas) == 8


def test_estado():
    e = servicio(MotorFalso("piper", False), MotorFalso("sapi")).estado()
    assert e["motor_en_uso"] == "sapi" and e["motores"] == {"piper": False, "sapi": True} and e["max_caracteres"] == 600


# ======================= API =======================
@pytest.fixture
def api_falsa(monkeypatch):
    s = servicio(MotorFalso("piper"))
    monkeypatch.setattr(modulo_servicio, "obtener_tts", lambda: s)
    return s


def test_api_devuelve_audio_wav_y_audita_sin_el_texto(client, db, api_falsa):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    r = client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": "Su cédula 1023456789 quedó verificada."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.headers["x-motor"] == "piper" and r.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 22050
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "tts_sintetizar")).one()
    assert set(a.detalle) == {"motor", "audio_s", "caracteres"} and "1023456789" not in str(a.detalle)


def test_api_permisos_y_validaciones(client, db, api_falsa):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    assert client.post(f"{A}/tts/sintetizar", json={"texto": "hola"}).status_code == 401
    assert client.post(f"{A}/tts/sintetizar", headers=cabecera(client, "rec@ejemplo.com"), json={"texto": "hola"}).status_code == 403
    h = cabecera(client, "coord@ejemplo.com")
    assert client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": ""}).status_code == 422
    assert client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": "hola", "velocidad": 3}).status_code == 422
    assert client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": "x" * 700}).json()["error"]["codigo"] == "texto_muy_largo"
    assert client.get(f"{A}/tts/estado", headers=h).json()["motor_en_uso"] == "piper"


def test_api_sin_voz_responde_503(client, db, monkeypatch):
    monkeypatch.setattr(modulo_servicio, "obtener_tts", lambda: servicio(MotorFalso("piper", False), MotorFalso("sapi", False)))
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    r = client.post(f"{A}/tts/sintetizar", headers=cabecera(client, "coord@ejemplo.com"), json={"texto": "hola"})
    assert r.status_code == 503 and r.json()["error"]["codigo"] == "tts_no_disponible"


# ======================= motores reales (y ida y vuelta por Whisper) =======================
def _wav_ok(datos: bytes):
    with wave.open(io.BytesIO(datos)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes() / w.getframerate()


@pytest.fixture(scope="session")
def piper_real():
    m = MotorPiper(Settings(_env_file=None))
    if not m.disponible():
        pytest.skip("No está la voz de Piper (data/models/tts/piper/es_MX-claude-high.onnx)")
    m.precargar()
    return m


@pytest.fixture(scope="session")
def sapi_real():
    m = MotorSapi(Settings(_env_file=None))
    if not m.disponible():
        pytest.skip("La voz de Windows no está disponible")
    try:
        m.sintetizar("hola", 1.0)
    except ErrorNegocio:
        pytest.skip("La voz «Microsoft Sabina» no está instalada")
    yield m
    m.cerrar()


@pytest.fixture(scope="session")
def stt_real():
    from app.stt.transcriptor import Transcriptor

    t = Transcriptor(Settings(_env_file=None))
    if not t.estado()["disponible"]:
        pytest.skip("No está el modelo de voz a texto")
    return t


def palabras(texto):
    from app.stt.transcriptor import normalizar

    return set(normalizar(texto).split())


@pytest.mark.tts
@pytest.mark.parametrize("motor", ["piper_real", "sapi_real"])
def test_los_motores_reales_dan_wav_valido_y_rapido(motor, request):
    m = request.getfixturevalue(motor)
    a = m.sintetizar("Buenos días. Soy el asistente virtual de la institución. ¿En qué puedo ayudarle?", 1.0)
    canales, ancho, frecuencia, duracion = _wav_ok(a.wav)
    assert (canales, ancho) == (1, 2) and frecuencia in (16000, 22050, 24000, 44100) and 3 < duracion < 12 and a.motor == m.nombre


@pytest.mark.tts
def test_piper_es_mucho_mas_rapido_que_el_tiempo_real(piper_real):
    import time

    t = time.perf_counter()
    a = piper_real.sintetizar("Listo, su cita quedó agendada para el lunes cuatro de marzo a las diez de la mañana con la doctora Ficticia Uno.", 1.0)
    factor = (time.perf_counter() - t) / a.duracion_s
    print(f"\n  Piper: {a.duracion_s:.1f} s de audio en {(time.perf_counter() - t) * 1000:.0f} ms (RTF {factor:.2f})")
    assert factor < 0.5


@pytest.mark.tts
@pytest.mark.parametrize("motor", ["piper_real", "sapi_real"])
def test_la_velocidad_cambia_la_duracion(motor, request):
    m = request.getfixturevalue(motor)
    frase = "Por favor tenga a la mano su documento de identidad para verificar su identidad."
    normal, lento = m.sintetizar(frase, 1.0).duracion_s, m.sintetizar(frase, 0.7).duracion_s
    assert lento > normal * 1.15


@pytest.mark.tts
@pytest.mark.parametrize("motor", ["piper_real", "sapi_real"])
def test_ida_y_vuelta_acentos_y_enie(motor, request, stt_real):
    m = request.getfixturevalue(motor)
    r = stt_real.transcribir(m.sintetizar("Mañana el niño tiene cita de pediatría. Traiga su cédula.", 1.0).wav)
    assert {"manana", "nino", "cedula"} <= palabras(r.texto) or {"manana", "cedula", "pediatria"} <= palabras(r.texto), r.texto


@pytest.mark.tts
@pytest.mark.parametrize("motor", ["piper_real", "sapi_real"])
def test_ida_y_vuelta_de_lo_critico_horas_y_emergencia(motor, request, stt_real):
    m = request.getfixturevalue(motor)
    servicio_real = ServicioTTS(Settings(_env_file=None), [m])
    hora = stt_real.transcribir(servicio_real.sintetizar("Su cita es el lunes a las 10:00 a. m. con la doctora Ficticia Uno.").wav)
    assert {"lunes", "10", "manana"} <= palabras(hora.texto) or {"lunes", "diez", "manana"} <= palabras(hora.texto), hora.texto
    emergencia = stt_real.transcribir(servicio_real.sintetizar("Llame ahora mismo a la línea de emergencias 123.").wav)
    assert "123" in emergencia.texto or {"1", "2", "3"} <= set(emergencia.texto) or "uno dos tres" in emergencia.texto.lower(), emergencia.texto


@pytest.mark.tts
def test_la_voz_de_windows_se_recupera_si_su_proceso_muere(sapi_real):
    sapi_real.sintetizar("primera", 1.0)
    sapi_real._proc.kill()
    sapi_real._proc.wait()
    assert _wav_ok(sapi_real.sintetizar("segunda después de caerse", 1.0).wav)[3] > 0.5  # reinicia el trabajador solo


@pytest.mark.tts
def test_la_voz_de_windows_acepta_texto_hostil_sin_ejecutar_nada(sapi_real, tmp_path):
    marca = tmp_path / "no-debe-existir.txt"
    a = sapi_real.sintetizar(f"hola'; New-Item {marca}; '\" $(New-Item {marca}) `n `\"", 1.0)  # el texto viaja en base64: es dato, nunca código
    assert not marca.exists() and a.duracion_s > 0.5


@pytest.mark.tts
@pytest.mark.parametrize("motor", ["piper_real", "sapi_real"])
def test_sintetizar_en_paralelo_con_motores_reales(motor, request):
    m = request.getfixturevalue(motor)
    salidas = []
    hilos = [threading.Thread(target=lambda i=i: salidas.append(m.sintetizar(f"Esta es la frase número {i} de la prueba.", 1.0).duracion_s)) for i in range(4)]
    [h.start() for h in hilos]
    [h.join(120) for h in hilos]
    assert len(salidas) == 4 and all(s > 0.5 for s in salidas)


@pytest.mark.tts
def test_servicio_real_extremo_a_extremo_con_frases_del_asistente(piper_real, stt_real):
    s = ServicioTTS(Settings(_env_file=None), [piper_real])
    texto = "Listo, su cita quedó agendada para el lunes 4 de marzo a las 10:00 a. m. con Dra. Ficticia Uno. Su código de reserva es K 7 M 2 Q X D 9."
    a = s.sintetizar(texto)
    r = stt_real.transcribir(a.wav)
    assert {"cita", "agendada", "lunes", "marzo", "doctora"} <= palabras(r.texto), r.texto
    _, _, _, dur = _wav_ok(a.wav)
    assert dur == pytest.approx(a.duracion_s, abs=0.1) and SimpleNamespace

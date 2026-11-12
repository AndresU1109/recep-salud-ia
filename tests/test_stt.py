"""Fase 12: voz a texto. Unitarias (sin modelo), API con un transcriptor falso, y pruebas con el modelo real y audio sintetizado."""
import io
import math
import threading
import wave
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from app.agents import entendimiento as nlu
from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.models import AuditoriaLog
from app.models.enums import Rol
from app.stt import transcriptor as tr
from app.stt.transcriptor import Transcriptor, decodificar, energia_dbfs, es_alucinacion
from tests import audio_utils as au
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"


def pcm(segundos: float, amplitud: float = 0.0, frecuencia: int = 440) -> bytes:
    t = np.arange(int(16000 * segundos)) / 16000
    return (np.sin(2 * np.pi * frecuencia * t) * amplitud * 32767).astype("<i2").tobytes()


# ======================= funciones auxiliares =======================
def test_energia_en_dbfs():
    assert energia_dbfs(np.zeros(1000, dtype=np.float32)) == -120.0 and energia_dbfs(np.array([], dtype=np.float32)) == -120.0
    seno = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000).astype(np.float32)
    assert energia_dbfs(seno) == pytest.approx(-3.0, abs=0.2)  # un seno a escala completa
    assert energia_dbfs(seno * 0.01) == pytest.approx(-43.0, abs=0.2)


@pytest.mark.parametrize("texto, esperado", [
    ("Gracias por ver el video", True), ("¡Gracias por ver el video!", True), ("Subtítulos realizados por la comunidad de Amara.org", True), ("Suscríbete", True), ("", True), ("   ", True),
    ("Música", True), ("Gracias por ver el video, quiero una cita médica para mañana por la mañana", False), ("Quiero cancelar mi cita", False),
    ("Mi cédula es 1020304050", False), ("hasta la próxima semana tengo disponibilidad para una consulta de medicina general", False),
])
def test_filtro_de_alucinaciones(texto, esperado):
    assert es_alucinacion(texto) is esperado


def test_decodificar_pcm16():
    audio = decodificar(pcm(0.5, 0.5), "pcm16")
    assert audio.dtype == np.float32 and audio.size == 8000 and abs(audio).max() == pytest.approx(0.5, abs=0.01)
    assert decodificar(b"\x00\x01\x02", "pcm16").size == 1  # un byte suelto de más se ignora


def test_audio_ilegible_se_rechaza_con_mensaje_claro():
    with pytest.raises(ErrorNegocio) as e:
        decodificar(b"esto no es audio de ningun tipo" * 20, "auto")
    assert e.value.codigo == "audio_invalido" and e.value.status == 422 and "webm" in e.value.mensaje


# ======================= límites y silencio (sin cargar el modelo) =======================
def nuevo(**ajustes) -> Transcriptor:
    return Transcriptor(Settings(_env_file=None, **ajustes))


def test_audio_vacio_demasiado_grande_o_largo():
    t = nuevo(stt_max_bytes=1000, stt_max_segundos=5)
    for datos, codigo, status in ((b"", "audio_vacio", 422), (b"\0" * 1001, "audio_muy_grande", 413), (pcm(6.0), "audio_muy_grande", 413)):
        with pytest.raises(ErrorNegocio) as e:
            t.transcribir(datos, "pcm16")
        assert (e.value.codigo, e.value.status) == (codigo, status)
    t = nuevo(stt_max_segundos=5)
    with pytest.raises(ErrorNegocio) as e:
        t.transcribir(pcm(6.0), "pcm16")
    assert e.value.codigo == "audio_muy_largo" and e.value.status == 413


def test_silencio_y_audio_muy_corto_no_cargan_el_modelo_ni_inventan_texto():
    t = nuevo()
    silencio = t.transcribir(pcm(2.0, 0.0), "pcm16")
    assert silencio.silencio and silencio.texto == "" and silencio.motivo == "silencio" and silencio.confianza == 0.0
    corto = t.transcribir(pcm(0.1, 0.5), "pcm16")
    assert corto.silencio and corto.motivo == "muy_corto"
    susurro = t.transcribir(pcm(2.0, 0.001), "pcm16")  # por debajo de -50 dBFS
    assert susurro.silencio and susurro.motivo == "silencio"
    assert t._modelo is None  # nada de esto necesitó el modelo


def test_sin_el_modelo_en_disco_el_error_es_claro(tmp_path):
    t = nuevo(stt_models_dir=str(tmp_path / "no-existe"))
    with pytest.raises(ErrorNegocio) as e:
        t.transcribir(pcm(1.0, 0.5), "pcm16")
    assert e.value.codigo == "stt_no_disponible" and e.value.status == 503 and "no-existe" in e.value.mensaje
    assert t.estado()["disponible"] is False and t.estado()["cargado"] is False


# ======================= con un modelo falso (lógica de confianza y filtros) =======================
class ModeloFalso:
    def __init__(self, segmentos):
        self.segmentos, self.llamadas = segmentos, []

    def transcribe(self, audio, **kw):
        self.llamadas.append(kw)
        return iter(self.segmentos), SimpleNamespace()


def seg(texto, logprob=-0.2):
    return SimpleNamespace(text=f" {texto}", avg_logprob=logprob)


def con_modelo(*segmentos, **ajustes) -> Transcriptor:
    t = nuevo(**ajustes)
    t._modelo = ModeloFalso(list(segmentos))
    return t


# ======================= GPU opcional con respaldo a CPU =======================
def test_preparar_cuda_agrega_las_carpetas_que_existen(tmp_path, monkeypatch):
    import os

    cublas, inexistente = tmp_path / "cublas" / "bin", tmp_path / "no_existe" / "bin"
    cublas.mkdir(parents=True)
    agregadas = []
    monkeypatch.setattr(os, "add_dll_directory", lambda d: agregadas.append(d), raising=False)
    monkeypatch.setattr(os, "environ", {"PATH": "base"})
    monkeypatch.setattr(tr.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(tr.site, "getusersitepackages", lambda: str(tmp_path / "usuario"))
    puestas = tr.preparar_cuda(Settings(_env_file=None, stt_cuda_dll_dirs=f"{cublas};{inexistente}"))
    assert puestas == [str(cublas)] and agregadas == [str(cublas)] and os.environ["PATH"].startswith(str(cublas))


class FabricaDeModelos:
    """Sustituye a WhisperModel: anota con qué dispositivo se pidió y puede fallar en la GPU."""

    def __init__(self, falla_en_gpu_al_cargar=False, falla_en_gpu_al_transcribir=False):
        self.pedidos, self.fallos_gpu = [], (falla_en_gpu_al_cargar, falla_en_gpu_al_transcribir)

    def __call__(self, nombre, device, **kw):
        self.pedidos.append(device)
        if device == "cuda" and self.fallos_gpu[0]:
            raise RuntimeError("Library cublas64_12.dll is not found")
        modelo = ModeloFalso([seg("Quiero una cita")])
        if device == "cuda" and self.fallos_gpu[1]:
            bueno, llamadas = modelo.transcribe, []

            def transcribe(audio, **kw):  # la prueba de carga (la primera) pasa; las siguientes se quedan sin memoria
                llamadas.append(1)
                if len(llamadas) > 1:
                    raise RuntimeError("CUDA out of memory")
                return bueno(audio, **kw)
            modelo.transcribe = transcribe
        return modelo


@pytest.fixture
def whisper_falso(monkeypatch):
    import faster_whisper

    def instalar(**kw):
        f = FabricaDeModelos(**kw)
        monkeypatch.setattr(faster_whisper, "WhisperModel", f)
        monkeypatch.setattr(tr, "preparar_cuda", lambda s: [])
        return f
    return instalar


def test_con_auto_usa_la_gpu_si_esta_disponible(whisper_falso, monkeypatch):
    f = whisper_falso()
    monkeypatch.setattr(tr, "vram_libre_mib", lambda: 4000)
    t = nuevo(stt_device="auto")
    assert t.transcribir(pcm(2.0, 0.4), "pcm16").texto == "Quiero una cita"
    assert f.pedidos == ["cuda"] and t.dispositivo.startswith("cuda")


def test_con_poca_memoria_de_gpu_se_va_a_cpu(whisper_falso, monkeypatch):
    f = whisper_falso()
    monkeypatch.setattr(tr, "vram_libre_mib", lambda: 300)  # otra aplicación ocupa la GPU
    t = nuevo(stt_device="auto")
    t.transcribir(pcm(2.0, 0.4), "pcm16")
    assert f.pedidos == ["cpu"] and t.dispositivo.startswith("cpu")


def test_si_faltan_las_librerias_de_cuda_se_va_a_cpu(whisper_falso, monkeypatch):
    f = whisper_falso(falla_en_gpu_al_cargar=True)
    monkeypatch.setattr(tr, "vram_libre_mib", lambda: 4000)
    t = nuevo(stt_device="auto")
    assert t.transcribir(pcm(2.0, 0.4), "pcm16").texto == "Quiero una cita"
    assert f.pedidos == ["cuda", "cpu"]


def test_si_la_gpu_falla_a_mitad_de_la_llamada_se_sigue_en_cpu_sin_perder_la_frase(whisper_falso, monkeypatch):
    f = whisper_falso(falla_en_gpu_al_transcribir=True)
    monkeypatch.setattr(tr, "vram_libre_mib", lambda: 4000)
    t = nuevo(stt_device="auto")
    r = t.transcribir(pcm(2.0, 0.4), "pcm16")
    assert r.texto == "Quiero una cita" and t.dispositivo.startswith("cpu") and t._solo_cpu
    t.transcribir(pcm(2.0, 0.4), "pcm16")
    assert f.pedidos.count("cuda") == 1  # no vuelve a intentar la GPU


def test_con_cpu_pedida_nunca_toca_la_gpu(whisper_falso):
    f = whisper_falso()
    nuevo(stt_device="cpu").transcribir(pcm(2.0, 0.4), "pcm16")
    assert f.pedidos == ["cpu"]


def test_texto_y_confianza_salen_del_modelo():
    t = con_modelo(seg("Quiero cancelar", -0.1), seg("mi cita, por favor.", -0.3))
    r = t.transcribir(pcm(2.0, 0.4), "pcm16")
    assert r.texto == "Quiero cancelar mi cita, por favor." and not r.silencio and r.motivo is None and not r.baja_confianza
    assert r.confianza == pytest.approx(math.exp(-0.2), abs=0.01) and r.segmentos == 2 and r.duracion_audio_s == 2.0


def test_el_modelo_recibe_los_parametros_que_evitan_alucinaciones_y_fuerzan_espanol():
    t = con_modelo(seg("Hola"))
    t.transcribir(pcm(1.0, 0.4), "pcm16")
    kw = t._modelo.llamadas[0]
    assert kw["language"] == "es" and kw["vad_filter"] is True and kw["condition_on_previous_text"] is False and kw["beam_size"] == 1
    assert kw["no_speech_threshold"] == 0.6 and kw["temperature"] == 0.0 and "Colombia" in kw["initial_prompt"]


def test_baja_confianza_se_marca_para_que_el_asistente_pida_repetir():
    r = con_modelo(seg("blablabla confuso", -2.0)).transcribir(pcm(2.0, 0.4), "pcm16")
    assert r.baja_confianza is True and r.texto == "blablabla confuso" and r.confianza < get_settings().stt_confianza_minima


@pytest.mark.parametrize("inventado", ["Gracias por ver el video.", "Subtítulos realizados por la comunidad de Amara.org", tr.PROMPT_INICIAL])
def test_se_descarta_lo_que_whisper_inventa_con_ruido(inventado):
    r = con_modelo(seg(inventado, -0.5)).transcribir(pcm(2.0, 0.4), "pcm16")
    assert r.silencio and r.texto == "" and r.motivo == "alucinacion"


def test_sin_segmentos_es_silencio():
    r = con_modelo().transcribir(pcm(2.0, 0.4), "pcm16")  # el VAD no encontró voz
    assert r.silencio and r.motivo == "silencio"


def test_el_modelo_no_ve_audio_que_ya_se_descarto_por_energia():
    t = con_modelo(seg("no debería llamarse"))
    t.transcribir(pcm(2.0, 0.0), "pcm16")
    assert t._modelo.llamadas == []


def test_dos_transcripciones_a_la_vez_se_serializan_sin_romperse():
    t = con_modelo(seg("ok"))
    resultados = []
    hilos = [threading.Thread(target=lambda: resultados.append(t.transcribir(pcm(1.0, 0.4), "pcm16").texto)) for _ in range(6)]
    [h.start() for h in hilos]
    [h.join(20) for h in hilos]
    assert resultados == ["ok"] * 6


# ======================= API =======================
@pytest.fixture
def api_falsa(monkeypatch):
    t = con_modelo(seg("Quiero cancelar mi cita", -0.2))
    monkeypatch.setattr(tr, "obtener_transcriptor", lambda: t)
    return t


def test_api_transcribe_y_audita_solo_metricas(client, db, api_falsa):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    r = client.post(f"{A}/stt/transcribir", headers=h, files={"archivo": ("a.raw", pcm(2.0, 0.4), "application/octet-stream")}, data={"formato": "pcm16"})
    assert r.status_code == 200
    d = r.json()
    assert d["texto"] == "Quiero cancelar mi cita" and d["silencio"] is False and d["duracion_audio_s"] == 2.0 and set(d) >= {"confianza", "baja_confianza", "duracion_proceso_ms", "dispositivo", "motivo"}
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "stt_transcribir")).one()
    assert set(a.detalle) == {"audio_s", "ms", "silencio"} and "cancelar" not in str(a.detalle)


def test_api_permisos_y_errores(client, db, api_falsa, monkeypatch):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    archivo = {"archivo": ("a.raw", pcm(1.0, 0.4), "application/octet-stream")}
    assert client.post(f"{A}/stt/transcribir", files=archivo).status_code == 401
    assert client.post(f"{A}/stt/transcribir", headers=cabecera(client, "rec@ejemplo.com"), files=archivo, data={"formato": "pcm16"}).status_code == 403
    h = cabecera(client, "coord@ejemplo.com")
    assert client.post(f"{A}/stt/transcribir", headers=h, files={"archivo": ("a.webm", b"no es audio" * 50, "audio/webm")}).json()["error"]["codigo"] == "audio_invalido"
    assert client.post(f"{A}/stt/transcribir", headers=h, files=archivo, data={"formato": "mp3"}).status_code == 422  # formato desconocido
    monkeypatch.setattr(get_settings(), "stt_max_bytes", 500)
    r = client.post(f"{A}/stt/transcribir", headers=h, files=archivo, data={"formato": "pcm16"})
    assert r.status_code == 413 and r.json()["error"]["codigo"] == "audio_muy_grande"


def test_api_estado(client, db, api_falsa):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    e = client.get(f"{A}/stt/estado", headers=cabecera(client, "coord@ejemplo.com")).json()
    assert e["modelo"] == "small" and e["idioma"] == "es" and e["max_segundos"] == 30 and e["cargado"] is True


# ======================= con el modelo real y audio sintetizado =======================
@pytest.fixture(scope="session")
def audios(tmp_path_factory):
    modelo = nuevo()
    if not modelo.estado()["disponible"]:
        pytest.skip("No está el modelo de voz en data/models/whisper")
    if not au.hay_voz_sapi():
        pytest.skip("No hay voz en español del sistema (Microsoft Sabina) para generar audio de prueba")
    carpeta = tmp_path_factory.mktemp("audio")
    frases = {"cita": "Buenos días, quiero agendar una cita de medicina general para el lunes", "cancelar": "Quiero cancelar mi cita, por favor",
              "persona": "Necesito hablar con una persona de la institución",
              "cedula": "Mi cédula es uno cero dos cero tres cero cuatro cero cinco cero y nací el cinco de marzo de mil novecientos ochenta y cinco"}
    return SimpleNamespace(carpeta=carpeta, wav={k: au.sintetizar_wav(v, carpeta / f"{k}.wav") for k, v in frases.items()}, frases=frases)


@pytest.fixture(scope="session")
def modelo_real():
    t = nuevo()
    return t


def palabras(texto):
    return set(tr.normalizar(texto).split())


@pytest.mark.stt
@pytest.mark.parametrize("clave, esperadas", [("cita", {"agendar", "cita", "medicina", "general", "lunes"}), ("cancelar", {"cancelar", "cita"}), ("persona", {"hablar", "persona", "institucion"})])
def test_modelo_real_transcribe_frases_de_recepcion(audios, modelo_real, clave, esperadas):
    r = modelo_real.transcribir(audios.wav[clave].read_bytes())
    assert not r.silencio and esperadas <= palabras(r.texto) and not r.baja_confianza, r.texto


@pytest.mark.stt
def test_modelo_real_de_la_voz_al_documento_y_la_fecha(audios, modelo_real):
    """Lo más importante para el asistente: lo que se DICTA llega a la extracción de identidad."""
    r = modelo_real.transcribir(audios.wav["cedula"].read_bytes())
    assert nlu.extraer_identidad(r.texto, date(2026, 11, 2)) == ("CC", "1020304050", date(1985, 3, 5)), r.texto


@pytest.mark.stt
@pytest.mark.skipif(not au.hay_ffmpeg(), reason="Falta ffmpeg para generar los formatos del navegador")
@pytest.mark.parametrize("nombre, args", [
    ("webm_opus", ("-c:a", "libopus", "-f", "webm")),                 # Chrome / Edge / Firefox (MediaRecorder)
    ("ogg_opus", ("-c:a", "libopus", "-f", "ogg")),                   # Firefox
    ("m4a_aac", ("-c:a", "aac", "-f", "mp4")),                        # Safari
    ("mp3", ("-c:a", "libmp3lame", "-f", "mp3")),
    ("wav_8k_mono", ("-ac", "1", "-ar", "8000", "-f", "wav")),        # telefonía
    ("wav_44k_estereo", ("-ac", "2", "-ar", "44100", "-f", "wav")),
])
def test_modelo_real_entiende_los_formatos_de_los_navegadores(audios, modelo_real, nombre, args):
    datos = au.convertir(audios.wav["cancelar"], audios.carpeta / f"cancelar_{nombre}.bin", *args)
    r = modelo_real.transcribir(datos)
    assert {"cancelar", "cita"} <= palabras(r.texto), (nombre, r.texto)


@pytest.mark.stt
@pytest.mark.skipif(not au.hay_ffmpeg(), reason="Falta ffmpeg")
def test_modelo_real_pcm_crudo_de_la_interfaz_de_voz(audios, modelo_real):
    datos = au.pcm16_16k(audios.wav["cancelar"], audios.carpeta / "cancelar.pcm")
    r = modelo_real.transcribir(datos, "pcm16")
    assert {"cancelar", "cita"} <= palabras(r.texto)


@pytest.mark.stt
def test_modelo_real_con_silencio_ruido_y_pausas(audios, modelo_real):
    assert modelo_real.transcribir(au.wav_bytes(np.zeros(16000 * 3))).texto == ""
    ruido = np.random.default_rng(1).normal(0, 0.05, 16000 * 3)  # ruido de fondo moderado: antes de VAD Whisper suele inventar texto
    assert modelo_real.transcribir(au.wav_bytes(ruido)).texto == ""
    voz = np.frombuffer(au.pcm16_16k(audios.wav["cancelar"], audios.carpeta / "c2.pcm"), dtype="<i2").astype(np.float32) / 32768
    con_pausas = np.concatenate([np.zeros(16000 * 2), voz, np.zeros(16000 * 2)])  # silencio antes y después
    assert {"cancelar", "cita"} <= palabras(modelo_real.transcribir(au.wav_bytes(con_pausas)).texto)


@pytest.mark.stt
def test_modelo_real_velocidad_y_concurrencia(audios, modelo_real):
    datos = audios.wav["cita"].read_bytes()
    modelo_real.transcribir(datos)  # calentamiento
    r = modelo_real.transcribir(datos)
    factor = r.duracion_proceso_ms / 1000 / r.duracion_audio_s
    print(f"\n  STT: {r.duracion_audio_s:.1f} s de audio en {r.duracion_proceso_ms} ms (RTF {factor:.2f}, {r.dispositivo})")
    assert factor < 1.5  # más rápido que el tiempo real, con margen (el equipo puede estar ocupado)
    textos = []
    hilos = [threading.Thread(target=lambda: textos.append(modelo_real.transcribir(datos).texto)) for _ in range(3)]
    [h.start() for h in hilos]
    [h.join(120) for h in hilos]
    assert len(textos) == 3 and all("cita" in palabras(t) for t in textos)


@pytest.mark.stt
def test_modelo_real_de_la_voz_al_asistente(db, audios, modelo_real):
    """Voz → texto → asistente: la identidad dictada se verifica sin que el modelo de lenguaje intervenga."""
    from tests.test_agente import Guion, texto as respuesta_llm
    from app.llm import cliente as modulo_cliente
    from app.services import agente_service
    from tests.test_herramientas import DOC, paciente_real

    paciente_real(db)
    guion = Guion(respuesta_llm("Gracias, ya quedó identificada. ¿En qué le ayudo?"))
    mp = pytest.MonkeyPatch()
    mp.setattr(modulo_cliente, "obtener_cliente", lambda modelo=None: guion)
    try:
        conv, _ = agente_service.iniciar(db, es_prueba=True)
        dicho = modelo_real.transcribir(audios.wav["cedula"].read_bytes()).texto
        _, r = agente_service.enviar(db, conv.id, dicho)
    finally:
        mp.undo()
    assert "buscar_paciente" in r.herramientas and DOC not in r.texto, (dicho, r.texto)

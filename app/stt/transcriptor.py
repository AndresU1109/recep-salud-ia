"""Voz a texto con faster-whisper (local, sin enviar audio a ningún servicio externo).

Whisper INVENTA texto cuando oye silencio o ruido (medido: 1 s de silencio digital → 6 segmentos «.»; es conocido que produce frases como
«Gracias por ver el video»). Por eso: se mide la energía antes, se usa un filtro de voz (VAD), se descartan frases típicas de alucinación
y se devuelve una confianza para que el asistente pida repetir en vez de actuar sobre algo mal entendido.
"""
import io
import logging
import math
import os
import re
import site
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio

log = logging.getLogger("stt")

FRECUENCIA = 16000
MIN_SEGUNDOS = 0.3
# Contexto del dominio: ayuda con «cédula», «reprogramar»… Sin voz no se usa (el VAD lo evita): con silencio Whisper tiende a repetir el prompt.
PROMPT_INICIAL = "Conversación con la recepción de una institución de salud en Colombia: cita médica, cédula, fecha de nacimiento, cancelar, reprogramar."

_ALUCINACIONES = [
    "subtitulos realizados por la comunidad de amara org", "subtitulado por la comunidad de amara org", "amara org", "gracias por ver el video", "gracias por ver",
    "suscribete", "no olvides suscribirte", "dale like", "hasta la proxima", "nos vemos en el proximo video", "musica", "aplausos", "subtitulos por", "gracias por su atencion",
]


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", "".join(c for c in t if unicodedata.category(c) != "Mn"))).strip()


@dataclass
class ResultadoSTT:
    texto: str
    confianza: float  # 0..1 (probabilidad media de lo transcrito)
    silencio: bool
    motivo: str | None  # por qué no hay texto: silencio | muy_corto | alucinacion
    baja_confianza: bool
    duracion_audio_s: float
    duracion_proceso_ms: int
    dispositivo: str
    segmentos: int = 0


def energia_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return 20 * math.log10(rms) if rms > 1e-9 else -120.0


def es_alucinacion(texto: str) -> bool:
    """Frases que Whisper suele inventar con silencio o ruido. Solo cuenta si el texto ES prácticamente solo eso."""
    t = normalizar(texto)
    if not t:
        return True
    return any(t == a or (a in t and len(t) <= len(a) + 12) for a in _ALUCINACIONES)


def decodificar(datos: bytes, formato: str = "auto") -> np.ndarray:
    """Devuelve audio mono float32 a 16 kHz. formato 'pcm16' = PCM crudo de 16 bits a 16 kHz (lo que enviará la interfaz de voz);
    'auto' = cualquier contenedor que PyAV entienda (webm/opus y ogg de Chrome y Firefox, mp4/aac de Safari, wav, mp3…)."""
    if formato == "pcm16":
        if len(datos) % 2:
            datos = datos[:-1]
        return np.frombuffer(datos, dtype="<i2").astype(np.float32) / 32768.0
    from faster_whisper.audio import decode_audio

    try:
        return decode_audio(io.BytesIO(datos), sampling_rate=FRECUENCIA)
    except Exception as exc:  # noqa: BLE001 - PyAV lanza varios tipos según el formato
        raise ErrorNegocio("audio_invalido", "No se pudo leer el audio. Formatos aceptados: webm, ogg, mp4/m4a, wav, mp3.", 422) from exc


def preparar_cuda(s: Settings) -> list[str]:
    """CTranslate2 busca cuBLAS y cuDNN en el PATH. Se añaden las carpetas indicadas en STT_CUDA_DLL_DIRS y las `nvidia/*/bin` de pip, si existen."""
    candidatas = [d for d in s.stt_cuda_dll_dirs.split(";") if d.strip()]
    for raiz in [Path(p) / "nvidia" for p in site.getsitepackages() + [site.getusersitepackages()]]:
        candidatas += [str(raiz / sub / "bin") for sub in ("cublas", "cudnn", "cuda_nvrtc")]
    puestas = []
    for d in dict.fromkeys(candidatas):
        if Path(d).is_dir():
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            puestas.append(d)
    return puestas


def vram_libre_mib() -> int | None:
    """Memoria libre de la GPU en MiB (nvidia-smi), o None si no se puede saber."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(r.stdout.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


class Transcriptor:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._modelo = None
        self.dispositivo = "sin cargar"
        self._candado = threading.Lock()  # un solo modelo: se transcribe de a una intervención
        self._solo_cpu = False  # la GPU falló una vez: no se vuelve a intentar hasta reiniciar

    def _cargar(self):
        from faster_whisper import WhisperModel

        carpeta = Path(self.s.stt_models_dir)
        pedido = self.s.stt_device
        intentos = [("cuda", "int8_float16"), ("cpu", self.s.stt_compute_type)] if pedido == "auto" else [(pedido, self.s.stt_compute_type if pedido == "cpu" else "int8_float16")]
        if self._solo_cpu:
            intentos = [("cpu", self.s.stt_compute_type)]
        ultimo = None
        for dispositivo, tipo in intentos:
            try:
                if dispositivo == "cuda":
                    preparar_cuda(self.s)
                    libre = vram_libre_mib()
                    if pedido == "auto" and libre is not None and libre < self.s.stt_vram_minima_mib:  # otra aplicación (el LLM, un editor de video) ocupa la GPU
                        raise MemoryError(f"solo hay {libre} MiB libres en la GPU")
                modelo = WhisperModel(self.s.stt_model, device=dispositivo, compute_type=tipo, download_root=str(carpeta), local_files_only=True,
                                      cpu_threads=self.s.stt_cpu_threads if dispositivo == "cpu" else 0)
                list(modelo.transcribe(np.zeros(FRECUENCIA, dtype=np.float32), language=self.s.voice_language)[0])  # prueba real: con CUDA falla aquí si faltan librerías
                self.dispositivo = f"{dispositivo}/{tipo}"
                log.info("stt modelo=%s dispositivo=%s", self.s.stt_model, self.dispositivo)
                return modelo
            except Exception as exc:  # noqa: BLE001
                ultimo = exc
                log.warning("stt no pudo usar %s (%s)", dispositivo, type(exc).__name__)
        raise ErrorNegocio("stt_no_disponible", f"No se pudo cargar el modelo de voz ({type(ultimo).__name__}). ¿Está en {carpeta}?", 503)

    def modelo(self):
        with self._candado:
            if self._modelo is None:
                self._modelo = self._cargar()
            return self._modelo

    def _transcribir(self, audio: np.ndarray, idioma: str | None) -> list:
        s = self.s
        modelo = self.modelo()
        with self._candado:
            segmentos, _ = modelo.transcribe(audio, language=idioma or s.voice_language, beam_size=s.stt_beam_size, vad_filter=True,
                                             vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False, initial_prompt=PROMPT_INICIAL,
                                             no_speech_threshold=0.6, temperature=0.0)
            return list(segmentos)

    def estado(self) -> dict:
        return {"modelo": self.s.stt_model, "cargado": self._modelo is not None, "dispositivo": self.dispositivo, "idioma": self.s.voice_language,
                "max_segundos": self.s.stt_max_segundos, "carpeta_modelos": self.s.stt_models_dir, "disponible": (Path(self.s.stt_models_dir) / f"models--Systran--faster-whisper-{self.s.stt_model}").exists()}

    def transcribir(self, datos: bytes, formato: str = "auto", idioma: str | None = None) -> ResultadoSTT:
        s = self.s
        if not datos:
            raise ErrorNegocio("audio_vacio", "No llegó audio.", 422)
        if len(datos) > s.stt_max_bytes:
            raise ErrorNegocio("audio_muy_grande", f"El audio supera el máximo de {s.stt_max_bytes // (1024 * 1024)} MB.", 413)
        t0 = time.perf_counter()
        audio = decodificar(datos, formato)
        duracion = audio.size / FRECUENCIA
        if duracion > s.stt_max_segundos:
            raise ErrorNegocio("audio_muy_largo", f"Cada intervención puede durar como máximo {s.stt_max_segundos} segundos.", 413)

        def vacio(motivo: str) -> ResultadoSTT:
            return ResultadoSTT("", 0.0, True, motivo, False, round(duracion, 2), int((time.perf_counter() - t0) * 1000), self.dispositivo)

        if duracion < MIN_SEGUNDOS:
            return vacio("muy_corto")
        if energia_dbfs(audio) < s.stt_umbral_silencio_dbfs:
            return vacio("silencio")

        try:
            lista = self._transcribir(audio, idioma)
        except Exception as exc:  # noqa: BLE001 - un fallo de CUDA a mitad de la llamada (memoria, controlador) no debe dejar al paciente sin voz
            if not self.dispositivo.startswith("cuda"):
                raise
            log.warning("stt: falló la GPU (%s); se sigue en CPU", type(exc).__name__)
            with self._candado:
                self._solo_cpu, self._modelo = True, None
            lista = self._transcribir(audio, idioma)
        if not lista:
            return vacio("silencio")
        texto = " ".join(x.text.strip() for x in lista).strip()
        if es_alucinacion(texto) or normalizar(texto) == normalizar(PROMPT_INICIAL):
            return vacio("alucinacion")
        confianza = float(np.clip(math.exp(np.mean([x.avg_logprob for x in lista])), 0.0, 1.0))
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("stt audio_s=%.1f ms=%d confianza=%.2f segmentos=%d", duracion, ms, confianza, len(lista))
        return ResultadoSTT(texto, round(confianza, 3), False, None, confianza < s.stt_confianza_minima, round(duracion, 2), ms, self.dispositivo, len(lista))


_instancia: Transcriptor | None = None
_candado_instancia = threading.Lock()


def obtener_transcriptor() -> Transcriptor:
    """Punto único (singleton: el modelo pesa ~0.5 GB); las pruebas lo reemplazan por uno falso."""
    global _instancia
    with _candado_instancia:
        if _instancia is None:
            _instancia = Transcriptor()
        return _instancia

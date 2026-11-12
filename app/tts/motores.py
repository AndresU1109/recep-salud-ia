"""Motores de síntesis. Los dos trabajan sin conexión y devuelven un WAV de 16 bits, mono."""
import base64
import io
import json
import logging
import os
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.core.errores import ErrorNegocio

log = logging.getLogger("tts")


@dataclass
class Audio:
    wav: bytes
    frecuencia: int
    duracion_s: float
    motor: str


def _duracion(wav: bytes) -> tuple[int, float]:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getframerate(), w.getnframes() / w.getframerate()


class Motor(Protocol):
    nombre: str

    def disponible(self) -> bool: ...

    def sintetizar(self, texto: str, velocidad: float) -> Audio: ...


def _no_disponible(detalle: str) -> ErrorNegocio:
    return ErrorNegocio("tts_no_disponible", f"El servicio de voz no está disponible: {detalle}", 503)


# ======================= Piper (neuronal, ONNX) =======================
class MotorPiper:
    nombre = "piper"

    def __init__(self, s: Settings):
        self.ruta = Path(s.tts_piper_modelo)
        self._voz = None
        self._candado = threading.Lock()

    def disponible(self) -> bool:
        if not self.ruta.is_file():
            return False
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def _cargar(self):
        from piper import PiperVoice

        voz = PiperVoice.load(str(self.ruta))
        with wave.open(io.BytesIO(), "wb") as w:  # la primera síntesis tarda varios segundos (medido: 5.6 s): se hace al cargar, no con un paciente esperando
            voz.synthesize_wav("hola", w)
        log.info("tts piper cargado modelo=%s", self.ruta.name)
        return voz

    def precargar(self) -> None:
        with self._candado:
            if self._voz is None:
                self._voz = self._cargar()

    def sintetizar(self, texto: str, velocidad: float) -> Audio:
        from piper import SynthesisConfig

        try:
            self.precargar()
            buffer = io.BytesIO()
            with self._candado, wave.open(buffer, "wb") as w:
                self._voz.synthesize_wav(texto, w, SynthesisConfig(length_scale=1.0 / max(velocidad, 0.3)))
        except ErrorNegocio:
            raise
        except Exception as exc:  # noqa: BLE001 - onnxruntime/espeak lanzan tipos propios
            raise _no_disponible(f"Piper falló ({type(exc).__name__})") from exc
        wav = buffer.getvalue()
        frecuencia, duracion = _duracion(wav)
        return Audio(wav, frecuencia, duracion, self.nombre)


# ======================= Kokoro (neuronal, ONNX; varias voces en español) =======================
# El modelo (326 MB) se carga UNA vez y lo comparten las instancias de todas las voces: cambiar de voz no recarga nada.
_KOKORO_LOCK = threading.Lock()
_KOKORO: dict[str, object] = {}
VOCES_KOKORO_ES = {"ef_dora": "Dora (mujer)", "em_alex": "Alex (hombre)", "em_santa": "Santa (hombre)"}  # las voces españolas de Kokoro v1.0; las demás hablan otro idioma


def voces_kokoro(ruta_voces: str) -> list[str]:
    """Voces españolas presentes en el archivo de voces (sin cargar el modelo)."""
    try:
        import numpy as np

        with np.load(ruta_voces) as f:
            return [v for v in VOCES_KOKORO_ES if v in f.files]
    except (OSError, ValueError):
        return []


class MotorKokoro:
    nombre = "kokoro"

    def __init__(self, s: Settings, voz: str = "ef_dora"):
        self.modelo, self.archivo_voces, self.voz = Path(s.tts_kokoro_modelo), Path(s.tts_kokoro_voces), voz

    def disponible(self) -> bool:
        if not (self.modelo.is_file() and self.archivo_voces.is_file()) or self.voz not in voces_kokoro(str(self.archivo_voces)):
            return False
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False
        return True

    def _cargar(self):
        with _KOKORO_LOCK:
            clave = str(self.modelo)
            if clave not in _KOKORO:
                from kokoro_onnx import Kokoro

                k = Kokoro(str(self.modelo), str(self.archivo_voces))
                k.create("hola", voice=self.voz, speed=1.0, lang="es")  # la primera síntesis es lenta: se hace al cargar
                _KOKORO[clave] = k
                log.info("tts kokoro cargado modelo=%s", self.modelo.name)
            return _KOKORO[clave]

    def precargar(self) -> None:
        self._cargar()

    def sintetizar(self, texto: str, velocidad: float) -> Audio:
        import numpy as np

        try:
            k = self._cargar()
            with _KOKORO_LOCK:  # onnxruntime comparte la sesión: una síntesis a la vez
                muestras, frecuencia = k.create(texto, voice=self.voz, speed=max(0.5, min(velocidad, 2.0)), lang="es")
        except ErrorNegocio:
            raise
        except Exception as exc:  # noqa: BLE001 - onnxruntime/espeak lanzan tipos propios
            raise _no_disponible(f"Kokoro falló ({type(exc).__name__})") from exc
        pcm = (np.clip(muestras, -1.0, 1.0) * 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(frecuencia))
            w.writeframes(pcm.tobytes())
        wav = buffer.getvalue()
        _, duracion = _duracion(wav)
        return Audio(wav, int(frecuencia), duracion, self.nombre)


# ======================= Voz de Windows (SAPI) =======================
# Un solo PowerShell vivo que carga System.Speech una vez y atiende peticiones por stdin: arrancar PowerShell por cada frase cuesta ~0.5 s.
# El texto viaja en base64 (la consola de Windows rompe los acentos) y el script se pasa con -Command (no hay que cambiar la política de ejecución).
_GUION_SAPI = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($env:RECEP_VOZ)
[Console]::Out.WriteLine('LISTO'); [Console]::Out.Flush()
while ($true) {
  $linea = [Console]::In.ReadLine()
  if ($null -eq $linea) { break }
  try {
    $p = $linea | ConvertFrom-Json
    $s.Rate = [int]$p.r
    $s.SetOutputToWaveFile($p.o)
    $s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p.t)))
    $s.SetOutputToNull()
    [Console]::Out.WriteLine('OK')
  } catch { [Console]::Out.WriteLine('ERR ' + ($_.Exception.Message -replace '[\r\n]+', ' ')) }
  [Console]::Out.Flush()
}
"""


class MotorSapi:
    nombre = "sapi"

    def __init__(self, s: Settings):
        self.voz = s.tts_voice
        self._proc: subprocess.Popen | None = None
        self._candado = threading.Lock()

    def disponible(self) -> bool:
        return os.name == "nt"

    def _arrancar(self) -> subprocess.Popen:
        proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", _GUION_SAPI], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={**os.environ, "RECEP_VOZ": self.voz}, text=True, encoding="utf-8", bufsize=1)
        linea = self._leer(proc, 30)
        if linea != "LISTO":
            proc.kill()
            raise _no_disponible(f"no se pudo iniciar la voz de Windows «{self.voz}»")
        return proc

    @staticmethod
    def _leer(proc: subprocess.Popen, segundos: float) -> str:
        resultado: list[str] = []
        hilo = threading.Thread(target=lambda: resultado.append(proc.stdout.readline().strip()), daemon=True)
        hilo.start()
        hilo.join(segundos)
        if hilo.is_alive():
            proc.kill()
            raise _no_disponible("la voz de Windows no respondió a tiempo")
        return resultado[0] if resultado else ""

    @staticmethod
    def _rate(velocidad: float) -> int:
        return max(-10, min(10, round((velocidad - 1.0) * 10)))

    def sintetizar(self, texto: str, velocidad: float) -> Audio:
        with self._candado:
            for intento in (1, 2):  # si el trabajador murió, se reinicia una vez
                try:
                    if self._proc is None or self._proc.poll() is not None:
                        self._proc = self._arrancar()
                    fd, ruta = tempfile.mkstemp(suffix=".wav", prefix="tts_")
                    os.close(fd)
                    try:
                        peticion = json.dumps({"t": base64.b64encode(texto.encode("utf-8")).decode(), "r": self._rate(velocidad), "o": ruta})
                        self._proc.stdin.write(peticion + "\n")
                        self._proc.stdin.flush()
                        respuesta = self._leer(self._proc, 30)
                        if respuesta != "OK":
                            raise _no_disponible(f"la voz de Windows falló ({respuesta[:60] or 'sin respuesta'})")
                        wav = Path(ruta).read_bytes()
                    finally:
                        Path(ruta).unlink(missing_ok=True)
                    frecuencia, duracion = _duracion(wav)
                    return Audio(wav, frecuencia, duracion, self.nombre)
                except (BrokenPipeError, OSError):
                    self._proc = None
                    if intento == 2:
                        raise _no_disponible("la voz de Windows se cerró") from None
                except ErrorNegocio:
                    self._proc = None  # estado dudoso: la próxima vez arranca de cero
                    if intento == 2:
                        raise
        raise _no_disponible("no se pudo sintetizar")

    def cerrar(self) -> None:
        with self._candado:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
            self._proc = None

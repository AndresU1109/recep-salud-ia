"""Audio de prueba: se sintetiza con la voz del sistema (Windows SAPI, «Microsoft Sabina», es-MX) y se convierte con ffmpeg a los
formatos que mandan los navegadores. No hay grabaciones de personas: todo es ficticio y se genera al vuelo."""
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

VOZ = "Microsoft Sabina Desktop"


def hay_voz_sapi() -> bool:
    if shutil.which("powershell") is None:
        return False
    ps = "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | % { $_.VoiceInfo.Name }"
    try:
        return VOZ in subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return False


def hay_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def sintetizar_wav(texto: str, ruta: Path) -> Path:
    ps = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice('{VOZ}'); "
          f"$s.SetOutputToWaveFile('{ruta}'); $s.Speak('{texto.replace(chr(39), '')}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=60)
    return ruta


def convertir(origen: Path, destino: Path, *args: str) -> bytes:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(origen), *args, str(destino)], check=True, timeout=60)
    return destino.read_bytes()


def pcm16_16k(origen: Path, destino: Path) -> bytes:
    """PCM crudo de 16 bits, mono, 16 kHz (el formato que enviará la interfaz de voz)."""
    return convertir(origen, destino, "-ac", "1", "-ar", "16000", "-f", "s16le")


def wav_bytes(muestras: np.ndarray, frecuencia: int = 16000) -> bytes:
    import io

    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(frecuencia)
        w.writeframes((np.clip(muestras, -1, 1) * 32767).astype("<i2").tobytes())
    return b.getvalue()


def duracion_wav(ruta: Path) -> float:
    with wave.open(str(ruta)) as w:
        return w.getnframes() / w.getframerate()

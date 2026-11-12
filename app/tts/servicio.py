"""Servicio de voz: elige el motor, prepara el texto, sintetiza por frases y guarda en caché lo que se repite
(saludo, aviso de emergencia, confirmaciones). El audio no se guarda en disco ni se asocia a ninguna persona."""
import hashlib
import logging
import threading
from pathlib import Path
from collections import OrderedDict
from collections.abc import Iterator

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.tts.motores import VOCES_KOKORO_ES, Audio, Motor, MotorKokoro, MotorPiper, MotorSapi, voces_kokoro
from app.tts.preparacion import dividir_en_frases, preparar_para_voz

log = logging.getLogger("tts")
MAX_BYTES_CACHE = 24 * 1024 * 1024


class ServicioTTS:
    def __init__(self, settings: Settings | None = None, motores: list[Motor] | None = None):
        self.s = settings or get_settings()
        self._motores = motores if motores is not None else [MotorPiper(self.s), MotorKokoro(self.s), MotorSapi(self.s)]
        self._kokoro: dict[str, MotorKokoro] = {}  # una instancia por voz (comparten el modelo cargado)
        self._cache: OrderedDict[str, Audio] = OrderedDict()
        self._bytes = 0
        self._candado = threading.Lock()

    def _motor_de_voz(self, voz: str) -> Motor | None:
        """«piper», «sapi» o «kokoro:ef_dora» → el motor listo para esa voz (None si no existe)."""
        nombre, _, detalle = voz.partition(":")
        if nombre == "kokoro" and any(m.nombre == "kokoro" for m in self._motores):
            return self._kokoro.setdefault(detalle or "ef_dora", MotorKokoro(self.s, detalle or "ef_dora"))
        return next((m for m in self._motores if m.nombre == nombre), None)

    def motor(self, voz: str | None = None) -> Motor:
        """La voz pedida (o la elegida en el panel); si no hay o no está disponible, el motor configurado: con 'auto', Piper si tiene su voz y, si no, la voz de Windows."""
        elegida = voz if voz is not None else self.s.tts_voz
        if elegida:
            m = self._motor_de_voz(elegida)
            if m is not None and m.disponible():
                return m
            if voz is not None:  # la pidieron expresamente (una muestra): no se sustituye en silencio
                raise ErrorNegocio("voz_no_disponible", f"La voz «{voz}» no está disponible en este equipo.", 422)
            log.warning("tts: la voz elegida %s no está disponible; se usa la automática", elegida)
        pedido = self.s.tts_motor
        candidatos = self._motores if pedido == "auto" else [m for m in self._motores if m.nombre == pedido]
        for m in candidatos:
            if m.disponible():
                return m
        raise ErrorNegocio("tts_no_disponible", "No hay ninguna voz disponible: falta el modelo de Piper (data/models/tts/piper) y la voz de Windows no responde.", 503)

    def voces(self) -> list[dict]:
        """Las voces que se pueden elegir (solo las que están instaladas), con la que suena ahora marcada."""
        out = [{"id": "", "motor": "auto", "nombre": "Automática (la mejor disponible)"}]
        for m in self._motores:
            if not m.disponible() and m.nombre != "kokoro":
                continue
            if m.nombre == "piper":
                out.append({"id": "piper", "motor": "piper", "nombre": f"{Path(self.s.tts_piper_modelo).stem.replace('es_MX-', '').capitalize()} (español de México)"})
            elif m.nombre == "kokoro":
                out += [{"id": f"kokoro:{v}", "motor": "kokoro", "nombre": VOCES_KOKORO_ES[v]} for v in voces_kokoro(self.s.tts_kokoro_voces) if self._motor_de_voz(f"kokoro:{v}").disponible()]
            elif m.nombre == "sapi":
                out.append({"id": "sapi", "motor": "sapi", "nombre": f"{self.s.tts_voice} (voz de Windows)"})
            else:
                out.append({"id": m.nombre, "motor": m.nombre, "nombre": m.nombre})
        return out

    def estado(self) -> dict:
        return {"motor_configurado": self.s.tts_motor, "voz_elegida": self.s.tts_voz, "motor_en_uso": next((m.nombre for m in self._motores if m.disponible() and (self.s.tts_motor in ("auto", m.nombre))), None),
                "motores": {m.nombre: m.disponible() for m in self._motores}, "voces": self.voces(), "velocidad": self.s.tts_velocidad, "max_caracteres": self.s.tts_max_caracteres,
                "en_cache": len(self._cache), "idioma": self.s.voice_language}

    def precargar(self) -> None:
        """Calienta el motor (la primera síntesis de Piper tarda ~5 s). Se llama al arrancar, en segundo plano."""
        try:
            m = self.motor()
            if hasattr(m, "precargar"):
                m.precargar()
            else:
                m.sintetizar("hola", 1.0)
        except ErrorNegocio as e:
            log.warning("tts: no se pudo precargar (%s)", e.codigo)

    # ---------- caché ----------
    def _clave(self, motor: str, texto: str, velocidad: float) -> str:
        return hashlib.sha256(f"{motor}|{self.s.tts_voice}|{velocidad}|{texto}".encode()).hexdigest()  # «motor» ya incluye la voz (p. ej. kokoro:ef_dora)

    def _guardar(self, clave: str, audio: Audio) -> None:
        with self._candado:
            self._cache[clave] = audio
            self._cache.move_to_end(clave)
            self._bytes += len(audio.wav)
            while len(self._cache) > self.s.tts_cache_entradas or self._bytes > MAX_BYTES_CACHE:
                _, viejo = self._cache.popitem(last=False)
                self._bytes -= len(viejo.wav)

    # ---------- síntesis ----------
    def _sintetizar_una(self, motor: Motor, texto: str, velocidad: float) -> Audio:
        clave = self._clave(f"{motor.nombre}:{getattr(motor, 'voz', '')}", texto, velocidad)
        with self._candado:
            if clave in self._cache:
                self._cache.move_to_end(clave)
                return self._cache[clave]
        audio = motor.sintetizar(texto, velocidad)
        self._guardar(clave, audio)
        return audio

    def preparar(self, texto: str) -> str:
        t = preparar_para_voz(texto or "")
        if not t:
            raise ErrorNegocio("texto_vacio", "No hay texto para leer.", 422)
        if len(t) > self.s.tts_max_caracteres:
            raise ErrorNegocio("texto_muy_largo", f"El texto para leer supera los {self.s.tts_max_caracteres} caracteres.", 422)
        return t

    def sintetizar_frases(self, texto: str, velocidad: float | None = None, voz: str | None = None) -> Iterator[tuple[str, Audio]]:
        """Un audio por frase, en orden. Para reproducir la primera mientras se sintetiza el resto (menor latencia percibida)."""
        preparado = self.preparar(texto)
        v = velocidad or self.s.tts_velocidad
        motor = self.motor(voz)
        for frase in dividir_en_frases(preparado):
            yield frase, self._sintetizar_una(motor, frase, v)

    def sintetizar(self, texto: str, velocidad: float | None = None, voz: str | None = None) -> Audio:
        """Todo el texto en un solo WAV (las frases se unen sin saltos)."""
        import io
        import wave

        partes = [a for _, a in self.sintetizar_frases(texto, velocidad, voz)]
        if len(partes) == 1:
            return partes[0]
        salida = io.BytesIO()
        with wave.open(salida, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(partes[0].frecuencia)
            for a in partes:
                with wave.open(io.BytesIO(a.wav)) as origen:
                    w.writeframes(origen.readframes(origen.getnframes()))
        wav = salida.getvalue()
        return Audio(wav, partes[0].frecuencia, sum(a.duracion_s for a in partes), partes[0].motor)


_instancia: ServicioTTS | None = None
_candado_instancia = threading.Lock()


def obtener_tts() -> ServicioTTS:
    """Punto único (singleton: el modelo de voz pesa ~60 MB en memoria); las pruebas lo reemplazan por uno falso."""
    global _instancia
    with _candado_instancia:
        if _instancia is None:
            _instancia = ServicioTTS()
        return _instancia

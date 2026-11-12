"""Activación por voz: el panel escucha en «modo espera» y, cuando alguien dice la frase de activación («hola asistente»), inicia la llamada sola.

El audio se transcribe en este servidor, se compara con las frases y se descarta: NO se guarda, NO se registra y NO se devuelve lo que dijo la
persona; la respuesta es solo sí o no."""
import re
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.agents.seguridad import normalizar
from app.services import config_ia_service

UMBRAL = 0.82
MAX_PALABRAS = 8  # una frase de activación es corta: una conversación entera captada por el micrófono no activa nada


def frases(db: Session) -> list[str]:
    """Las frases que activan: la elegida en el panel (si hay) y las del nombre del asistente. Siempre «hola asistente» / «oye asistente»."""
    c = config_ia_service.efectiva(db)
    nombre = normalizar(c.nombre_asistente)
    base = ["hola asistente", "oye asistente"]
    propio = [] if not nombre or nombre == "asistente virtual" else [f"hola {nombre}", f"oye {nombre}"]
    elegida = normalizar(c.frase_activacion) if c.frase_activacion else ""
    return list(dict.fromkeys(([elegida] if elegida else []) + propio + base))


def coincide(texto: str, lista: list[str]) -> bool:
    """True si el texto dicho es (parecido a) alguna frase de activación, aunque el reconocimiento la deforme («ola asistente», «hola asistente virtual»)."""
    t = normalizar(texto)
    palabras = t.split()
    if not palabras or len(palabras) > MAX_PALABRAS:
        return False
    for f in lista:
        n = len(f.split())
        ventanas = [" ".join(palabras[i:i + n]) for i in range(max(1, len(palabras) - n + 1))]
        if any(SequenceMatcher(None, v, f).ratio() >= UMBRAL for v in ventanas):
            return True
    return False

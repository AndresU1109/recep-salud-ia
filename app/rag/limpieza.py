"""Limpieza de texto extraído: deja párrafos limpios sin alterar el contenido."""
import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​‎‏﻿]")
_SOLO_PAGINA = re.compile(r"^\s*(?:p[áa]g(?:ina)?\.?\s*)?\d{1,4}(?:\s*(?:de|/)\s*\d{1,4})?\s*$", re.IGNORECASE)


_NUMERACION = re.compile(r"\bp[áa]g(?:ina)?\b|\bpage\b|\b\d+\s*(?:de|/|of)\s*\d+\b", re.IGNORECASE)


def quitar_encabezados_y_pies(paginas: list[str]) -> list[str]:
    """Quita líneas cortas que se repiten en la mayoría de las páginas (membretes, pies) y los números de página."""
    if len(paginas) < 3:
        return [_sin_numeracion(p) for p in paginas]
    # Los dígitos solo se ignoran en líneas de numeración ("Página 3 de 9"); en el resto, "Paso 1" y "Paso 2" son líneas distintas.
    clave = lambda l: re.sub(r"\d+", "#", l.strip().lower()) if _NUMERACION.search(l) else l.strip().lower()  # noqa: E731
    conteo: dict[str, int] = {}
    for p in paginas:
        for k in {clave(l) for l in p.splitlines() if 0 < len(l.strip()) <= 80}:
            conteo[k] = conteo.get(k, 0) + 1
    umbral = max(3, int(len(paginas) * 0.6))
    repetidas = {k for k, n in conteo.items() if n >= umbral}
    return [_sin_numeracion("\n".join(l for l in p.splitlines() if clave(l) not in repetidas)) for p in paginas]


def _sin_numeracion(pagina: str) -> str:
    return "\n".join(l for l in pagina.splitlines() if not _SOLO_PAGINA.match(l))


def limpiar(texto: str) -> str:
    t = unicodedata.normalize("NFC", texto)
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    t = _CONTROL.sub("", t)
    t = re.sub(r"(?<=[a-záéíóúñ])-\n(?=[a-záéíóúñ])", "", t)             # palabra partida con guion al final de línea
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    t = re.sub(r"(?<![.:;?!\n])\n(?=[a-záéíóúñ(¿¡])", " ", t)             # renglón cortado a mitad de frase → se une
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

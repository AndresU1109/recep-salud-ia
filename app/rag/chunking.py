"""Fragmentación por párrafos con solapamiento. Un fragmento ≈ 1000 caracteres (~250 tokens): cabe de sobra en el
contexto del modelo de embeddings (2048 tokens) y es lo bastante específico para recuperar la regla exacta."""
import re

_FIN_FRASE = re.compile(r"(?<=[.!?;:])\s+")


def tokens_aprox(texto: str) -> int:
    return max(1, len(texto) // 4)


def _es_titulo(p: str) -> bool:
    return len(p) <= 90 and "\n" not in p and not re.search(r"[.!?;,]$", p)


def _partir_largo(parrafo: str, maximo: int) -> list[str]:
    piezas, actual = [], ""
    for frase in _FIN_FRASE.split(parrafo):
        while len(frase) > maximo:  # frase gigante sin puntuación: se parte por palabras
            corte = frase.rfind(" ", 0, maximo)
            corte = corte if corte > 0 else maximo
            if actual:
                piezas.append(actual)
                actual = ""
            piezas.append(frase[:corte].strip())
            frase = frase[corte:].strip()
        if actual and len(actual) + 1 + len(frase) > maximo:
            piezas.append(actual)
            actual = frase
        else:
            actual = f"{actual} {frase}".strip()
    if actual:
        piezas.append(actual)
    return piezas


def _cola(texto: str, n: int) -> str:
    """Las últimas frases completas que quepan en n caracteres (para solapar fragmentos)."""
    cola = ""
    for frase in reversed(_FIN_FRASE.split(texto)):
        if len(frase) + len(cola) + 1 > n:
            break
        cola = f"{frase} {cola}".strip()
    return cola


def dividir(texto: str, maximo: int = 1000, solapamiento: int = 150) -> list[str]:
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    unidades: list[str] = []
    for p in parrafos:
        for pieza in (_partir_largo(p, maximo) if len(p) > maximo else [p]):
            if unidades and _es_titulo(unidades[-1]) and len(unidades[-1]) + 2 + len(pieza) <= maximo:
                unidades[-1] = f"{unidades[-1]}\n{pieza}"  # un título viaja junto al texto que introduce
            else:
                unidades.append(pieza)
    fragmentos, actual = [], ""
    for u in unidades:
        if actual and len(actual) + 2 + len(u) > maximo:
            fragmentos.append(actual)
            cola = _cola(actual, solapamiento)
            actual = f"{cola}\n\n{u}" if cola and len(cola) + 2 + len(u) <= maximo + solapamiento else u
        else:
            actual = f"{actual}\n\n{u}" if actual else u
    if actual:
        fragmentos.append(actual)
    return fragmentos

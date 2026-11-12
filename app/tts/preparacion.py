"""Texto → texto «para leer en voz alta». Los motores TTS leen mal las abreviaturas, las horas con «a. m.» y números como 123 (dicen
«ciento veintitrés»); aquí se dejan como las diría una recepcionista. No cambia el significado: solo cómo se pronuncia."""
import re
import unicodedata

_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️‍]")
_ABREVIATURAS = [(r"\bDra\.", "doctora"), (r"\bDr\.", "doctor"), (r"\bSra\.", "señora"), (r"\bSr\.", "señor"), (r"\bEPS\b", "E P S"), (r"\bIPS\b", "I P S"),
                 (r"\bNo\.\s*(?=\d)", "número "), (r"\bn\.º\s*", "número "), (r"\bTel\.", "teléfono"), (r"\bCC\b", "cédula de ciudadanía"), (r"\bTI\b", "tarjeta de identidad")]


_HORA_AM_PM = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*([ap])\s*\.?\s*m\b\.?", re.IGNORECASE)  # 8:20 a. m. · 8:20 am · 8.20 a.m.


def _hora_am_pm(m: re.Match) -> str:
    """'8:20 a. m.' → '8 y 20 de la mañana'; '3:00 p. m.' → '3 de la tarde'; '8:05 p. m.' → '8 y 5 de la noche'."""
    h, mi, am = int(m.group(1)), int(m.group(2)), m.group(3).lower() == "a"
    dicha = str(h) if mi == 0 else f"{h} y {mi}"
    if am:
        franja = "de la madrugada" if h == 12 else "de la mañana"
    else:
        franja = "del mediodía" if h == 12 else ("de la tarde" if h < 7 else "de la noche")
    return f"{dicha} {franja}"


def preparar_para_voz(texto: str) -> str:
    t = unicodedata.normalize("NFC", texto)
    t = _EMOJI.sub("", t)
    t = re.sub(r"[*_#`>~|\[\]{}]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)
    t = _HORA_AM_PM.sub(_hora_am_pm, t)
    t = re.sub(r"\b(\d{1,2}):00\b", r"\1 en punto", t)  # «a las 14:00» → «a las 14 en punto» (formato de 24 h)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m.group(1))} y {m.group(2)}", t)
    t = re.sub(r"\b123\b", "uno dos tres", t)  # la línea de emergencias
    for patron, reemplazo in _ABREVIATURAS:
        t = re.sub(patron, reemplazo, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


_FIN = re.compile(r"(?<=[.!?…;:])\s+")


def dividir_en_frases(texto: str, maximo: int = 220) -> list[str]:
    """Una frase por trozo (de a lo sumo ``maximo`` caracteres): el primer audio sale rápido, el resto se sintetiza mientras suena,
    y las frases que se repiten («Buenos días.») salen de la caché aunque cambie lo que las rodea."""
    frases: list[str] = []
    for parte in _FIN.split(texto.strip()):
        while len(parte) > maximo:  # frase larguísima sin puntuación: se corta en una coma o, en último caso, en un espacio
            corte = max(parte.rfind(",", 0, maximo), parte.rfind(" ", 0, maximo))
            corte = corte if corte > 0 else maximo
            frases.append(parte[:corte].strip())
            parte = parte[corte:].lstrip(", ")
        frases.append(parte)
    return [f for f in frases if f]

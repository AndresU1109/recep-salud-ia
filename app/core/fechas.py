"""Fechas legibles en la hora de la institución (las usan el asistente y los avisos a pacientes)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def texto_fecha(dt: datetime) -> str:
    """'martes 3 de noviembre a las 8:40 a. m.' (en la hora de la institución)."""
    l = dt.astimezone(ZoneInfo(get_settings().timezone))
    h12 = l.hour % 12 or 12
    return f"{DIAS[l.weekday()]} {l.day} de {MESES[l.month - 1]} a las {h12}:{l.minute:02d} {'a. m.' if l.hour < 12 else 'p. m.'}"

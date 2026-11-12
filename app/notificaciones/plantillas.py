"""Texto de los avisos. Regla de confidencialidad: un correo o SMS puede leerlo otra persona, así que NO dicen la especialidad, el profesional,
el motivo ni nada clínico: solo cuándo, dónde y el código de reserva. Tampoco prometen nada que la institución no haya configurado."""
from dataclasses import dataclass

from app.models.enums import TipoNotificacion


@dataclass(frozen=True)
class Datos:
    nombre: str  # solo el primer nombre
    institucion: str
    cuando: str = ""  # «martes 3 de noviembre a las 8:40 a. m.»
    sede: str = ""
    codigo: str = ""


def _donde(d: Datos) -> str:
    return f", en {d.sede}" if d.sede else ""


def _codigo(d: Datos) -> str:
    return f" Código de reserva: {d.codigo}." if d.codigo else ""


def _cierre(d: Datos) -> str:
    return f"\n\n{d.institucion}"


def render(tipo: TipoNotificacion, d: Datos) -> tuple[str, str]:
    """(asunto, cuerpo)."""
    saludo = f"Hola, {d.nombre}.\n\n"
    if tipo == TipoNotificacion.CITA_AGENDADA:
        return ("Su cita quedó agendada",
                f"{saludo}Su cita quedó agendada para el {d.cuando}{_donde(d)}.{_codigo(d)}\n\n"
                f"Si no puede asistir, comuníquese con {d.institucion} para cancelarla o reprogramarla." + _cierre(d))
    if tipo == TipoNotificacion.CITA_CANCELADA:
        return ("Su cita fue cancelada",
                f"{saludo}La cita del {d.cuando} fue cancelada.{_codigo(d)}\n\n"
                f"Si desea una nueva cita, comuníquese con {d.institucion}." + _cierre(d))
    if tipo == TipoNotificacion.CITA_REPROGRAMADA:
        return ("Su cita cambió de horario",
                f"{saludo}Su cita ahora es el {d.cuando}{_donde(d)}.{_codigo(d)}\n\n"
                f"Si ese horario no le sirve, comuníquese con {d.institucion}." + _cierre(d))
    if tipo == TipoNotificacion.RECORDATORIO:
        return ("Recordatorio de su cita",
                f"{saludo}Le recordamos su cita del {d.cuando}{_donde(d)}.{_codigo(d)}\n\n"
                f"Si no puede asistir, avísenos con tiempo para que otra persona pueda usar el cupo." + _cierre(d))
    if tipo == TipoNotificacion.CUPO_DISPONIBLE:
        return ("Se liberó un cupo que usted esperaba",
                f"{saludo}Se liberó un cupo que usted esperaba: {d.cuando}{_donde(d)}.\n\n"
                f"Este aviso NO reserva el cupo, porque otras personas también están en espera. Si lo desea, comuníquese con {d.institucion} "
                f"lo antes posible para que verifiquen si sigue disponible." + _cierre(d))
    raise ValueError(f"tipo de aviso sin plantilla: {tipo}")

"""Estado de UNA conversación que las herramientas leen y modifican. El LLM nunca ve ni controla estos campos."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.enums import CanalConversacion


@dataclass
class CupoOfrecido:
    """Un cupo que el asistente le mostró al paciente. Solo se puede agendar lo que se ofreció aquí."""

    profesional_id: int
    especialidad_id: int
    inicio: datetime
    fin: datetime
    profesional: str
    sede: str
    consultorio: str | None
    texto: str  # "martes 3 de noviembre a las 8:40 a. m."


@dataclass
class ContextoHerramientas:
    db: Session
    conversacion_id: uuid.UUID
    canal: CanalConversacion = CanalConversacion.TEXTO
    institucion_id: int | None = None  # la institución en cuyo nombre se habla: todo lo que se consulta se limita a ella (y a lo compartido)
    paciente_id: int | None = None
    identidad_verificada: bool = False
    primer_nombre: str | None = None
    transferida: bool = False
    guarda_transcripcion: bool = False
    politica_datos_version: str = ""  # vacío = el registro por este canal no está habilitado (no se inventa una política)
    cupos: dict[int, CupoOfrecido] = field(default_factory=dict)
    ultimo_cupo: int = 0
    ultima_oferta: list[int] = field(default_factory=list)  # números de cupo de la ÚLTIMA oferta, en orden ("la segunda" = ultima_oferta[1])
    cupo_elegido: int | None = None  # eligió un horario antes de identificarse: se retoma al verificar su identidad

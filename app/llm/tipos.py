"""Tipos comunes del cliente LLM, independientes del proveedor (Ollama u otro compatible con OpenAI)."""
from dataclasses import dataclass, field


@dataclass
class LlamadaHerramienta:
    nombre: str
    argumentos: dict
    id: str | None = None


@dataclass
class Mensaje:
    rol: str  # system | user | assistant | tool
    contenido: str = ""
    llamadas: list[LlamadaHerramienta] = field(default_factory=list)  # solo assistant
    nombre: str | None = None  # solo tool: herramienta que respondió
    id: str | None = None  # solo tool: id de la llamada que responde

    @staticmethod
    def sistema(texto: str) -> "Mensaje":
        return Mensaje("system", texto)

    @staticmethod
    def usuario(texto: str) -> "Mensaje":
        return Mensaje("user", texto)

    @staticmethod
    def asistente(texto: str = "", llamadas: list[LlamadaHerramienta] | None = None) -> "Mensaje":
        return Mensaje("assistant", texto, llamadas or [])

    @staticmethod
    def herramienta(nombre: str, resultado: str, id: str | None = None) -> "Mensaje":
        return Mensaje("tool", resultado, nombre=nombre, id=id)


@dataclass
class Opciones:
    temperatura: float = 0.2
    max_tokens: int = 400
    razonamiento: bool = False
    num_ctx: int = 4096
    esquema_json: dict | None = None  # salida estructurada: el modelo debe responder con JSON que cumpla este esquema


@dataclass
class RespuestaLLM:
    contenido: str
    llamadas: list[LlamadaHerramienta]
    modelo: str
    tokens_entrada: int | None
    tokens_salida: int | None
    duracion_ms: int
    ttft_ms: int | None = None  # solo en streaming: tiempo hasta el primer texto


@dataclass
class Trozo:
    """Un pedazo de la respuesta en streaming. El último trae ``final`` con las métricas y las llamadas a herramientas."""

    texto: str = ""
    final: RespuestaLLM | None = None

"""Registro de herramientas: lo ÚNICO que el LLM puede pedir ejecutar. El backend valida argumentos, identidad y reglas."""
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.errores import ErrorNegocio
from app.tools.contexto import ContextoHerramientas

MAX_RESULTADO = 2500  # caracteres que ve el LLM: lo demás se recorta (ahorra contexto y limita lo que puede "leer")

# Valores que se pueden registrar tal cual en la auditoría; el resto se omite (documentos, nombres, fechas de nacimiento, texto libre…).
_PARAMETROS_SEGUROS = {"especialidad", "desde", "hasta", "franja", "tipo", "codigo_reserva", "cupo", "nuevo_cupo", "profesional", "tipo_documento",
                       "autoriza_tratamiento_datos", "autoriza_transcripcion", "incluir_pasadas"}


@dataclass
class Herramienta:
    nombre: str
    descripcion: str
    modelo: type[BaseModel]
    funcion: Callable[[ContextoHerramientas, BaseModel], dict]
    requiere_identidad: bool = False
    requiere_confirmacion: bool = False  # acciones que modifican algo: el paciente debe confirmarlas expresamente
    resumen: Callable[[ContextoHerramientas, BaseModel], str] | None = None  # texto de la confirmación

    def esquema(self) -> dict:
        return {"type": "function", "function": {"name": self.nombre, "description": self.descripcion, "parameters": esquema_json(self.modelo)}}


@dataclass
class ResultadoHerramienta:
    ok: bool
    datos: dict
    codigo_error: str | None = None
    rechazada: bool = False  # el backend la negó (argumentos, identidad, regla de negocio) vs. fallo inesperado
    duracion_ms: int = 0
    parametros: dict = field(default_factory=dict)  # ya redactados, para la auditoría

    def para_llm(self) -> str:
        texto = json.dumps(self.datos, ensure_ascii=False, default=str)
        if len(texto) <= MAX_RESULTADO:
            return texto
        return json.dumps({"ok": self.ok, "resultado_recortado": texto[:MAX_RESULTADO]}, ensure_ascii=False)


def esquema_json(modelo: type[BaseModel]) -> dict:
    """Esquema JSON compacto para el LLM: sin títulos, con enums resueltos y los Optional como campos opcionales."""
    bruto = modelo.model_json_schema()
    defs = bruto.get("$defs", {})

    def limpiar(n):
        if isinstance(n, list):
            return [limpiar(x) for x in n]
        if not isinstance(n, dict):
            return n
        if "$ref" in n:
            return limpiar(defs[n["$ref"].split("/")[-1]])
        if "anyOf" in n:
            validas = [x for x in n["anyOf"] if x.get("type") != "null"]
            base = limpiar(validas[0]) if validas else {}
            return {**base, **({"description": n["description"]} if "description" in n else {})}
        return {k: limpiar(v) for k, v in n.items() if k not in ("title", "default", "additionalProperties", "$defs")}

    esquema = limpiar(bruto)
    esquema.setdefault("properties", {})
    return esquema


def redactar_parametros(argumentos: dict) -> dict:
    return {k: (v if k in _PARAMETROS_SEGUROS else "[omitido]") for k, v in (argumentos or {}).items()}


def _mensaje_validacion(e: ValidationError) -> str:
    partes = []
    for err in e.errors():
        campo = ".".join(str(x) for x in err["loc"]) or "argumentos"
        partes.append(f"{campo}: {'no se permite este campo' if err['type'] == 'extra_forbidden' else err['msg']}")
    return "; ".join(partes)


class Registro:
    def __init__(self):
        self._h: dict[str, Herramienta] = {}

    def registrar(self, h: Herramienta) -> Herramienta:
        self._h[h.nombre] = h
        return h

    def __contains__(self, nombre: str) -> bool:
        return nombre in self._h

    def obtener(self, nombre: str) -> Herramienta | None:
        return self._h.get(nombre)

    def nombres(self) -> list[str]:
        return list(self._h)

    def esquemas(self) -> list[dict]:
        return [h.esquema() for h in self._h.values()]

    # ---- validación previa (también la usa la puerta de confirmación) ----
    def preparar(self, ctx: ContextoHerramientas, nombre: str, argumentos: dict) -> tuple[Herramienta | None, BaseModel | None, ResultadoHerramienta | None]:
        """Devuelve (herramienta, argumentos validados, None) o (None, None, resultado de rechazo)."""
        h = self._h.get(nombre)
        if h is None:
            return None, None, ResultadoHerramienta(False, {"ok": False, "error": "herramienta_desconocida", "mensaje": f"No existe la herramienta «{nombre}»."},
                                                    "herramienta_desconocida", True, parametros=redactar_parametros(argumentos))
        try:
            args = h.modelo.model_validate(argumentos or {})
        except ValidationError as e:
            return None, None, ResultadoHerramienta(False, {"ok": False, "error": "argumentos_invalidos", "mensaje": _mensaje_validacion(e)}, "argumentos_invalidos", True,
                                                    parametros=redactar_parametros(argumentos))
        if h.requiere_identidad and not ctx.identidad_verificada:
            return None, None, ResultadoHerramienta(False, {"ok": False, "error": "identidad_no_verificada",
                                                            "mensaje": "Primero hay que verificar la identidad del paciente: pídale tipo y número de documento y fecha de nacimiento, y use buscar_paciente."},
                                                    "identidad_no_verificada", True, parametros=redactar_parametros(argumentos))
        return h, args, None

    def ejecutar(self, ctx: ContextoHerramientas, nombre: str, argumentos: dict) -> ResultadoHerramienta:
        t0 = time.perf_counter()
        h, args, rechazo = self.preparar(ctx, nombre, argumentos)
        if rechazo:
            return rechazo
        parametros = redactar_parametros(argumentos)
        try:
            datos = h.funcion(ctx, args)
            res = ResultadoHerramienta(True, {"ok": True, **datos}, parametros=parametros)
        except ErrorNegocio as e:
            ctx.db.rollback()
            res = ResultadoHerramienta(False, {"ok": False, "error": e.codigo, "mensaje": e.mensaje}, e.codigo, True, parametros=parametros)
        except SQLAlchemyError:
            ctx.db.rollback()
            res = ResultadoHerramienta(False, {"ok": False, "error": "error_interno", "mensaje": "Ocurrió un problema técnico al consultar el sistema."}, "error_interno", False, parametros=parametros)
        res.duracion_ms = int((time.perf_counter() - t0) * 1000)
        return res


REGISTRO = Registro()

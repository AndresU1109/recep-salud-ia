"""Errores de negocio con formato uniforme {"error": {"codigo", "mensaje"}} y sin filtrar datos sensibles."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorNegocio(Exception):
    def __init__(self, codigo: str, mensaje: str, status: int = 400):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.status = codigo, mensaje, status


def _respuesta(status: int, codigo: str, mensaje: str, extra: dict | None = None, headers: dict | None = None):
    return JSONResponse({"error": {"codigo": codigo, "mensaje": mensaje, **(extra or {})}}, status_code=status, headers=headers)


_MOTIVOS = {
    "missing": "es obligatorio",
    "string_too_short": "es demasiado corto (mínimo {min_length} caracteres)",
    "string_too_long": "es demasiado largo (máximo {max_length} caracteres)",
    "too_short": "debe tener al menos {min_length} elemento(s)",
    "too_long": "admite como máximo {max_length} elemento(s)",
    "greater_than": "debe ser mayor que {gt}",
    "greater_than_equal": "debe ser mayor o igual a {ge}",
    "less_than": "debe ser menor que {lt}",
    "less_than_equal": "debe ser menor o igual a {le}",
    "int_parsing": "debe ser un número entero",
    "int_type": "debe ser un número entero",
    "bool_parsing": "debe ser verdadero o falso",
    "enum": "no es un valor permitido",
    "string_type": "debe ser texto",
    "extra_forbidden": "no se puede modificar",
    "date_from_datetime_inexact": "debe ser una fecha",
    "date_parsing": "debe ser una fecha válida (AAAA-MM-DD)",
    "date_type": "debe ser una fecha válida (AAAA-MM-DD)",
}


def _motivo(e: dict) -> str:
    if e["type"] == "value_error":  # mensajes propios, ya en español
        return str(e["msg"]).removeprefix("Value error, ")
    plantilla = _MOTIVOS.get(e["type"])
    try:
        return plantilla.format(**(e.get("ctx") or {})) if plantilla else str(e["msg"])
    except (KeyError, IndexError):
        return str(e["msg"])


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(ErrorNegocio)
    async def _negocio(_: Request, exc: ErrorNegocio):
        return _respuesta(exc.status, exc.codigo, exc.mensaje)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _respuesta(exc.status_code, f"http_{exc.status_code}", str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError):
        # Solo campo y motivo: el detalle por defecto de FastAPI devuelve el valor enviado (p. ej. una contraseña).
        campos = [{"campo": ".".join(str(p) for p in e["loc"] if p != "body"), "motivo": _motivo(e)} for e in exc.errors()]
        return _respuesta(422, "datos_invalidos", "Hay datos inválidos en la solicitud.", {"campos": campos})

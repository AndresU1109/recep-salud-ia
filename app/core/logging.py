"""Logging que oculta datos personales (correos, teléfonos, documentos) antes de escribirlos."""
import logging
import re

_PATRONES = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[correo]"),
    (re.compile(r"(?<![\w])(?:\+?57[\s-]?)?3\d{2}[\s-]?\d{3}[\s-]?\d{4}(?![\w])"), "[telefono]"),
    (re.compile(r"(?<![\w])\d{1,3}(?:[.\s]\d{3}){2,}(?![\w])"), "[documento]"),
    (re.compile(r"(?<![\w])\d{7,12}(?![\w])"), "[documento]"),
]


def redactar(texto: str) -> str:
    for patron, reemplazo in _PATRONES:
        texto = patron.sub(reemplazo, texto)
    return texto


class FiltroDatosPersonales(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redactar(record.getMessage())
        record.args = ()
        return True


def configurar_logging(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    handler.addFilter(FiltroDatosPersonales())
    raiz = logging.getLogger()
    raiz.handlers = [handler]
    raiz.setLevel(nivel.upper())

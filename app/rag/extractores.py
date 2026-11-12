"""Extracción de texto de PDF, DOCX, TXT y MD. Valida el contenido real (no solo la extensión) y pone límites."""
import io
import zipfile
from pathlib import PurePath

from app.core.errores import ErrorNegocio
from app.rag.limpieza import limpiar, quitar_encabezados_y_pies

EXTENSIONES = {".pdf", ".docx", ".txt", ".md"}
MAX_PAGINAS_PDF = 500
MAX_DESCOMPRIMIDO = 100 * 1024 * 1024  # defensa contra "zip bombs" en .docx
MAX_CARACTERES = 1_500_000


def _error(codigo: str, mensaje: str) -> ErrorNegocio:
    return ErrorNegocio(codigo, mensaje, 422)


def _pdf(datos: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if not datos.startswith(b"%PDF"):
        raise _error("archivo_invalido", "El archivo no es un PDF válido.")
    try:
        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted and not lector.decrypt(""):
            raise _error("pdf_protegido", "El PDF está protegido con contraseña. Quita la protección y vuelve a subirlo.")
        if len(lector.pages) > MAX_PAGINAS_PDF:
            raise _error("documento_muy_largo", f"El PDF tiene más de {MAX_PAGINAS_PDF} páginas. Divídelo en partes.")
        paginas = [(p.extract_text() or "") for p in lector.pages]
    except ErrorNegocio:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, RecursionError) as exc:
        raise _error("archivo_invalido", "No se pudo leer el PDF (¿está dañado?).") from exc
    return "\n\n".join(quitar_encabezados_y_pies(paginas))


def _docx(datos: bytes) -> str:
    from docx import Document

    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_DESCOMPRIMIDO:
                raise _error("archivo_invalido", "El documento descomprimido es demasiado grande.")
        doc = Document(io.BytesIO(datos))
    except ErrorNegocio:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise _error("archivo_invalido", "El archivo no es un documento Word (.docx) válido.") from exc
    partes = [p.text for p in doc.paragraphs]
    for tabla in doc.tables:
        for fila in tabla.rows:
            celdas = [c.text.strip() for c in fila.cells if c.text.strip()]
            if celdas:
                partes.append(" | ".join(dict.fromkeys(celdas)))
    return "\n\n".join(partes)


def _texto(datos: bytes) -> str:
    if b"\x00" in datos[:4096]:
        raise _error("archivo_invalido", "El archivo parece binario, no texto.")
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise _error("archivo_invalido", "No se pudo leer la codificación del texto.")


def extraer_texto(nombre: str, datos: bytes) -> str:
    ext = PurePath(nombre).suffix.lower()
    if ext not in EXTENSIONES:
        raise _error("tipo_no_soportado", f"Tipo de archivo no soportado. Usa: {', '.join(sorted(EXTENSIONES))}.")
    if not datos:
        raise _error("archivo_vacio", "El archivo está vacío.")
    bruto = {".pdf": _pdf, ".docx": _docx}.get(ext, _texto)(datos)
    texto = limpiar(bruto)
    if len(texto) < 20:
        raise _error("sin_texto", "No se encontró texto en el archivo. Si es un PDF escaneado (imágenes) hace falta OCR, que este sistema no incluye.")
    if len(texto) > MAX_CARACTERES:
        raise _error("documento_muy_largo", "El texto del documento es demasiado largo. Divídelo en partes.")
    return texto

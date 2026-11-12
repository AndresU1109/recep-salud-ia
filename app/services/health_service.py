"""Comprobaciones de salud de las dependencias. Nunca devuelve credenciales ni cadenas de conexión."""
import httpx
from sqlalchemy import text

from app.core.config import Settings
from app.database.session import get_engine


_SQL_DIMENSION = text(
    "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
    "JOIN pg_class c ON c.oid = a.attrelid "
    "WHERE c.relname = 'documento_fragmentos' AND a.attname = 'embedding' AND NOT a.attisdropped"
)


def dimension_columna_vectores(conn) -> int | None:
    """Dimensión declarada de documento_fragmentos.embedding (p. ej. 768), o None si no existe."""
    tipo = conn.execute(_SQL_DIMENSION).scalar()  # 'vector(768)'
    if tipo and "(" in tipo:
        return int(tipo.split("(")[1].rstrip(")"))
    return None


def revisar_base_de_datos(settings: Settings) -> dict:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            pgvector = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
            if pgvector is None:
                return {"estado": "parcial", "detalle": "PostgreSQL responde pero la extensión pgvector no está instalada (aplicar migraciones: alembic upgrade head)."}
            dim = dimension_columna_vectores(conn)
        if dim is None:
            return {"estado": "parcial", "pgvector": pgvector, "detalle": "Faltan las tablas del RAG (alembic upgrade head)."}
        if dim != settings.embedding_dimensions:
            return {"estado": "error", "pgvector": pgvector,
                    "detalle": f"La columna de vectores es de {dim} dimensiones pero EMBEDDING_DIMENSIONS={settings.embedding_dimensions}. Migrar la columna y reindexar."}
        return {"estado": "ok", "pgvector": pgvector, "dimension_vectores": dim}
    except Exception as exc:  # noqa: BLE001 - se reporta solo el tipo de error, sin cadena de conexión
        return {"estado": "error", "detalle": f"No se pudo conectar a PostgreSQL ({type(exc).__name__})."}


def revisar_ollama(settings: Settings) -> dict:
    try:
        r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3)
        r.raise_for_status()
        instalados = {m["name"] for m in r.json().get("models", [])}
    except Exception as exc:  # noqa: BLE001
        return {"estado": "error", "detalle": f"Ollama no responde ({type(exc).__name__})."}

    faltan = [m for m in (settings.llm_model, settings.embedding_model) if m not in instalados]
    if faltan:
        return {"estado": "parcial", "detalle": f"Ollama responde pero faltan modelos: {', '.join(faltan)}"}
    try:
        capacidades = httpx.post(f"{settings.ollama_base_url}/api/show", json={"model": settings.llm_model}, timeout=5).json().get("capabilities", [])
    except (httpx.HTTPError, ValueError):
        capacidades = None
    if capacidades is not None and "tools" not in capacidades:
        return {"estado": "parcial", "detalle": f"El modelo {settings.llm_model} no soporta herramientas (tool calling), que el asistente necesita."}
    return {"estado": "ok", "llm": settings.llm_model, "embeddings": settings.embedding_model, "herramientas": capacidades is not None}


def revisar_todo(settings: Settings) -> dict:
    partes = {"base_de_datos": revisar_base_de_datos(settings), "ollama": revisar_ollama(settings)}
    general = "ok" if all(p["estado"] == "ok" for p in partes.values()) else "degradado"
    return {"estado": general, **partes}

"""Proveedores de IA en la nube que hablan el protocolo «OpenAI compatible». En el panel solo se elige uno y se pega la clave de API:
la dirección y un modelo sugerido vienen de aquí. Los modelos reales se listan con la clave al probar la conexión."""
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.errores import ErrorNegocio


@dataclass(frozen=True)
class Proveedor:
    id: str
    nombre: str
    base: str  # vacío = la escribe la persona («otro»)
    modelo_sugerido: str
    nota: str = ""


PROVEEDORES: dict[str, Proveedor] = {p.id: p for p in (
    Proveedor("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    Proveedor("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    Proveedor("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1", "claude-haiku-5-5",
              "Usa la capa de compatibilidad de Anthropic: ignora el formato JSON estricto; el asistente funciona, pero es la menos probada."),
    Proveedor("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    Proveedor("groq", "Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    Proveedor("otro", "Otro (compatible con OpenAI)", "", "", "Escribe la dirección base, p. ej. https://mi-proveedor.com/v1"),
)}

# Modelos de la lista del proveedor que no sirven para conversar.
_NO_CHAT = ("embed", "whisper", "tts", "dall-e", "moderation", "image", "audio", "transcribe", "realtime", "rerank", "guard", "imagen", "veo", "aqa")


def obtener(id_: str) -> Proveedor:
    if id_ not in PROVEEDORES:
        raise ErrorNegocio("proveedor_desconocido", f"«{id_}» no es un proveedor de IA conocido.", 422)
    return PROVEEDORES[id_]


def base_efectiva(proveedor: Proveedor, base_propia: str | None) -> str:
    """La dirección a usar. Siempre HTTPS (la clave viaja en cada petición); HTTP solo hacia este mismo equipo.
    Se rechazan direcciones internas para que el panel no sirva de puente hacia la red de la institución."""
    base = (base_propia or "").strip() if proveedor.id == "otro" else proveedor.base
    if not base:
        raise ErrorNegocio("base_requerida", "Escribe la dirección base del proveedor.", 422)
    u = urlparse(base)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise ErrorNegocio("base_insegura", "La dirección debe empezar por https:// (la clave de API viaja en cada petición).", 422)
    if not u.hostname or u.username or u.password:
        raise ErrorNegocio("base_invalida", "La dirección del proveedor no es válida.", 422)
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_loopback and (ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise ErrorNegocio("base_interna", "No se permiten direcciones de la red interna como proveedor de IA.", 422)
    return base.rstrip("/")


def solo_chat(ids: list[str]) -> list[str]:
    """Quita de la lista de modelos los de embeddings, audio, imágenes… y el prefijo «models/» que antepone Gemini."""
    limpios = sorted({i.removeprefix("models/") for i in ids if i})
    return [i for i in limpios if not any(m in i.lower() for m in _NO_CHAT)]

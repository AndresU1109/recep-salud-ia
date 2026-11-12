import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errores import registrar_manejadores
from app.core.logging import configurar_logging
from app.notificaciones import trabajador as notificaciones

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Sin scripts ni estilos en línea: todo sale de nuestro propio origen. Swagger (/docs) usa CDN y queda fuera.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SIN_CSP = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _precargar_stt(transcriptor) -> None:
    try:
        transcriptor.obtener_transcriptor().modelo()
    except Exception:  # noqa: BLE001 - sin el modelo de voz el panel sigue funcionando; el error se verá al transcribir
        logging.getLogger("stt").warning("no se pudo precargar el modelo de voz a texto")


def _calentar_ollama() -> None:
    """Una consulta mínima al modelo de lenguaje y a los embeddings para que ya estén en memoria cuando llegue el primer paciente
    (cargarlos en frío tarda 10-60 s: por voz sería un silencio largo)."""
    try:
        from app.embeddings import cliente as embeddings
        from app.llm import cliente as llm
        from app.llm.tipos import Mensaje, Opciones

        if get_settings().llm_provider == "ollama":  # con un proveedor en la nube no hay nada que calentar (y cada llamada cuesta)
            c = llm.obtener_cliente()
            try:
                c.chat([Mensaje.usuario("Responde solo: ok")], None, Opciones(max_tokens=2))
            finally:
                c.cerrar()
        embeddings.obtener_embedder().embed_consulta("hola")
    except Exception:  # noqa: BLE001
        logging.getLogger("agente").warning("no se pudo calentar Ollama (¿apagado?)")


def _aplicar_ajustes_guardados() -> None:
    """Proveedor de IA y modelo de embeddings elegidos en el panel (si los hay) pasan a los ajustes del proceso."""
    try:
        from app.database.session import get_session_factory
        from app.services import config_ia_service

        with get_session_factory()() as db:
            config_ia_service.aplicar_a_ajustes(db)
    except Exception:  # noqa: BLE001 - sin base de datos el arranque sigue y /health lo dirá
        logging.getLogger("agente").warning("no se pudo leer la configuración de IA guardada; se usa la del servidor")


@asynccontextmanager
async def _ciclo_de_vida(app: FastAPI):
    # Los modelos de voz tardan segundos en la primera vez: se calientan en segundo plano para que el primer paciente no espere.
    from app.tts import servicio as tts

    _aplicar_ajustes_guardados()
    from app.services import config_ia_service

    threading.Thread(target=config_ia_service.modelos_instalados, daemon=True, name="modelos-ollama").start()  # la pantalla de IA los pide: ya listos
    if get_settings().precargar_modelos_de_voz:
        from app.stt import transcriptor

        threading.Thread(target=tts.obtener_tts().precargar, daemon=True, name="precarga-tts").start()
        threading.Thread(target=_precargar_stt, args=(transcriptor,), daemon=True, name="precarga-stt").start()
        threading.Thread(target=_calentar_ollama, daemon=True, name="calentar-ollama").start()
    trabajo = asyncio.create_task(notificaciones.bucle()) if get_settings().notificaciones_activas else None
    yield
    if trabajo:
        trabajo.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    configurar_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Recepción médica con agentes conversacionales (texto y voz).",
        lifespan=_ciclo_de_vida,
        # En producción no se publica la documentación interactiva ni el esquema (muestran toda la superficie de la API y usan un CDN).
        docs_url=None if settings.app_env == "production" else "/docs", redoc_url=None if settings.app_env == "production" else "/redoc",
        openapi_url=None if settings.app_env == "production" else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    registrar_manejadores(app)

    @app.middleware("http")
    async def cabeceras_de_seguridad(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        if request.url.path not in SIN_CSP:
            response.headers.setdefault("Content-Security-Policy", CSP)
        ruta = request.url.path
        if ruta.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # respuestas con datos de pacientes: nunca en cachés del navegador ni de intermediarios
        elif ruta.startswith("/vendor/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")  # Bootstrap: archivos con versión fija
        elif response.status_code == 200:
            response.headers.setdefault("Cache-Control", "no-cache")  # el panel: se revalida siempre (ETag), así un despliegue nuevo se ve al instante
        return response

    app.include_router(api_router, prefix="/api/v1")
    if FRONTEND_DIR.exists():
        # Se monta al final para que /api y /docs tengan prioridad.
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()

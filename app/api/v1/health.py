from fastapi import APIRouter

from app.core.config import get_settings
from app.services import health_service

router = APIRouter(prefix="/health", tags=["Salud del sistema"])


@router.get("", summary="¿La API está viva?")
def health() -> dict:
    s = get_settings()
    return {"estado": "ok", "app": s.app_name, "version": s.app_version, "entorno": s.app_env}


@router.get("/dependencias", summary="Estado de PostgreSQL/pgvector y Ollama")
def dependencias() -> dict:
    return health_service.revisar_todo(get_settings())

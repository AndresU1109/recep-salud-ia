from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models import Usuario
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import config_ia_service as svc
from app.tts import servicio as tts_servicio

router = APIRouter(prefix="/config-ia", tags=["Configuración de IA"])
gestion = Depends(requiere(Permiso.CONFIG_IA_GESTIONAR))


class ConfigIAIn(BaseModel):
    """Cada clave: valor para personalizar, null para volver al valor por defecto. Claves desconocidas → 422."""

    model_config = ConfigDict(extra="forbid")

    # Tipos estrictos: sin ellos Pydantic convertiría true → 1.0 o "si" → True y se saltaría las validaciones.
    llm_modelo: StrictStr | None = None
    llm_temperatura: StrictFloat | None = None
    llm_max_tokens: StrictInt | None = None
    llm_razonamiento: StrictBool | None = None
    nombre_asistente: StrictStr | None = None
    mensaje_bienvenida: StrictStr | None = None
    politica_datos_version: StrictStr | None = None
    encuesta_activa: StrictBool | None = None
    frase_activacion: StrictStr | None = None


def _estado(db: Session) -> dict:
    s = get_settings()
    cfg = svc.efectiva(db)
    modelos = svc.modelos_instalados()
    return {"efectiva": {k: getattr(cfg, k) for k in svc.CLAVES}, "por_defecto": svc.por_defecto(), "personalizadas": cfg.personalizadas,
            "proveedor": s.llm_provider, "externo_habilitado": s.llm_allow_external, "modelos": modelos,
            "voz": {"elegida": s.tts_voz, "velocidad": s.tts_velocidad, "voces": tts_servicio.obtener_tts().voces()},
            "nube": svc.estado_nube(db), "embedding": {"modelo": s.embedding_model, "dimensiones": s.embedding_dimensions, "instalados": svc.embeddings_instalados(modelos),
                                                       "por_defecto": svc._origen().embedding_model}}


@router.get("", summary="Configuración efectiva de la IA, valores por defecto y modelos instalados")
def ver(_: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return _estado(db)


@router.put("", summary="Cambiar ajustes (null = restablecer)")
def cambiar(datos: ConfigIAIn, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    svc.actualizar(db, usuario, datos.model_dump(exclude_unset=True))
    return _estado(db)


@router.post("/probar", summary="Probar la conexión y medir la velocidad del modelo")
def probar(_: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return svc.probar(db)


# ---------- IA en la nube: se elige proveedor, se pega la clave de API y se prueba ----------
class NubeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proveedor: StrictStr
    api_key: StrictStr | None = Field(default=None, max_length=400, description="Vacía = usar la clave ya guardada de ese proveedor")
    modelo: StrictStr | None = Field(default=None, max_length=120)
    direccion: StrictStr | None = Field(default=None, max_length=200, description="Solo para el proveedor «otro»")


class NubeGuardarIn(NubeIn):
    autoriza: StrictBool = False


@router.post("/nube/probar", summary="Probar la clave de API de un proveedor en la nube (sin guardarla)")
def probar_nube(datos: NubeIn, _: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    return svc.probar_nube(db, datos.proveedor, datos.api_key, datos.modelo, datos.direccion)


@router.put("/nube", summary="Guardar la clave (cifrada) y usar ese proveedor; exige confirmar que las conversaciones salen de la institución")
def guardar_nube(datos: NubeGuardarIn, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    svc.guardar_nube(db, usuario, datos.proveedor, api_key=datos.api_key, modelo=datos.modelo, base_propia=datos.direccion, autoriza=datos.autoriza)
    return _estado(db)


@router.post("/nube/usar-local", summary="Volver al modelo local de Ollama")
def usar_local(usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    svc.usar_ollama(db, usuario)
    return _estado(db)


@router.delete("/nube/{proveedor}/clave", summary="Borrar la clave guardada de un proveedor")
def borrar_clave(proveedor: str, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    svc.borrar_clave_nube(db, usuario, proveedor)
    return _estado(db)


# ---------- modelo de embeddings ----------
class EmbeddingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modelo: StrictStr = Field(min_length=1, max_length=100)


@router.post("/embeddings/probar", summary="Probar un modelo de embeddings (dimensiones y velocidad)")
def probar_embedding(datos: EmbeddingIn, _: Usuario = gestion) -> dict:
    return svc.probar_embedding(datos.modelo)


@router.put("/embeddings", summary="Usar otro modelo de embeddings Gemma y reindexar el conocimiento")
def guardar_embedding(datos: EmbeddingIn, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    r = svc.guardar_embedding(db, usuario, datos.modelo)
    return {**_estado(db), "reindexando": r["reindexando"], "prueba": r["prueba"]}


# ---------- voz del asistente ----------
class VozIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voz: StrictStr = Field(max_length=40, description="«» automática, «piper», «kokoro:ef_dora» o «sapi»")
    velocidad: float | None = Field(default=None, ge=0.6, le=1.5)


@router.put("/voz", summary="Elegir la voz del asistente y su velocidad")
def guardar_voz(datos: VozIn, usuario: Usuario = gestion, db: Session = Depends(get_db)) -> dict:
    svc.guardar_voz(db, usuario, datos.voz, datos.velocidad)
    return _estado(db)

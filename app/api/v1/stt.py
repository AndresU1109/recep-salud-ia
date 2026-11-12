"""Voz a texto. El audio se procesa en memoria y NO se guarda; solo se audita cuánto duró y si hubo voz (nunca lo dicho)."""
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import Usuario
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import auditoria
from app.stt import transcriptor

router = APIRouter(prefix="/stt", tags=["Voz a texto"])
probar = Depends(requiere(Permiso.AGENTE_PROBAR))


@router.get("/estado", summary="Estado del modelo de voz")
def estado(_: Usuario = probar) -> dict:
    return transcriptor.obtener_transcriptor().estado()


@router.post("/transcribir", summary="Transcribir una intervención de audio (máx. 30 s)")
async def transcribir(archivo: UploadFile = File(...), formato: Annotated[Literal["auto", "pcm16"], Form()] = "auto", usuario: Usuario = probar,
                      db: Session = Depends(get_db)) -> dict:
    limite = get_settings().stt_max_bytes
    datos = await archivo.read(limite + 1)  # nunca se lee más de lo permitido
    if len(datos) > limite:
        raise ErrorNegocio("audio_muy_grande", f"El audio supera el máximo de {limite // (1024 * 1024)} MB.", 413)
    r = await run_in_threadpool(transcriptor.obtener_transcriptor().transcribir, datos, formato)  # pesa CPU: no debe bloquear el servidor
    auditoria.registrar(db, accion="stt_transcribir", usuario_id=usuario.id, detalle={"audio_s": r.duracion_audio_s, "ms": r.duracion_proceso_ms, "silencio": r.silencio})
    db.commit()
    return asdict(r)

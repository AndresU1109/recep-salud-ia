"""Texto a voz. Se audita solo cuánto se leyó y con qué motor; nunca el texto."""
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import auditoria
from app.tts import servicio

router = APIRouter(prefix="/tts", tags=["Texto a voz"])
probar = Depends(requiere(Permiso.AGENTE_PROBAR))


class SintetizarIn(BaseModel):
    texto: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    velocidad: float | None = Field(default=None, ge=0.6, le=1.5, description="1.0 normal; 0.8 más lento")
    voz: str | None = Field(default=None, max_length=40, description="Probar una voz sin cambiar la elegida («piper», «kokoro:ef_dora», «sapi»)")


@router.get("/estado", summary="Motores de voz disponibles")
def estado(_: Usuario = probar) -> dict:
    return servicio.obtener_tts().estado()


@router.post("/sintetizar", summary="Texto → audio WAV (máx. 600 caracteres)", response_class=Response, responses={200: {"content": {"audio/wav": {}}}})
def sintetizar(datos: SintetizarIn, usuario: Usuario = probar, db: Session = Depends(get_db)) -> Response:
    audio = servicio.obtener_tts().sintetizar(datos.texto, datos.velocidad, datos.voz)
    auditoria.registrar(db, accion="tts_sintetizar", usuario_id=usuario.id, detalle={"motor": audio.motor, "audio_s": round(audio.duracion_s, 2), "caracteres": len(datos.texto)})
    db.commit()
    return Response(audio.wav, media_type="audio/wav", headers={"X-Motor": audio.motor, "X-Duracion-S": f"{audio.duracion_s:.2f}", "Cache-Control": "no-store"})

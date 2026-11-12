"""Conversar con el asistente en modo PRUEBA (personal autorizado). Las conversaciones de pacientes usan el canal de la Fase 14."""
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from app.agents.recepcionista import Respuesta
from app.database.session import get_db
from app.models import Usuario
from app.models.enums import CanalConversacion
from app.security.dependencias import requiere
from app.security.permisos import Permiso, tiene_permiso
from app.services import agente_service, auditoria, conversacion_service

router = APIRouter(prefix="/agente", tags=["Asistente conversacional"])
probar = Depends(requiere(Permiso.AGENTE_PROBAR))


class IniciarIn(BaseModel):
    canal: CanalConversacion = CanalConversacion.TEXTO
    institucion_id: int | None = None  # en nombre de qué institución habla el asistente (por defecto, la primera)


class MensajeIn(BaseModel):
    texto: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class RespuestaOut(BaseModel):
    conversacion_id: uuid.UUID
    texto: str
    intencion: str
    fuentes: list[dict]
    herramientas: list[str]
    transferida: bool
    emergencia: bool
    requiere_confirmacion: bool
    terminada: bool = False  # el paciente dijo que no necesita más ayuda: la conversación ya se cerró
    institucion: dict | None = None  # {id, nombre, tipo} si en este turno quedó elegida la institución


class MensajeOut(BaseModel):
    rol: str
    contenido: str | None
    intencion: str | None
    creado_en: datetime


def _out(conv_id: uuid.UUID, r: Respuesta) -> RespuestaOut:
    return RespuestaOut(conversacion_id=conv_id, texto=r.texto, intencion=r.intencion, fuentes=r.fuentes, herramientas=r.herramientas, transferida=r.transferida,
                        emergencia=r.emergencia, requiere_confirmacion=r.requiere_confirmacion, terminada=r.terminar, institucion=r.institucion)


@router.post("/conversaciones", response_model=RespuestaOut, status_code=status.HTTP_201_CREATED, summary="Iniciar una conversación de prueba (devuelve el saludo)")
def iniciar(datos: IniciarIn, usuario: Usuario = probar, db: Session = Depends(get_db)):
    conv, r = agente_service.iniciar(db, datos.canal, es_prueba=True, institucion_id=datos.institucion_id)
    auditoria.registrar(db, accion="agente_prueba_iniciada", usuario_id=usuario.id, entidad="conversacion", entidad_id=str(conv.id))
    db.commit()
    return _out(conv.id, r)


@router.post("/conversaciones/{conv_id}/mensajes", response_model=RespuestaOut, summary="Enviar un mensaje del paciente (de prueba)")
def enviar(conv_id: uuid.UUID, datos: MensajeIn, _: Usuario = probar, db: Session = Depends(get_db)):
    conv, r = agente_service.enviar(db, conv_id, datos.texto)
    if r.terminar:
        agente_service.finalizar(db, conv_id)
    return _out(conv.id, r)


@router.post("/conversaciones/{conv_id}/finalizar", status_code=status.HTTP_204_NO_CONTENT)
def finalizar(conv_id: uuid.UUID, _: Usuario = probar, db: Session = Depends(get_db)):
    agente_service.finalizar(db, conv_id)


@router.get("/conversaciones/{conv_id}", response_model=list[MensajeOut], summary="Transcripción (solo con permiso de transcripciones)")
def transcripcion(conv_id: uuid.UUID, usuario: Usuario = probar, db: Session = Depends(get_db)):
    ve_contenido = tiene_permiso(usuario.rol, Permiso.TRANSCRIPCIONES_VER)
    return [MensajeOut(rol=m.rol.value, contenido=m.contenido if ve_contenido else None, intencion=m.intencion, creado_en=m.creado_en)
            for m in conversacion_service.mensajes(db, conv_id)]

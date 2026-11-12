"""Ciclo de vida de una conversación con el asistente: iniciar, enviar mensajes, finalizar."""
import uuid

from sqlalchemy.orm import Session

from app.agents.recepcionista import Recepcionista, Respuesta
from app.agents.sesiones import SESIONES
from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import Conversacion
from app.models.enums import CanalConversacion, EstadoConversacion
from app.services import config_ia_service, conversacion_service, institucion_service


def iniciar(db: Session, canal: CanalConversacion = CanalConversacion.TEXTO, *, es_prueba: bool = False,
            institucion_id: int | None = None) -> tuple[Conversacion, Respuesta]:
    """``institucion_id``: en nombre de qué institución habla el asistente. Sin indicarla y con VARIAS instituciones activas, el asistente se la pregunta
    al paciente al empezar (se elige conversando); con una sola, es esa; si no hay ninguna, el nombre configurado."""
    config = config_ia_service.efectiva(db)
    elegir = institucion_id is None and len(institucion_service.listar(db)) >= 2
    inst = None if elegir else (institucion_service.obtener(db, institucion_id) if institucion_id is not None else institucion_service.por_defecto(db))
    conv = conversacion_service.crear(db, canal, es_prueba=es_prueba, institucion_id=inst.id if inst else None)
    agente = Recepcionista(conv.id, canal, config, inst.nombre if inst else ("" if elegir else get_settings().institution_name), inst.id if inst else None,
                           inst.mensaje_bienvenida if inst else None, elegir_institucion=elegir)
    SESIONES.agregar(conv.id, agente)
    return conv, agente.saludo(db, conv)


def _sesion(db: Session, conv_id: uuid.UUID) -> tuple[Conversacion, Recepcionista]:
    conv = conversacion_service.obtener(db, conv_id)
    if conv is None:
        raise ErrorNegocio("conversacion_no_encontrada", "La conversación no existe.", 404)
    if conv.estado in (EstadoConversacion.RESUELTA, EstadoConversacion.ABANDONADA):
        raise ErrorNegocio("conversacion_cerrada", "La conversación ya terminó. Inicia una nueva.", 409)
    agente = SESIONES.obtener(conv_id)
    if agente is None:
        raise ErrorNegocio("sesion_expirada", "La sesión de esta conversación caducó. Inicia una nueva.", 409)
    return conv, agente


def enviar(db: Session, conv_id: uuid.UUID, texto: str) -> tuple[Conversacion, Respuesta]:
    conv, agente = _sesion(db, conv_id)
    return conv, agente.responder(db, conv, texto)


def finalizar(db: Session, conv_id: uuid.UUID, estado: EstadoConversacion = EstadoConversacion.RESUELTA) -> Conversacion:
    conv = conversacion_service.obtener(db, conv_id)
    if conv is None:
        raise ErrorNegocio("conversacion_no_encontrada", "La conversación no existe.", 404)
    if conv.estado == EstadoConversacion.ACTIVA:
        conversacion_service.finalizar(db, conv, estado)
    SESIONES.quitar(conv_id)
    return conv

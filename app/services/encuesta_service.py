"""Encuesta de satisfacción al final de la conversación: una calificación de 1 a 5 y, si el paciente quiere, un comentario corto.
No se liga al paciente; el comentario es opcional y se guarda recortado."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Conversacion, EncuestaSatisfaccion

MAX_COMENTARIO = 300


def registrar(db: Session, conv: Conversacion, puntaje: int) -> EncuestaSatisfaccion:
    if not 1 <= puntaje <= 5:
        raise ErrorNegocio("puntaje_invalido", "La calificación debe estar entre 1 y 5.", 422)
    e = db.scalar(select(EncuestaSatisfaccion).where(EncuestaSatisfaccion.conversacion_id == conv.id))
    if e is None:
        e = EncuestaSatisfaccion(conversacion_id=conv.id, institucion_id=conv.institucion_id, canal=conv.canal, puntaje=puntaje)
        db.add(e)
    else:
        e.puntaje = puntaje
    db.commit()
    return e


def comentar(db: Session, conv: Conversacion, comentario: str) -> None:
    e = db.scalar(select(EncuestaSatisfaccion).where(EncuestaSatisfaccion.conversacion_id == conv.id))
    texto = " ".join((comentario or "").split())[:MAX_COMENTARIO]
    if e is not None and texto:
        e.comentario = texto
        db.commit()

"""Instituciones: cada una con su catálogo, sus políticas y su conocimiento. El asistente de voz habla en nombre de una.

Regla de alcance: un registro sin institución (institucion_id vacío) es COMPARTIDO y lo ven todas; uno con institución solo lo ve ella."""
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import Institucion


def listar(db: Session, solo_activas: bool = True) -> list[Institucion]:
    q = select(Institucion).order_by(Institucion.nombre)
    if solo_activas:
        q = q.where(Institucion.activa)
    return list(db.scalars(q))


def obtener(db: Session, institucion_id: int) -> Institucion:
    i = db.get(Institucion, institucion_id)
    if i is None or not i.activa:
        raise ErrorNegocio("institucion_no_encontrada", "La institución no existe o no está activa.", 404)
    return i


def por_defecto(db: Session) -> Institucion | None:
    """La primera institución activa (la del sistema antes de que hubiera varias)."""
    return db.scalar(select(Institucion).where(Institucion.activa).order_by(Institucion.id).limit(1))


def alcance(columna, institucion_id: int | None):
    """Condición SQL: lo de esta institución y lo compartido. Sin institución indicada no se filtra nada."""
    return or_(columna == institucion_id, columna.is_(None)) if institucion_id is not None else true()


def nombre_para_mostrar(db: Session, institucion_id: int | None) -> str:
    if institucion_id is not None:
        i = db.get(Institucion, institucion_id)
        if i is not None:
            return i.nombre
    return get_settings().institution_name

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Usuario


def por_id(db: Session, usuario_id: int) -> Usuario | None:
    return db.get(Usuario, usuario_id)


def por_email(db: Session, email: str) -> Usuario | None:
    return db.scalar(select(Usuario).where(func.lower(Usuario.email) == email.strip().lower()))


def listar(db: Session) -> list[Usuario]:
    return list(db.scalars(select(Usuario).order_by(Usuario.nombre)))


def contar_administradores_activos(db: Session) -> int:
    from app.models.enums import Rol

    return db.scalar(select(func.count()).select_from(Usuario).where(Usuario.rol == Rol.ADMINISTRADOR, Usuario.activo.is_(True))) or 0

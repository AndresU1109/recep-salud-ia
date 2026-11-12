"""Escritura en auditoria_log. El detalle nunca debe incluir contraseñas, documentos ni datos de contacto."""
from sqlalchemy.orm import Session

from app.core.logging import redactar
from app.models import AuditoriaLog


def registrar(db: Session, *, accion: str, actor: str = "usuario", usuario_id: int | None = None,
              entidad: str | None = None, entidad_id: int | str | None = None, exito: bool = True,
              detalle: dict | None = None, ip: str | None = None) -> None:
    # Red de seguridad: si alguien pone un dato personal en el detalle, se oculta antes de guardarlo.
    limpio = {k: redactar(v) if isinstance(v, str) else v for k, v in (detalle or {}).items()} or None
    db.add(AuditoriaLog(actor=actor, usuario_id=usuario_id, accion=accion, entidad=entidad,
                        entidad_id=None if entidad_id is None else str(entidad_id), exito=exito, detalle=limpio, ip=ip))
    db.flush()

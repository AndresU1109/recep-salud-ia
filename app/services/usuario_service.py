"""Gestión de usuarios (solo ADMINISTRADOR). Reglas: no quedarse sin administradores ni auto-degradarse."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Usuario
from app.models.enums import Rol
from app.repositories import usuario_repository as repo
from app.security import passwords
from app.services import auditoria


def _validar_clave(clave: str) -> None:
    if (problemas := passwords.problemas_de_fortaleza(clave)):
        raise ErrorNegocio("clave_debil", " ".join(problemas), 422)


def crear(db: Session, actor: Usuario | None, *, email: str, nombre: str, rol: Rol, clave: str) -> Usuario:
    """actor=None solo para el primer administrador (scripts/crear_admin.py)."""
    if repo.por_email(db, email):
        raise ErrorNegocio("correo_en_uso", "Ya existe un usuario con ese correo.", 409)
    _validar_clave(clave)
    usuario = Usuario(email=email.strip().lower(), nombre=nombre.strip(), rol=rol,
                      password_hash=passwords.hash_password(clave), debe_cambiar_clave=True)
    db.add(usuario)
    db.flush()
    auditoria.registrar(db, accion="usuario_creado", actor="usuario" if actor else "sistema",
                        usuario_id=actor.id if actor else None, entidad="usuario", entidad_id=usuario.id,
                        detalle={"rol": rol.value})
    db.commit()
    return usuario


def obtener(db: Session, usuario_id: int) -> Usuario:
    usuario = repo.por_id(db, usuario_id)
    if not usuario:
        raise ErrorNegocio("usuario_no_encontrado", "El usuario no existe.", 404)
    return usuario


def actualizar(db: Session, actor: Usuario, usuario_id: int, *, nombre: str | None, rol: Rol | None, activo: bool | None) -> Usuario:
    usuario = obtener(db, usuario_id)
    deja_de_ser_admin_activo = usuario.rol == Rol.ADMINISTRADOR and usuario.activo and (
        (rol is not None and rol != Rol.ADMINISTRADOR) or activo is False
    )
    if deja_de_ser_admin_activo:
        if usuario.id == actor.id:
            raise ErrorNegocio("auto_degradacion", "No puedes quitarte tu propio acceso de administrador.", 409)
        if repo.contar_administradores_activos(db) <= 1:
            raise ErrorNegocio("ultimo_administrador", "Debe quedar al menos un administrador activo.", 409)
    cambios = {}
    if nombre is not None:
        usuario.nombre = nombre.strip()
        cambios["nombre"] = True
    if rol is not None and rol != usuario.rol:
        cambios["rol"] = f"{usuario.rol.value}->{rol.value}"
        usuario.rol = rol
    if activo is not None and activo != usuario.activo:
        cambios["activo"] = activo
        usuario.activo = activo
    auditoria.registrar(db, accion="usuario_actualizado", usuario_id=actor.id, entidad="usuario", entidad_id=usuario.id, detalle=cambios)
    db.commit()
    return usuario


def restablecer_clave(db: Session, actor: Usuario, usuario_id: int, clave_temporal: str) -> None:
    usuario = obtener(db, usuario_id)
    _validar_clave(clave_temporal)
    usuario.password_hash = passwords.hash_password(clave_temporal)
    usuario.debe_cambiar_clave = True
    usuario.clave_cambiada_en = datetime.now(timezone.utc)
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    auditoria.registrar(db, accion="clave_restablecida", usuario_id=actor.id, entidad="usuario", entidad_id=usuario.id)
    db.commit()

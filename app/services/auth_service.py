"""Inicio de sesión con bloqueo temporal por intentos fallidos. Mensaje único para no revelar si el correo existe."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import Usuario
from app.repositories import usuario_repository as repo
from app.security import passwords
from app.security.tokens import crear_token_acceso
from app.services import auditoria

_MSG_CREDENCIALES = "Correo o contraseña incorrectos."


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def iniciar_sesion(db: Session, email: str, clave: str, ip: str | None = None) -> tuple[Usuario, str, int]:
    s = get_settings()
    usuario = repo.por_email(db, email)

    if usuario and usuario.bloqueado_hasta and usuario.bloqueado_hasta > _ahora():
        passwords.verificar_password(clave, None)  # igualar tiempos
        auditoria.registrar(db, accion="login", usuario_id=usuario.id, exito=False, detalle={"motivo": "cuenta_bloqueada"}, ip=ip)
        db.commit()
        raise ErrorNegocio("cuenta_bloqueada", "Demasiados intentos. Intenta de nuevo más tarde.", 429)

    ok = passwords.verificar_password(clave, usuario.password_hash if usuario else None)
    if not usuario or not usuario.activo or not ok:
        if usuario:
            usuario.intentos_fallidos += 1
            if usuario.intentos_fallidos >= s.login_max_intentos:
                usuario.bloqueado_hasta = _ahora() + timedelta(minutes=s.login_bloqueo_minutos)
                usuario.intentos_fallidos = 0
        auditoria.registrar(db, accion="login", usuario_id=usuario.id if usuario else None, exito=False,
                            detalle={"motivo": "credenciales" if not usuario or usuario.activo else "usuario_inactivo"}, ip=ip)
        db.commit()
        raise ErrorNegocio("credenciales_invalidas", _MSG_CREDENCIALES, 401)

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    usuario.ultimo_acceso = _ahora()
    if passwords.requiere_rehash(usuario.password_hash):
        usuario.password_hash = passwords.hash_password(clave)
    auditoria.registrar(db, accion="login", usuario_id=usuario.id, ip=ip)
    db.commit()
    token, expira = crear_token_acceso(usuario)
    return usuario, token, expira


def cambiar_clave(db: Session, usuario: Usuario, clave_actual: str, clave_nueva: str) -> None:
    if not passwords.verificar_password(clave_actual, usuario.password_hash):
        auditoria.registrar(db, accion="cambiar_clave", usuario_id=usuario.id, exito=False, detalle={"motivo": "clave_actual"})
        db.commit()
        raise ErrorNegocio("clave_actual_incorrecta", "La contraseña actual no es correcta.", 400)
    if (problemas := passwords.problemas_de_fortaleza(clave_nueva)):
        raise ErrorNegocio("clave_debil", " ".join(problemas), 422)
    if passwords.verificar_password(clave_nueva, usuario.password_hash):
        raise ErrorNegocio("clave_repetida", "La nueva contraseña debe ser distinta de la actual.", 422)
    usuario.password_hash = passwords.hash_password(clave_nueva)
    usuario.debe_cambiar_clave = False
    usuario.clave_cambiada_en = _ahora()
    auditoria.registrar(db, accion="cambiar_clave", usuario_id=usuario.id, entidad="usuario", entidad_id=usuario.id)
    db.commit()

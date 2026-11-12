"""Dependencias de FastAPI: usuario autenticado y verificación de permisos."""
from collections.abc import Callable
from datetime import timezone

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import Usuario
from app.repositories import usuario_repository as repo
from app.security.permisos import Permiso, tiene_permiso
from app.security.tokens import TokenInvalido, decodificar_token
from app.services import auditoria

_bearer = HTTPBearer(auto_error=False)
_NO_AUTENTICADO = ErrorNegocio("no_autenticado", "Debes iniciar sesión.", 401)


def usuario_desde_token(db: Session, token: str) -> Usuario:
    """Valida el token y que el usuario siga activo y no haya cambiado su clave después de emitirlo (también lo usa el WebSocket de voz)."""
    try:
        payload = decodificar_token(token)
        usuario = repo.por_id(db, int(payload["sub"]))
    except (TokenInvalido, ValueError):
        raise _NO_AUTENTICADO from None
    if not usuario or not usuario.activo:
        raise _NO_AUTENTICADO
    if usuario.clave_cambiada_en and payload["iat"] < int(usuario.clave_cambiada_en.astimezone(timezone.utc).timestamp()):
        raise ErrorNegocio("sesion_vencida", "Tu contraseña cambió; inicia sesión de nuevo.", 401)
    return usuario


def usuario_autenticado(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> Usuario:
    if cred is None:
        raise _NO_AUTENTICADO
    return usuario_desde_token(db, cred.credentials)


def usuario_actual(usuario: Usuario = Depends(usuario_autenticado)) -> Usuario:
    """Como usuario_autenticado, pero bloquea a quien debe cambiar su clave temporal."""
    if usuario.debe_cambiar_clave:
        raise ErrorNegocio("cambio_de_clave_requerido", "Debes cambiar tu contraseña antes de continuar.", 403)
    return usuario


def requiere(permiso: Permiso) -> Callable[..., Usuario]:
    def _dependencia(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)) -> Usuario:
        if not tiene_permiso(usuario.rol, permiso):
            auditoria.registrar(db, accion="acceso_denegado", usuario_id=usuario.id, exito=False,
                                detalle={"permiso": permiso.value})
            db.commit()
            raise ErrorNegocio("sin_permiso", "No tienes permiso para esta acción.", 403)
        return usuario

    return _dependencia

"""Tokens de acceso JWT (HS256, vida corta)."""
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings
from app.models import Usuario

ALGORITMO = "HS256"


class TokenInvalido(Exception):
    pass


def crear_token_acceso(usuario: Usuario, ahora: datetime | None = None) -> tuple[str, int]:
    s = get_settings()
    ahora = ahora or datetime.now(timezone.utc)
    vence = ahora + timedelta(minutes=s.access_token_minutes)
    payload = {"sub": str(usuario.id), "rol": usuario.rol.value, "iat": int(ahora.timestamp()), "exp": int(vence.timestamp())}
    return jwt.encode(payload, s.secret_key, algorithm=ALGORITMO), s.access_token_minutes * 60


def decodificar_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITMO], options={"require": ["exp", "iat", "sub"]})
    except jwt.PyJWTError as exc:
        raise TokenInvalido(type(exc).__name__) from exc

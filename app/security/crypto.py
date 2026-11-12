"""Protección de datos personales: cifrado reversible (Fernet) y huella HMAC para búsquedas.

El número de documento se guarda cifrado (para mostrarlo a quien tiene permiso) y como HMAC
(para buscar sin descifrar toda la tabla). Perder DATA_ENCRYPTION_KEY = perder esos datos.
"""
import base64
import hashlib
import hmac
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings


class ErrorDeCifrado(Exception):
    pass


def _derivar(clave_base: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(clave_base)


@lru_cache
def _clave_maestra() -> bytes:
    s = get_settings()
    if s.data_encryption_key:
        return base64.urlsafe_b64decode(s.data_encryption_key)
    return _derivar(s.secret_key.encode(), b"recep-salud/data-key-dev")  # solo desarrollo y pruebas


@lru_cache
def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_clave_maestra()))


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ErrorDeCifrado("No se pudo descifrar (clave incorrecta o dato alterado).") from exc


def normalizar_documento(numero: str) -> str:
    """'1.023.456-789' y ' 1023456789 ' deben dar el mismo documento."""
    return re.sub(r"[^0-9A-Za-z]", "", numero).upper()


def hash_documento(tipo: str, numero: str) -> str:
    clave = _derivar(_clave_maestra(), b"recep-salud/documento-hmac")
    return hmac.new(clave, f"{tipo}:{normalizar_documento(numero)}".encode(), hashlib.sha256).hexdigest()


def ultimos4(numero: str) -> str:
    return normalizar_documento(numero)[-4:]


def enmascarar(numero: str) -> str:
    n = normalizar_documento(numero)
    return "*" * max(len(n) - 4, 0) + n[-4:]

"""Contraseñas: Argon2id (nunca en texto plano) y política de fortaleza."""
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

# Parámetros mínimos recomendados por OWASP para Argon2id.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_HASH_FALSO = _hasher.hash("contraseña-inexistente-para-igualar-tiempos")


def hash_password(clave: str) -> str:
    return _hasher.hash(clave)


def verificar_password(clave: str, hash_guardado: str | None) -> bool:
    """Si el usuario no existe se verifica contra un hash falso, para no revelar por tiempo si existe."""
    try:
        return _hasher.verify(hash_guardado or _HASH_FALSO, clave) and hash_guardado is not None
    except (VerificationError, InvalidHashError):
        return False


def requiere_rehash(hash_guardado: str) -> bool:
    return _hasher.check_needs_rehash(hash_guardado)


def problemas_de_fortaleza(clave: str) -> list[str]:
    minimo = get_settings().clave_longitud_minima
    problemas = []
    if len(clave) < minimo:
        problemas.append(f"Debe tener al menos {minimo} caracteres.")
    if not re.search(r"[a-z]", clave) or not re.search(r"[A-Z]", clave):
        problemas.append("Debe combinar mayúsculas y minúsculas.")
    if not re.search(r"\d", clave):
        problemas.append("Debe incluir al menos un número.")
    return problemas

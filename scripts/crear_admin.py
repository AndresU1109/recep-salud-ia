"""Crea el primer administrador. Uso:  python -m scripts.crear_admin --email admin@tu-institucion.co --nombre "Nombre"

Si no se pasa --clave se genera una temporal aleatoria que se muestra UNA vez; el usuario
deberá cambiarla en su primer inicio de sesión.
"""
import argparse
import secrets
import string

from app.core.errores import ErrorNegocio
from app.database.session import get_session_factory
from app.models.enums import Rol
from app.services import usuario_service


def _clave_aleatoria() -> str:
    alfabeto = string.ascii_letters + string.digits
    while True:
        clave = "".join(secrets.choice(alfabeto) for _ in range(16))
        if any(c.islower() for c in clave) and any(c.isupper() for c in clave) and any(c.isdigit() for c in clave):
            return clave


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--nombre", required=True)
    p.add_argument("--clave", help="Opcional. Evítala en consolas compartidas (queda en el historial).")
    a = p.parse_args()
    clave = a.clave or _clave_aleatoria()
    with get_session_factory()() as db:
        try:
            u = usuario_service.crear(db, None, email=a.email, nombre=a.nombre, rol=Rol.ADMINISTRADOR, clave=clave)
        except ErrorNegocio as e:
            raise SystemExit(f"No se creó el usuario: {e.mensaje}")
    print(f"Administrador creado (id={u.id}).")
    if not a.clave:
        print(f"Contraseña temporal (se muestra una sola vez): {clave}")


if __name__ == "__main__":
    main()

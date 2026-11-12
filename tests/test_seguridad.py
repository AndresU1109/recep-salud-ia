"""Fase 3 (unitarias): contraseñas, cifrado, tokens y matriz de permisos."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

from app.core.config import get_settings
from app.models.enums import Rol
from app.security import crypto, passwords
from app.security.permisos import PERMISOS_POR_ROL, Permiso, tiene_permiso
from app.security.tokens import TokenInvalido, crear_token_acceso, decodificar_token

USUARIO = SimpleNamespace(id=7, rol=Rol.RECEPCIONISTA)


# ---------- contraseñas ----------
def test_hash_no_es_texto_plano_y_es_distinto_cada_vez():
    h1, h2 = passwords.hash_password("MiClaveSegura1"), passwords.hash_password("MiClaveSegura1")
    assert h1.startswith("$argon2id$") and "MiClaveSegura1" not in h1
    assert h1 != h2


def test_verificar_clave():
    h = passwords.hash_password("MiClaveSegura1")
    assert passwords.verificar_password("MiClaveSegura1", h)
    assert not passwords.verificar_password("miclavesegura1", h)
    assert not passwords.verificar_password("cualquiera", None)  # usuario inexistente
    assert not passwords.verificar_password("x", "no-es-un-hash")


@pytest.mark.parametrize("clave, ok", [
    ("MiClaveSegura1", True), ("corta1A", False), ("todominusculas123", False),
    ("TODOMAYUSCULAS123", False), ("SinNumerosAquiii", False),
])
def test_politica_de_fortaleza(clave, ok):
    assert (passwords.problemas_de_fortaleza(clave) == []) is ok


# ---------- cifrado de datos personales ----------
def test_cifrado_ida_y_vuelta_y_no_deja_texto_visible():
    token = crypto.cifrar("1023456789")
    assert "1023456789" not in token
    assert crypto.descifrar(token) == "1023456789"


def test_dato_alterado_no_se_descifra():
    token = crypto.cifrar("1023456789")
    with pytest.raises(crypto.ErrorDeCifrado):
        crypto.descifrar(token[:-4] + "AAAA")


def test_hash_de_documento_normaliza_y_distingue_tipos():
    assert crypto.hash_documento("CC", "1.023.456-789") == crypto.hash_documento("CC", " 1023456789 ")
    assert crypto.hash_documento("CC", "1023456789") != crypto.hash_documento("TI", "1023456789")
    assert "1023456789" not in crypto.hash_documento("CC", "1023456789")


def test_mascara_de_documento():
    assert crypto.enmascarar("1.023.456.789") == "******6789"
    assert crypto.ultimos4("1023456789") == "6789"


# ---------- tokens ----------
def test_token_valido_ida_y_vuelta():
    token, expira = crear_token_acceso(USUARIO)
    datos = decodificar_token(token)
    assert datos["sub"] == "7" and datos["rol"] == "RECEPCIONISTA"
    assert expira == get_settings().access_token_minutes * 60


def test_token_vencido_se_rechaza():
    token, _ = crear_token_acceso(USUARIO, ahora=datetime.now(timezone.utc) - timedelta(hours=2))
    with pytest.raises(TokenInvalido):
        decodificar_token(token)


def test_token_alterado_o_con_otra_clave_se_rechaza():
    token, _ = crear_token_acceso(USUARIO)
    with pytest.raises(TokenInvalido):
        decodificar_token(token[:-3] + "abc")
    falso = jwt.encode({"sub": "1", "rol": "ADMINISTRADOR", "iat": 1, "exp": 4102444800}, "otra-clave-distinta-de-32-bytes!!", algorithm="HS256")
    with pytest.raises(TokenInvalido):
        decodificar_token(falso)


def test_token_sin_firma_alg_none_se_rechaza():
    sin_firma = jwt.encode({"sub": "1", "iat": 1, "exp": 4102444800}, None, algorithm="none")
    with pytest.raises(TokenInvalido):
        decodificar_token(sin_firma)


# ---------- permisos ----------
def test_administrador_tiene_todos_los_permisos():
    assert all(tiene_permiso(Rol.ADMINISTRADOR, p) for p in Permiso)


def test_todos_los_roles_tienen_matriz():
    assert set(PERMISOS_POR_ROL) == set(Rol)


@pytest.mark.parametrize("rol, permiso, esperado", [
    (Rol.RECEPCIONISTA, Permiso.CITAS_GESTIONAR, True),
    (Rol.RECEPCIONISTA, Permiso.USUARIOS_GESTIONAR, False),
    (Rol.RECEPCIONISTA, Permiso.TRANSCRIPCIONES_VER, False),
    (Rol.PROFESIONAL_SALUD, Permiso.CITAS_GESTIONAR, False),
    (Rol.PROFESIONAL_SALUD, Permiso.DOCUMENTO_PACIENTE_VER, False),
    (Rol.CONSULTA, Permiso.PACIENTES_VER, False),
    (Rol.CONSULTA, Permiso.REPORTES_VER, True),
    (Rol.COORDINADOR, Permiso.INASISTENCIAS_VERIFICAR, True),
    (Rol.COORDINADOR, Permiso.USUARIOS_GESTIONAR, False),
    (Rol.COORDINADOR, Permiso.CONFIG_IA_GESTIONAR, False),
])
def test_matriz_de_permisos(rol, permiso, esperado):
    assert tiene_permiso(rol, permiso) is esperado


def test_solo_el_administrador_gestiona_usuarios():
    con_permiso = [r for r in Rol if tiene_permiso(r, Permiso.USUARIOS_GESTIONAR)]
    assert con_permiso == [Rol.ADMINISTRADOR]

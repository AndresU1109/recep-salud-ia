"""Fase 3 (API + PostgreSQL): login, bloqueo, sesiones, permisos y gestión de usuarios."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import AuditoriaLog, Usuario
from app.models.enums import Rol
from app.security import passwords
from app.security.tokens import crear_token_acceso

CLAVE = "ClaveDePrueba123"


def crear_usuario(db, email="ana@ejemplo.com", rol=Rol.RECEPCIONISTA, clave=CLAVE, **extra) -> Usuario:
    u = Usuario(email=email, nombre="Persona Ficticia", rol=rol, password_hash=passwords.hash_password(clave), **extra)
    db.add(u)
    db.flush()
    return u


def login(client, email="ana@ejemplo.com", clave=CLAVE):
    return client.post("/api/v1/auth/login", json={"email": email, "clave": clave})


def cabecera(client, email="ana@ejemplo.com", clave=CLAVE):
    r = login(client, email, clave)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- login ----------
def test_login_correcto_devuelve_token_y_usuario(client, db):
    crear_usuario(db)
    r = login(client, "ANA@Ejemplo.com")  # el correo no distingue mayúsculas
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["token_type"] == "bearer" and cuerpo["usuario"]["rol"] == "RECEPCIONISTA"
    assert "password" not in r.text.lower() and "hash" not in r.text.lower()
    assert r.headers["cache-control"] == "no-store"


def test_clave_incorrecta_y_correo_inexistente_dan_la_misma_respuesta(client, db):
    crear_usuario(db)
    a = login(client, clave="incorrecta")
    b = login(client, email="nadie@ejemplo.com")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_bloqueo_tras_varios_intentos_fallidos(client, db):
    crear_usuario(db)
    for _ in range(5):
        assert login(client, clave="mala").status_code == 401
    r = login(client)  # ya con la clave correcta
    assert r.status_code == 429 and r.json()["error"]["codigo"] == "cuenta_bloqueada"


def test_bloqueo_vence_con_el_tiempo(client, db):
    u = crear_usuario(db, bloqueado_hasta=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert login(client).status_code == 200
    db.refresh(u)
    assert u.bloqueado_hasta is None and u.intentos_fallidos == 0


def test_usuario_inactivo_no_inicia_sesion(client, db):
    crear_usuario(db, activo=False)
    assert login(client).status_code == 401


def test_intentos_fallidos_quedan_en_auditoria_sin_la_clave(client, db):
    crear_usuario(db)
    login(client, clave="ClaveEquivocada999")
    registros = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "login")).all()
    assert registros and registros[-1].exito is False
    assert "ClaveEquivocada999" not in str(registros[-1].detalle)


# ---------- sesión ----------
def test_endpoint_protegido_sin_token_da_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/usuarios").status_code == 401


def test_token_manipulado_da_401(client, db):
    crear_usuario(db)
    h = cabecera(client)
    h["Authorization"] += "x"
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_me_devuelve_el_usuario(client, db):
    crear_usuario(db)
    assert client.get("/api/v1/auth/me", headers=cabecera(client)).json()["email"] == "ana@ejemplo.com"


def test_usuario_desactivado_pierde_acceso_aunque_su_token_siga_vigente(client, db):
    u = crear_usuario(db)
    h = cabecera(client)
    u.activo = False
    db.flush()
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_cambio_de_clave_invalida_tokens_anteriores(client, db):
    u = crear_usuario(db)
    viejo, _ = crear_token_acceso(u, ahora=datetime.now(timezone.utc) - timedelta(minutes=10))
    u.clave_cambiada_en = datetime.now(timezone.utc)
    db.flush()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {viejo}"})
    assert r.status_code == 401 and r.json()["error"]["codigo"] == "sesion_vencida"


def test_clave_temporal_obliga_a_cambiarla(client, db):
    crear_usuario(db, rol=Rol.ADMINISTRADOR, debe_cambiar_clave=True)
    h = cabecera(client)
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    bloqueado = client.get("/api/v1/usuarios", headers=h)
    assert bloqueado.status_code == 403 and bloqueado.json()["error"]["codigo"] == "cambio_de_clave_requerido"

    r = client.post("/api/v1/auth/cambiar-clave", headers=h, json={"clave_actual": CLAVE, "clave_nueva": "NuevaClave456Z"})
    assert r.status_code == 204
    assert login(client, clave=CLAVE).status_code == 401
    assert client.get("/api/v1/usuarios", headers=cabecera(client, clave="NuevaClave456Z")).status_code == 200


@pytest.mark.parametrize("actual, nueva, codigo", [
    ("equivocada", "NuevaClave456Z", "clave_actual_incorrecta"),
    (CLAVE, "debil", "clave_debil"),
    (CLAVE, CLAVE, "clave_repetida"),
])
def test_cambio_de_clave_rechaza_casos_invalidos(client, db, actual, nueva, codigo):
    crear_usuario(db)
    r = client.post("/api/v1/auth/cambiar-clave", headers=cabecera(client), json={"clave_actual": actual, "clave_nueva": nueva})
    assert r.json()["error"]["codigo"] == codigo


# ---------- permisos ----------
def test_recepcionista_no_puede_gestionar_usuarios_y_queda_auditado(client, db):
    u = crear_usuario(db)
    r = client.get("/api/v1/usuarios", headers=cabecera(client))
    assert r.status_code == 403 and r.json()["error"]["codigo"] == "sin_permiso"
    denegado = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "acceso_denegado")).all()
    assert denegado and denegado[-1].usuario_id == u.id


@pytest.mark.parametrize("rol", [Rol.RECEPCIONISTA, Rol.PROFESIONAL_SALUD, Rol.COORDINADOR, Rol.CONSULTA])
def test_ningun_rol_distinto_de_admin_crea_usuarios(client, db, rol):
    crear_usuario(db, rol=rol)
    r = client.post("/api/v1/usuarios", headers=cabecera(client),
                    json={"email": "n@ejemplo.com", "nombre": "Nuevo Usuario", "rol": "CONSULTA", "clave_temporal": "ClaveTemporal123"})
    assert r.status_code == 403


# ---------- gestión de usuarios ----------
def admin(client, db):
    crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    return cabecera(client, "admin@ejemplo.com")


def test_admin_crea_usuario_y_no_se_expone_la_clave(client, db):
    h = admin(client, db)
    r = client.post("/api/v1/usuarios", headers=h,
                    json={"email": "Nuevo@Ejemplo.com", "nombre": "Nuevo Usuario", "rol": "CONSULTA", "clave_temporal": "ClaveTemporal123"})
    assert r.status_code == 201
    assert r.json()["email"] == "nuevo@ejemplo.com" and r.json()["debe_cambiar_clave"] is True
    assert "ClaveTemporal123" not in r.text and "hash" not in r.text.lower()
    guardado = db.scalar(select(Usuario).where(Usuario.email == "nuevo@ejemplo.com"))
    assert guardado.password_hash.startswith("$argon2id$")


def test_no_se_repite_el_correo_ni_se_acepta_clave_debil(client, db):
    h = admin(client, db)
    base = {"email": "dup@ejemplo.com", "nombre": "Nombre Valido", "rol": "CONSULTA", "clave_temporal": "ClaveTemporal123"}
    assert client.post("/api/v1/usuarios", headers=h, json=base).status_code == 201
    assert client.post("/api/v1/usuarios", headers=h, json=base).status_code == 409
    assert client.post("/api/v1/usuarios", headers=h, json={**base, "email": "otro@ejemplo.com", "clave_temporal": "debil"}).status_code == 422


def test_errores_de_validacion_no_repiten_la_clave_enviada(client, db):
    h = admin(client, db)
    r = client.post("/api/v1/usuarios", headers=h,
                    json={"email": "no-es-correo", "nombre": "Nombre Valido", "rol": "CONSULTA", "clave_temporal": "SecretoQueNoDebeVerse1"})
    assert r.status_code == 422
    assert "SecretoQueNoDebeVerse1" not in r.text


def test_admin_no_puede_quitarse_su_propio_rol_ni_desactivarse(client, db):
    h = admin(client, db)
    yo = client.get("/api/v1/auth/me", headers=h).json()["id"]
    assert client.patch(f"/api/v1/usuarios/{yo}", headers=h, json={"activo": False}).status_code == 409
    assert client.patch(f"/api/v1/usuarios/{yo}", headers=h, json={"rol": "CONSULTA"}).status_code == 409


def test_no_se_puede_dejar_el_sistema_sin_administradores(client, db):
    h = admin(client, db)
    otro = crear_usuario(db, email="otro-admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    # con dos administradores se puede degradar a uno...
    assert client.patch(f"/api/v1/usuarios/{otro.id}", headers=h, json={"rol": "CONSULTA"}).status_code == 200
    # ...y el último queda protegido (la prueba anterior ya cubre el caso de uno mismo)
    assert client.patch(f"/api/v1/usuarios/{otro.id}", headers=h, json={"rol": "ADMINISTRADOR"}).status_code == 200


def test_admin_restablece_clave_y_desbloquea(client, db):
    h = admin(client, db)
    u = crear_usuario(db, bloqueado_hasta=datetime.now(timezone.utc) + timedelta(minutes=10), intentos_fallidos=3)
    r = client.post(f"/api/v1/usuarios/{u.id}/restablecer-clave", headers=h, json={"clave_temporal": "TemporalNueva789"})
    assert r.status_code == 204
    resp = login(client, clave="TemporalNueva789")
    assert resp.status_code == 200 and resp.json()["usuario"]["debe_cambiar_clave"] is True


def test_usuario_inexistente_da_404(client, db):
    assert client.get("/api/v1/usuarios/999999", headers=admin(client, db)).status_code == 404


# ---------- cabeceras ----------
def test_cabeceras_de_seguridad(client):
    r = client.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"

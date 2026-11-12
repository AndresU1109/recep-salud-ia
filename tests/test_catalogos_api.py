"""Fase 4 (API): administración del catálogo y resumen del panel."""
import pytest
from sqlalchemy import select

from app.models import AuditoriaLog
from app.models.enums import Rol
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"


@pytest.fixture
def coord(client, db):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    return cabecera(client, "coord@ejemplo.com")


def sede(client, h, nombre="Sede Norte"):
    r = client.post(f"{A}/sedes", headers=h, json={"nombre": nombre, "ciudad": "Ciudad Demo"})
    assert r.status_code == 201, r.text
    return r.json()


def especialidad(client, h, nombre="Cardiología Demo"):
    r = client.post(f"{A}/especialidades", headers=h, json={"nombre": nombre})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- permisos ----------
@pytest.mark.parametrize("rol", [Rol.RECEPCIONISTA, Rol.PROFESIONAL_SALUD, Rol.CONSULTA])
def test_roles_de_lectura_pueden_listar_pero_no_crear(client, db, rol):
    crear_usuario(db, email="lector@ejemplo.com", rol=rol)
    h = cabecera(client, "lector@ejemplo.com")
    assert client.get(f"{A}/sedes", headers=h).status_code == 200
    assert client.post(f"{A}/sedes", headers=h, json={"nombre": "Sede X"}).status_code == 403
    assert client.post(f"{A}/profesionales", headers=h, json={"nombre_completo": "Nombre Ficticio", "especialidad_ids": [1]}).status_code == 403


def test_catalogo_sin_sesion_da_401(client):
    for ruta in ("sedes", "consultorios", "especialidades", "servicios", "requisitos", "profesionales"):
        assert client.get(f"{A}/{ruta}").status_code == 401


# ---------- sedes y consultorios ----------
def test_crear_editar_y_desactivar_sede(client, coord, db):
    s = sede(client, coord)
    r = client.patch(f"{A}/sedes/{s['id']}", headers=coord, json={"telefono": "6010000000", "activa": False})
    assert r.status_code == 200 and r.json()["activa"] is False and r.json()["nombre"] == "Sede Norte"
    assert client.get(f"{A}/sedes", headers=coord).json()[0]["id"] == s["id"]  # sigue existiendo (baja lógica)
    acciones = set(db.scalars(select(AuditoriaLog.accion)))
    assert {"sede_creado", "sede_actualizado"} <= acciones


def test_sede_duplicada_da_409(client, coord):
    sede(client, coord)
    r = client.post(f"{A}/sedes", headers=coord, json={"nombre": "Sede Norte"})
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "duplicado"


def test_consultorios_de_una_sede(client, coord):
    s1, s2 = sede(client, coord, "Sede Uno"), sede(client, coord, "Sede Dos")
    assert client.post(f"{A}/consultorios", headers=coord, json={"sede_id": s1["id"], "nombre": "101"}).status_code == 201
    assert client.post(f"{A}/consultorios", headers=coord, json={"sede_id": s1["id"], "nombre": "101"}).status_code == 409
    assert client.post(f"{A}/consultorios", headers=coord, json={"sede_id": s2["id"], "nombre": "101"}).status_code == 201
    assert len(client.get(f"{A}/consultorios?sede_id={s1['id']}", headers=coord).json()) == 1


def test_consultorio_en_sede_inexistente_da_422(client, coord):
    r = client.post(f"{A}/consultorios", headers=coord, json={"sede_id": 999999, "nombre": "X"})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "referencia_invalida"


# ---------- especialidades, servicios, requisitos ----------
def test_servicio_con_duracion_invalida_se_rechaza(client, coord):
    e = especialidad(client, coord)
    for minutos in (0, -5, 9999):
        r = client.post(f"{A}/servicios", headers=coord, json={"especialidad_id": e["id"], "nombre": "Consulta", "duracion_minutos": minutos})
        assert r.status_code == 422
    ok = client.post(f"{A}/servicios", headers=coord, json={"especialidad_id": e["id"], "nombre": "Consulta", "duracion_minutos": 20})
    assert ok.status_code == 201
    assert len(client.get(f"{A}/servicios?especialidad_id={e['id']}", headers=coord).json()) == 1


def test_requisito_general_y_por_especialidad(client, coord):
    e = especialidad(client, coord)
    general = client.post(f"{A}/requisitos", headers=coord, json={"titulo": "Documento de identidad", "descripcion": "Presentar el documento."})
    propio = client.post(f"{A}/requisitos", headers=coord, json={"especialidad_id": e["id"], "titulo": "Remisión", "descripcion": "Texto de prueba."})
    assert general.status_code == propio.status_code == 201 and general.json()["especialidad_id"] is None
    assert [r["titulo"] for r in client.get(f"{A}/requisitos?especialidad_id={e['id']}", headers=coord).json()] == ["Remisión"]


def test_actualizar_inexistente_da_404(client, coord):
    assert client.patch(f"{A}/especialidades/999999", headers=coord, json={"activa": False}).status_code == 404


# ---------- profesionales ----------
def test_profesional_con_especialidades(client, coord):
    e1, e2 = especialidad(client, coord, "Esp Uno"), especialidad(client, coord, "Esp Dos")
    r = client.post(f"{A}/profesionales", headers=coord, json={"nombre_completo": "Dra. Ficticia Prueba", "especialidad_ids": [e1["id"]]})
    assert r.status_code == 201 and [e["nombre"] for e in r.json()["especialidades"]] == ["Esp Uno"]
    pid = r.json()["id"]
    r = client.patch(f"{A}/profesionales/{pid}", headers=coord, json={"especialidad_ids": [e1["id"], e2["id"]]})
    assert {e["nombre"] for e in r.json()["especialidades"]} == {"Esp Uno", "Esp Dos"}
    assert client.patch(f"{A}/profesionales/{pid}", headers=coord, json={"activo": False}).json()["activo"] is False


def test_profesional_exige_especialidades_validas(client, coord):
    base = {"nombre_completo": "Dr. Ficticio Prueba"}
    assert client.post(f"{A}/profesionales", headers=coord, json={**base, "especialidad_ids": []}).status_code == 422
    r = client.post(f"{A}/profesionales", headers=coord, json={**base, "especialidad_ids": [999999]})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "referencia_invalida"


# ---------- resumen del inicio ----------
def test_resumen_muestra_solo_lo_que_el_rol_puede_ver(client, db, coord):
    sede(client, coord)
    cifras_coord = client.get(f"{A}/panel/resumen", headers=coord).json()["cifras"]
    assert cifras_coord["sedes_activas"] == 1 and "usuarios_activos" not in cifras_coord

    crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    cifras_admin = client.get(f"{A}/panel/resumen", headers=cabecera(client, "admin@ejemplo.com")).json()["cifras"]
    assert cifras_admin["usuarios_activos"] == 2

    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    cifras_consulta = client.get(f"{A}/panel/resumen", headers=cabecera(client, "consulta@ejemplo.com")).json()["cifras"]
    assert "pacientes" not in cifras_consulta and "usuarios_activos" not in cifras_consulta
    assert cifras_consulta["citas_hoy"] == 0


def test_login_incluye_permisos_para_la_interfaz(client, db):
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    r = client.post(f"{A}/auth/login", json={"email": "consulta@ejemplo.com", "clave": "ClaveDePrueba123"})
    permisos = r.json()["usuario"]["permisos"]
    assert "agenda:ver" in permisos and "usuarios:gestionar" not in permisos

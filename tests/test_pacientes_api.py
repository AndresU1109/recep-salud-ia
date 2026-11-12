"""Fase 5: pacientes (datos ficticios), privacidad, permisos y verificación de identidad."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AuditoriaLog, Paciente
from app.models.enums import Rol
from tests.factories import crear_base, nueva_cita
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"
DOC = "1023456789"  # ficticio
CONSENTIMIENTO = {"politica_version": "demo-1", "aceptado": True, "autoriza_transcripcion": False}


def cuerpo(**cambios):
    base = {"tipo_documento": "CC", "numero_documento": DOC, "nombres": "María Paula", "apellidos": "Ficticia Prueba",
            "fecha_nacimiento": "1990-05-17", "telefono": "300 123 4567", "email": "Maria@Ejemplo.com", "consentimiento": CONSENTIMIENTO}
    return {**base, **cambios}


@pytest.fixture
def recep(client, db):
    crear_usuario(db, email="recep@ejemplo.com", rol=Rol.RECEPCIONISTA)
    return cabecera(client, "recep@ejemplo.com")


def con_rol(client, db, rol):
    crear_usuario(db, email=f"{rol.value.lower()}@ejemplo.com", rol=rol)
    return cabecera(client, f"{rol.value.lower()}@ejemplo.com")


def registrar(client, h, **cambios):
    r = client.post(f"{A}/pacientes", headers=h, json=cuerpo(**cambios))
    assert r.status_code == 201, r.text
    return r.json()


# ---------- registro ----------
def test_registro_oculta_el_documento_y_lo_guarda_cifrado(client, db, recep):
    r = client.post(f"{A}/pacientes", headers=recep, json=cuerpo())
    assert r.status_code == 201
    p = r.json()
    assert p["documento"] == "****6789" and DOC not in r.text
    assert p["email"] == "maria@ejemplo.com" and p["consentimiento_vigente"]["aceptado"] is True
    fila = db.scalar(select(Paciente).where(Paciente.id == p["id"]))
    assert DOC not in fila.documento_cifrado and DOC not in fila.documento_hash and fila.documento_ultimos4 == "6789"


def test_no_se_registra_sin_consentimiento(client, db, recep):
    r = client.post(f"{A}/pacientes", headers=recep, json=cuerpo(consentimiento={**CONSENTIMIENTO, "aceptado": False}))
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "sin_consentimiento"
    assert db.scalar(select(Paciente)) is None


def test_documento_duplicado_se_rechaza_aunque_cambie_el_formato(client, recep):
    registrar(client, recep)
    for numero in (DOC, "1.023.456.789", " 1023456789 "):
        r = client.post(f"{A}/pacientes", headers=recep, json=cuerpo(numero_documento=numero))
        assert r.status_code == 409 and r.json()["error"]["codigo"] == "paciente_duplicado"
    # el mismo número con otro tipo de documento es otra persona
    assert client.post(f"{A}/pacientes", headers=recep, json=cuerpo(tipo_documento="CE")).status_code == 201


@pytest.mark.parametrize("campo, valor", [
    ("fecha_nacimiento", (date.today() + timedelta(days=1)).isoformat()),
    ("fecha_nacimiento", "1800-01-01"),
    ("telefono", "abc"), ("email", "sin-arroba"), ("numero_documento", "12"), ("nombres", "A"),
])
def test_datos_invalidos_se_rechazan_sin_repetir_el_documento(client, recep, campo, valor):
    r = client.post(f"{A}/pacientes", headers=recep, json=cuerpo(**{campo: valor}))
    assert r.status_code == 422
    assert DOC not in r.text


# ---------- búsqueda ----------
def test_buscar_por_nombre_parcial_y_por_documento(client, recep):
    p = registrar(client, recep)
    registrar(client, recep, numero_documento="9876543210", nombres="Carlos", apellidos="Otro Ficticio", email=None, telefono=None)
    por_nombre = client.get(f"{A}/pacientes", headers=recep, params={"q": "maría ficti"}).json()
    assert [x["id"] for x in por_nombre] == [p["id"]]
    for q in (DOC, "1.023.456.789"):
        assert [x["id"] for x in client.get(f"{A}/pacientes", headers=recep, params={"q": q}).json()] == [p["id"]]


def test_la_busqueda_no_interpreta_comodines(client, recep):
    registrar(client, recep)
    assert client.get(f"{A}/pacientes", headers=recep, params={"q": "%%"}).json() == []
    assert client.get(f"{A}/pacientes", headers=recep, params={"q": "_"}).status_code == 422  # demasiado corta


def test_busqueda_y_consulta_quedan_auditadas_sin_guardar_lo_buscado(client, db, recep):
    p = registrar(client, recep)
    client.get(f"{A}/pacientes", headers=recep, params={"q": DOC})
    client.get(f"{A}/pacientes/{p['id']}", headers=recep)
    registros = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion.in_(["pacientes_busqueda", "paciente_consultado"]))).all()
    assert {r.accion for r in registros} == {"pacientes_busqueda", "paciente_consultado"}
    assert DOC not in str([r.detalle for r in registros])


# ---------- permisos ----------
def test_rol_consulta_no_ve_pacientes(client, db):
    h = con_rol(client, db, Rol.CONSULTA)
    assert client.get(f"{A}/pacientes", headers=h, params={"q": "maria"}).status_code == 403
    assert client.post(f"{A}/pacientes", headers=h, json=cuerpo()).status_code == 403


def test_profesional_ve_pero_no_crea_ni_edita_ni_ve_el_documento(client, db, recep):
    p = registrar(client, recep)
    h = con_rol(client, db, Rol.PROFESIONAL_SALUD)
    assert client.get(f"{A}/pacientes/{p['id']}", headers=h).status_code == 200
    assert client.post(f"{A}/pacientes", headers=h, json=cuerpo(numero_documento="5555555")).status_code == 403
    assert client.patch(f"{A}/pacientes/{p['id']}", headers=h, json={"telefono": "3001112222"}).status_code == 403
    assert client.get(f"{A}/pacientes/{p['id']}/documento", headers=h).status_code == 403


def test_solo_algunos_roles_ven_el_documento_completo_y_queda_auditado(client, db, recep):
    p = registrar(client, recep)
    r = client.get(f"{A}/pacientes/{p['id']}/documento", headers=recep)
    assert r.status_code == 200 and r.json()["numero_documento"] == DOC
    assert db.scalar(select(AuditoriaLog).where(AuditoriaLog.accion == "paciente_documento_revelado")) is not None
    assert client.get(f"{A}/pacientes/{p['id']}/documento", headers=con_rol(client, db, Rol.COORDINADOR)).status_code == 403


def test_endpoints_de_pacientes_exigen_sesion(client):
    for ruta in ("", "/1", "/1/documento", "/1/citas", "/1/consentimientos"):
        assert client.get(f"{A}/pacientes{ruta}").status_code == 401


# ---------- actualización ----------
def test_actualizar_contacto_pero_no_documento_ni_fecha(client, recep):
    p = registrar(client, recep)
    r = client.patch(f"{A}/pacientes/{p['id']}", headers=recep, json={"telefono": "3109998877", "entidad_aseguradora": "Aseguradora Demo"})
    assert r.status_code == 200 and r.json()["telefono"] == "3109998877"
    for prohibido in ({"numero_documento": "1"}, {"fecha_nacimiento": "2000-01-01"}, {"tipo_documento": "TI"}):
        assert client.patch(f"{A}/pacientes/{p['id']}", headers=recep, json=prohibido).status_code == 422


def test_actualizar_inexistente_da_404(client, recep):
    assert client.patch(f"{A}/pacientes/999999", headers=recep, json={"telefono": "3001112222"}).status_code == 404


# ---------- consentimiento ----------
def test_revocar_consentimiento_queda_como_vigente(client, recep):
    p = registrar(client, recep)
    r = client.post(f"{A}/pacientes/{p['id']}/consentimientos", headers=recep,
                    json={"politica_version": "demo-2", "aceptado": False, "autoriza_transcripcion": True})
    assert r.status_code == 201 and r.json()["autoriza_transcripcion"] is False  # sin consentimiento no hay transcripción
    vigente = client.get(f"{A}/pacientes/{p['id']}", headers=recep).json()["consentimiento_vigente"]
    assert vigente["aceptado"] is False and vigente["politica_version"] == "demo-2"
    assert len(client.get(f"{A}/pacientes/{p['id']}/consentimientos", headers=recep).json()) == 2


# ---------- verificación de identidad ----------
def verificar(client, h, nacimiento="1990-05-17", numero=DOC):
    return client.post(f"{A}/pacientes/verificar-identidad", headers=h,
                       json={"tipo_documento": "CC", "numero_documento": numero, "fecha_nacimiento": nacimiento})


def test_verificacion_correcta(client, recep):
    p = registrar(client, recep)
    r = verificar(client, recep)
    assert r.status_code == 200 and r.json()["id"] == p["id"]
    assert verificar(client, recep, numero="1.023.456.789").status_code == 200


def test_verificacion_no_revela_si_el_paciente_existe(client, recep):
    registrar(client, recep)
    fecha_mala = verificar(client, recep, nacimiento="1990-05-18")
    no_existe = verificar(client, recep, numero="5550001112")
    assert fecha_mala.status_code == no_existe.status_code == 403
    assert fecha_mala.json() == no_existe.json()


def test_verificacion_se_bloquea_tras_cinco_fallos(client, recep):
    registrar(client, recep)
    for _ in range(5):
        assert verificar(client, recep, nacimiento="1990-05-18").status_code == 403
    r = verificar(client, recep)  # ni con la fecha correcta
    assert r.status_code == 429 and r.json()["error"]["codigo"] == "verificacion_bloqueada"


def test_el_bloqueo_de_un_documento_no_afecta_a_otro(client, recep):
    registrar(client, recep)
    registrar(client, recep, numero_documento="7777777", email=None, telefono=None)
    for _ in range(5):
        verificar(client, recep, nacimiento="1990-05-18")
    assert verificar(client, recep, numero="7777777").status_code == 200


def test_paciente_inactivo_no_se_verifica(client, recep):
    p = registrar(client, recep)
    client.patch(f"{A}/pacientes/{p['id']}", headers=recep, json={"activo": False})
    assert verificar(client, recep).status_code == 403


def test_la_auditoria_de_verificacion_no_guarda_el_documento(client, db, recep):
    registrar(client, recep)
    verificar(client, recep, nacimiento="1990-05-18")
    registros = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "verificar_identidad")).all()
    assert registros and DOC not in str([(r.entidad_id, r.detalle) for r in registros])


# ---------- historial de citas ----------
def test_historial_administrativo_de_citas(client, db, recep):
    p = registrar(client, recep)
    base = crear_base(db)
    cita = nueva_cita(db, base, pac=SimpleNamespace(id=p["id"]))
    r = client.get(f"{A}/pacientes/{p['id']}/citas", headers=recep)
    assert r.status_code == 200 and len(r.json()) == 1
    fila = r.json()[0]
    assert fila["codigo_reserva"] == cita.codigo_reserva and fila["profesional"] == "Dra. Ficticia Uno" and fila["estado"] == "PROGRAMADA"
    assert client.get(f"{A}/pacientes/999999/citas", headers=recep).status_code == 404

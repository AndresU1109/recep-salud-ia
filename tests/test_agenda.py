"""Fase 6: motor de disponibilidad y API de agenda (datos ficticios; lunes 2030-03-04, hora de Bogotá)."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.errores import ErrorNegocio
from app.models import Consultorio, ExcepcionAgenda, HorarioProfesional, Servicio
from app.models.enums import EstadoCita, Rol
from app.schemas.agenda import EstadoSlot
from app.services import agenda_service as svc
from tests.factories import crear_base, nueva_cita, nuevo_paciente
from tests.test_auth_api import cabecera, crear_usuario

BOG = ZoneInfo("America/Bogota")
LUNES = date(2030, 3, 4)
MARTES = date(2030, 3, 5)
AHORA = datetime(2030, 1, 1, tzinfo=timezone.utc)  # "hoy" fijo y anterior a todo


def local(h, m=0, dia=LUNES):
    return datetime(dia.year, dia.month, dia.day, h, m, tzinfo=BOG)


def horario(db, b, *, prof=None, dia=0, ini=time(8), fin=time(12), dur=20, cupos=None, cons="cons1", desde=date(2020, 1, 1), hasta=None, activo=True):
    prof = prof or b.prof1
    if b.esp not in prof.especialidades:
        prof.especialidades.append(b.esp)
    h = HorarioProfesional(profesional_id=prof.id, especialidad_id=b.esp.id, sede_id=b.sede.id, consultorio_id=getattr(b, cons).id if cons else None,
                           dia_semana=dia, hora_inicio=ini, hora_fin=fin, duracion_cita_min=dur, cupos_maximos=cupos, vigente_desde=desde,
                           vigente_hasta=hasta, activo=activo)
    db.add(h)
    db.flush()
    return h


def slots(db, **kw):
    return svc.generar_slots(db, kw.pop("desde", LUNES), kw.pop("hasta", LUNES), ahora=kw.pop("ahora", AHORA), **kw)


def estados(lista):
    return [s.estado.value for s in lista]


# ---------- generación básica ----------
def test_genera_los_cupos_del_horario_solo_el_dia_que_corresponde(db):
    b = crear_base(db)
    horario(db, b)
    lunes = slots(db)
    assert len(lunes) == 12 and set(estados(lunes)) == {"LIBRE"}
    assert slots(db, desde=MARTES, hasta=MARTES) == []


def test_los_cupos_estan_en_hora_de_bogota(db):
    b = crear_base(db)
    horario(db, b)
    primero = slots(db)[0]
    assert primero.inicio.utcoffset() == timedelta(hours=-5) and primero.inicio.astimezone(timezone.utc).hour == 13
    assert primero.fin - primero.inicio == timedelta(minutes=20)


def test_el_ultimo_cupo_no_se_sale_de_la_jornada(db):
    b = crear_base(db)
    horario(db, b, ini=time(8), fin=time(8, 50), dur=20)  # caben 2 (8:00 y 8:20); 8:40 terminaría 9:00
    assert [s.inicio.strftime("%H:%M") for s in slots(db)] == ["08:00", "08:20"]


# ---------- ocupación ----------
def test_cita_ocupa_su_cupo_y_se_ve_el_codigo(db):
    b = crear_base(db)
    horario(db, b)
    c = nueva_cita(db, b, inicio=local(9), prof=b.prof1, cons=b.cons1)
    res = {s.inicio.strftime("%H:%M"): s for s in slots(db)}
    assert res["09:00"].estado == EstadoSlot.OCUPADO and res["09:00"].detalle == c.codigo_reserva
    assert res["08:40"].estado == res["09:20"].estado == EstadoSlot.LIBRE
    assert "09:00" not in [s.inicio.strftime("%H:%M") for s in svc.disponibilidad(db, LUNES, LUNES)]


def test_cita_cancelada_libera_el_cupo(db):
    b = crear_base(db)
    horario(db, b)
    c = nueva_cita(db, b, inicio=local(9))
    c.estado = EstadoCita.CANCELADA
    db.flush()
    assert set(estados(slots(db))) == {"LIBRE"}


def test_un_consultorio_compartido_bloquea_a_otro_profesional(db):
    b = crear_base(db)
    horario(db, b, prof=b.prof1)
    horario(db, b, prof=b.prof2)  # mismo consultorio c1
    nueva_cita(db, b, inicio=local(9), prof=b.prof1, cons=b.cons1)
    del_prof2 = [s for s in slots(db) if s.profesional_id == b.prof2.id and s.inicio == local(9)][0]
    assert del_prof2.estado == EstadoSlot.OCUPADO


# ---------- bloqueos ----------
@pytest.mark.parametrize("filtros", [{"profesional_id": True}, {"sede_id": True}, {}])
def test_bloqueos_por_profesional_sede_o_globales(db, filtros):
    b = crear_base(db)
    horario(db, b)
    db.add(ExcepcionAgenda(inicio=local(9), fin=local(10), motivo="Capacitación",
                           profesional_id=b.prof1.id if filtros.get("profesional_id") else None, sede_id=b.sede.id if filtros.get("sede_id") else None))
    db.flush()
    bloqueados = [s.inicio.strftime("%H:%M") for s in slots(db) if s.estado == EstadoSlot.BLOQUEADO]
    assert bloqueados == ["09:00", "09:20", "09:40"]
    assert slots(db)[3].detalle == "Capacitación"


def test_bloqueo_de_otro_profesional_no_afecta(db):
    b = crear_base(db)
    horario(db, b)
    db.add(ExcepcionAgenda(inicio=local(8), fin=local(12), motivo="Vacaciones", profesional_id=b.prof2.id))
    db.flush()
    assert set(estados(slots(db))) == {"LIBRE"}


# ---------- cupos, pasado, vigencia, estados ----------
def test_cupos_maximos_de_la_jornada(db):
    b = crear_base(db)
    horario(db, b, cupos=3)
    assert estados(slots(db)).count("LIBRE") == 3 and estados(slots(db)).count("SIN_CUPO") == 9
    nueva_cita(db, b, inicio=local(11, 40))
    assert estados(slots(db)).count("LIBRE") == 2


def test_los_cupos_pasados_no_se_ofrecen(db):
    b = crear_base(db)
    horario(db, b)
    lista = slots(db, ahora=local(9, 30))
    assert [s.inicio.strftime("%H:%M") for s in lista if s.estado == EstadoSlot.LIBRE][0] == "09:40"
    assert estados(lista)[:5] == ["PASADO"] * 5


@pytest.mark.parametrize("desde, hasta", [(date(2020, 1, 1), LUNES - timedelta(days=1)), (MARTES, None)])
def test_vigencia_del_horario(db, desde, hasta):
    b = crear_base(db)
    horario(db, b, desde=desde, hasta=hasta)
    assert slots(db) == []


def test_horario_o_entidades_inactivas_no_generan_cupos(db):
    b = crear_base(db)
    h = horario(db, b)
    for apagar in (lambda: setattr(h, "activo", False), lambda: setattr(b.prof1, "activo", False), lambda: setattr(b.sede, "activa", False),
                   lambda: setattr(b.esp, "activa", False), lambda: setattr(b.cons1, "activo", False)):
        h.activo, b.prof1.activo, b.sede.activa, b.esp.activa, b.cons1.activo = True, True, True, True, True
        apagar()
        db.flush()
        assert slots(db) == [], apagar


# ---------- servicios con duración propia ----------
def test_servicio_de_40_min_sobre_rejilla_de_20(db):
    b = crear_base(db)
    horario(db, b)
    serv = Servicio(especialidad_id=b.esp.id, nombre="Control largo", duracion_minutos=40)
    db.add(serv)
    db.flush()
    lista = slots(db, servicio_id=serv.id)
    assert lista[0].fin - lista[0].inicio == timedelta(minutes=40)
    assert lista[-1].inicio.strftime("%H:%M") == "11:20"  # 11:40 terminaría pasadas las 12:00


def test_servicio_de_otra_especialidad_se_rechaza(db):
    b = crear_base(db)
    horario(db, b)
    otra = crear_base(db)
    serv = Servicio(especialidad_id=otra.esp.id, nombre="Otro", duracion_minutos=20)
    db.add(serv)
    db.flush()
    with pytest.raises(ErrorNegocio) as e:
        slots(db, servicio_id=serv.id, especialidad_id=b.esp.id)
    assert e.value.codigo == "servicio_invalido"


# ---------- límites y reconfirmación ----------
def test_rangos_invalidos(db):
    for desde, hasta in ((LUNES, LUNES - timedelta(days=1)), (LUNES, LUNES + timedelta(days=62))):
        with pytest.raises(ErrorNegocio):
            slots(db, desde=desde, hasta=hasta)


def test_buscar_slot_libre_para_reconfirmar(db):
    b = crear_base(db)
    horario(db, b)
    assert svc.buscar_slot_libre(db, b.prof1.id, local(9), especialidad_id=b.esp.id) is not None
    nueva_cita(db, b, inicio=local(9))
    assert svc.buscar_slot_libre(db, b.prof1.id, local(9), especialidad_id=b.esp.id) is None
    assert svc.buscar_slot_libre(db, b.prof1.id, local(9, 5), especialidad_id=b.esp.id) is None  # no está en la rejilla


# ======================= API =======================
A = "/api/v1"


@pytest.fixture
def coord(client, db):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    return cabecera(client, "coord@ejemplo.com")


def cuerpo_horario(b, **cambios):
    b.prof1.especialidades = [b.esp]
    return {"profesional_id": b.prof1.id, "especialidad_id": b.esp.id, "sede_id": b.sede.id, "consultorio_id": b.cons1.id,
            "dia_semana": 0, "hora_inicio": "08:00:00", "hora_fin": "12:00:00", "duracion_cita_min": 20, **cambios}


def test_roles_de_lectura_no_crean_horarios(client, db):
    b = crear_base(db)
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    h = cabecera(client, "rec@ejemplo.com")
    assert client.get(f"{A}/horarios", headers=h).status_code == 200
    assert client.post(f"{A}/horarios", headers=h, json=cuerpo_horario(b)).status_code == 403


def test_crear_horario_valido_y_sin_sesion(client, db, coord):
    b = crear_base(db)
    r = client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b))
    assert r.status_code == 201 and r.json()["dia_semana"] == 0
    assert client.get(f"{A}/horarios").status_code == 401


def test_validaciones_de_horario(client, db, coord):
    b, otra = crear_base(db), crear_base(db)
    r = client.post(f"{A}/horarios", headers=coord, json={**cuerpo_horario(b), "hora_fin": "07:00:00"})
    assert r.status_code == 422
    r = client.post(f"{A}/horarios", headers=coord, json={**cuerpo_horario(b), "especialidad_id": otra.esp.id})
    assert r.json()["error"]["codigo"] == "especialidad_no_asignada"
    r = client.post(f"{A}/horarios", headers=coord, json={**cuerpo_horario(b), "consultorio_id": otra.cons1.id})
    assert r.json()["error"]["codigo"] == "consultorio_invalido"
    assert client.post(f"{A}/horarios", headers=coord, json={**cuerpo_horario(b), "dia_semana": 9}).status_code == 422
    assert client.post(f"{A}/horarios", headers=coord, json={**cuerpo_horario(b), "duracion_cita_min": 1}).status_code == 422


def test_horarios_solapados_se_rechazan_pero_contiguos_o_de_otro_dia_si(client, db, coord):
    b = crear_base(db)
    assert client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b)).status_code == 201
    r = client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b, hora_inicio="11:00:00", hora_fin="14:00:00"))
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "horario_solapado"
    assert client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b, hora_inicio="12:00:00", hora_fin="16:00:00")).status_code == 201
    assert client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b, dia_semana=1)).status_code == 201


def test_editar_a_un_horario_que_se_cruza_se_rechaza(client, db, coord):
    b = crear_base(db)
    client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b))
    h2 = client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b, hora_inicio="14:00:00", hora_fin="16:00:00")).json()
    r = client.patch(f"{A}/horarios/{h2['id']}", headers=coord, json={"hora_inicio": "11:00:00"})
    assert r.status_code == 409
    assert client.get(f"{A}/horarios", headers=coord, params={"profesional_id": b.prof1.id}).json()[1]["hora_inicio"] == "14:00:00"


def test_desactivar_un_horario_con_citas_futuras_advierte_sin_cancelarlas(client, db, coord):
    b = crear_base(db)
    h = client.post(f"{A}/horarios", headers=coord, json=cuerpo_horario(b)).json()
    cita = nueva_cita(db, b, inicio=local(9))
    r = client.patch(f"{A}/horarios/{h['id']}", headers=coord, json={"activo": False})
    assert r.status_code == 200 and "fuera del horario" in r.json()["advertencias"][0]
    db.refresh(cita)
    assert cita.estado == EstadoCita.PROGRAMADA


def test_bloqueo_informa_citas_afectadas_y_se_puede_eliminar(client, db, coord):
    b = crear_base(db)
    nueva_cita(db, b, inicio=local(9))
    nueva_cita(db, b, inicio=local(9, 20), pac=b.pac2, cons=b.cons2, prof=b.prof2)
    cuerpo = {"profesional_id": b.prof1.id, "inicio": local(8).isoformat(), "fin": local(12).isoformat(), "motivo": "Congreso"}
    r = client.post(f"{A}/excepciones-agenda", headers=coord, json=cuerpo)
    assert r.status_code == 201 and r.json()["citas_afectadas"] == 1  # solo la del profesional bloqueado
    assert len(client.get(f"{A}/excepciones-agenda", headers=coord).json()) == 1
    assert client.delete(f"{A}/excepciones-agenda/{r.json()['id']}", headers=coord).status_code == 204
    assert client.delete(f"{A}/excepciones-agenda/{r.json()['id']}", headers=coord).status_code == 404


def test_bloqueo_exige_zona_horaria_y_rango_valido(client, db, coord):
    b = crear_base(db)
    base = {"profesional_id": b.prof1.id, "motivo": "Prueba"}
    assert client.post(f"{A}/excepciones-agenda", headers=coord, json={**base, "inicio": "2030-03-04T08:00:00", "fin": "2030-03-04T09:00:00"}).status_code == 422
    assert client.post(f"{A}/excepciones-agenda", headers=coord, json={**base, "inicio": local(9).isoformat(), "fin": local(8).isoformat()}).status_code == 422
    assert client.post(f"{A}/excepciones-agenda", headers=coord, json={"profesional_id": 999999, "inicio": local(8).isoformat(), "fin": local(9).isoformat(), "motivo": "x y z"}).status_code == 422


def test_api_de_disponibilidad_y_agenda_del_dia(client, db):
    b = crear_base(db)
    horario(db, b)
    nueva_cita(db, b, inicio=local(9))
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    h = cabecera(client, "consulta@ejemplo.com")
    libres = client.get(f"{A}/agenda/disponibilidad", headers=h, params={"desde": "2030-03-04", "hasta": "2030-03-04", "especialidad_id": b.esp.id})
    assert libres.status_code == 200 and len(libres.json()) == 11 and all(s["estado"] == "LIBRE" for s in libres.json())
    dia = client.get(f"{A}/agenda/dia", headers=h, params={"fecha": "2030-03-04", "profesional_id": b.prof1.id}).json()
    assert len(dia) == 12 and [s["estado"] for s in dia].count("OCUPADO") == 1
    assert "paciente" not in str(dia).lower()  # la agenda no expone datos de pacientes
    assert client.get(f"{A}/agenda/disponibilidad", headers=h, params={"desde": "2030-03-04", "hasta": "2030-01-01"}).status_code == 422
    assert client.get(f"{A}/agenda/dia", params={"fecha": "2030-03-04"}).status_code == 401

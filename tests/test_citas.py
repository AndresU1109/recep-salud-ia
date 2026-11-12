"""Fase 7: ciclo de vida de las citas, políticas, inasistencias y lista de espera (datos ficticios)."""
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Cita, CitaHistorial, ConsentimientoDatos, ExcepcionAgenda, ListaEspera, PoliticaInstitucional, Servicio
from app.models.enums import AccionCita, CanalCita, EstadoCita, EstadoListaEspera, Rol, TipoPolitica
from app.schemas.agenda import EstadoSlot
from app.services import agenda_service
from app.services import cita_service as svc
from tests.factories import crear_base, nueva_cita, nuevo_paciente
from tests.test_agenda import LUNES, horario, local, slots
from tests.test_auth_api import cabecera, crear_usuario

STAFF = svc.Actor(svc.ActorTipo.USUARIO, None, False)
COORD = svc.Actor(svc.ActorTipo.USUARIO, None, True)  # puede forzar políticas / verificar


def escenario(db, **kw):
    b = crear_base(db)
    h = horario(db, b, **kw)
    return b, h


def agendar(db, b, inicio=None, **kw):
    kw.setdefault("canal", CanalCita.WEB)
    kw.setdefault("actor", STAFF)
    return svc.agendar(db, paciente_id=kw.pop("paciente", b.pac1).id, profesional_id=kw.pop("prof", b.prof1).id, especialidad_id=b.esp.id,
                       inicio=inicio or local(9), **kw)


def codigo_error(fn, *a, **k):
    with pytest.raises(ErrorNegocio) as e:
        fn(*a, **k)
    return e.value.codigo


def politica(db, tipo, parametros, **extra):
    p = PoliticaInstitucional(tipo=tipo, titulo="Política de prueba", parametros=parametros, vigente_desde=date(2020, 1, 1), **extra)
    db.add(p)
    db.flush()
    return p


def cita_cercana(db, b, horas=2, **kw):
    """Una cita dentro de pocas horas (para probar 'horas mínimas'), creada directo en la BD."""
    inicio = (datetime.now(timezone.utc) + timedelta(hours=horas)).replace(second=0, microsecond=0)
    return nueva_cita(db, b, inicio=inicio, **kw)


# ======================= agendar =======================
def test_agendar_crea_la_cita_con_codigo_y_deja_historial(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    assert len(c.codigo_reserva) == 8 and set(c.codigo_reserva) <= set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    assert c.estado == EstadoCita.PROGRAMADA and c.inicio == local(9) and c.consultorio_id == b.cons1.id and c.sede_id == b.sede.id
    h = db.scalars(select(CitaHistorial).where(CitaHistorial.cita_id == c.id)).all()
    assert [x.accion for x in h] == [AccionCita.CREADA] and h[0].estado_anterior is None


def test_el_cupo_agendado_deja_de_estar_libre(db):
    b, _ = escenario(db)
    agendar(db, b)
    assert agenda_service.buscar_slot_libre(db, b.prof1.id, local(9), especialidad_id=b.esp.id) is None


@pytest.mark.parametrize("cuando, esperado", [(local(9, 5), "cupo_no_disponible"),   # fuera de la rejilla
                                              (local(13), "cupo_no_disponible"),      # fuera de la jornada
                                              (local(9, dia=LUNES + timedelta(days=1)), "cupo_no_disponible")])  # martes: sin horario
def test_no_se_agenda_fuera_de_los_cupos_publicados(db, cuando, esperado):
    b, _ = escenario(db)
    assert codigo_error(agendar, db, b, cuando) == esperado


def test_no_se_agenda_un_cupo_ocupado_ni_bloqueado_ni_pasado(db):
    b, _ = escenario(db)
    agendar(db, b)
    assert codigo_error(agendar, db, b, local(9), paciente=b.pac2) == "cupo_no_disponible"
    db.add(ExcepcionAgenda(inicio=local(10), fin=local(11), motivo="Cierre", profesional_id=b.prof1.id))
    db.flush()
    assert codigo_error(agendar, db, b, local(10), paciente=b.pac2) == "cupo_no_disponible"
    pasado = crear_base(db)
    horario(db, pasado, desde=date(2020, 1, 1))
    antes = datetime(2020, 3, 2, 9, tzinfo=local(9).tzinfo)
    assert codigo_error(agendar, db, pasado, antes) == "cupo_no_disponible"


def test_paciente_sin_consentimiento_o_inactivo_no_agenda(db):
    b, _ = escenario(db)
    sin = nuevo_paciente(db, con_consentimiento=False)
    assert codigo_error(agendar, db, b, paciente=sin) == "sin_consentimiento"
    db.add(ConsentimientoDatos(paciente_id=b.pac2.id, politica_version="demo-2", aceptado=False, canal="PRUEBA"))  # revocó
    db.flush()
    assert codigo_error(agendar, db, b, paciente=b.pac2) == "sin_consentimiento"
    b.pac1.activo = False
    db.flush()
    assert codigo_error(agendar, db, b) == "paciente_no_encontrado"


def test_un_paciente_no_puede_tener_dos_citas_a_la_misma_hora(db):
    b, _ = escenario(db)
    horario(db, b, prof=b.prof2, cons="cons2")
    agendar(db, b)
    assert codigo_error(agendar, db, b, prof=b.prof2) == "paciente_con_cita_en_ese_horario"


def test_dos_reservas_simultaneas_del_mismo_cupo_solo_una_gana(engine_pruebas, monkeypatch):
    """Ambas pasan la reconfirmación (se fuerza con una barrera) y compiten en el INSERT: PostgreSQL deja pasar una."""
    with Session(engine_pruebas) as s:
        b = crear_base(s)
        horario(s, b)
        ids = SimpleNamespace(prof=b.prof1.id, esp=b.esp.id, p1=b.pac1.id, p2=b.pac2.id)
        s.commit()
    barrera = threading.Barrier(2)
    original = agenda_service.buscar_slot_libre

    def con_barrera(*a, **k):
        r = original(*a, **k)
        barrera.wait(timeout=20)  # los dos ya vieron el cupo "libre" antes de que alguno inserte
        return r

    monkeypatch.setattr(agenda_service, "buscar_slot_libre", con_barrera)
    resultados = []

    def reservar(pac):
        with Session(engine_pruebas) as s:
            try:
                svc.agendar(s, paciente_id=pac, profesional_id=ids.prof, especialidad_id=ids.esp, inicio=local(9), actor=STAFF, canal=CanalCita.WEB)
                resultados.append("ok")
            except ErrorNegocio as e:
                s.rollback()
                resultados.append(e.codigo)

    hilos = [threading.Thread(target=reservar, args=(p,)) for p in (ids.p1, ids.p2)]
    try:
        [t.start() for t in hilos]
        [t.join(40) for t in hilos]
        assert sorted(resultados) == ["cupo_no_disponible", "ok"]
        with Session(engine_pruebas) as s:
            assert s.scalar(select(text("count(*)")).select_from(Cita)) == 1
    finally:
        with engine_pruebas.begin() as c:
            c.execute(text("TRUNCATE citas, citas_historial, horarios_profesional, consentimientos_datos, pacientes, consultorios, "
                           "profesional_especialidad, profesionales, especialidades, sedes RESTART IDENTITY CASCADE"))


@pytest.mark.parametrize("sqlstate, es_conflicto", [("40P01", True), ("40001", True), ("08006", False)])
def test_un_deadlock_o_fallo_de_serializacion_se_trata_como_cupo_ocupado(sqlstate, es_conflicto):
    """PostgreSQL puede resolver dos reservas simultáneas con un deadlock en vez de una violación de EXCLUDE."""
    from sqlalchemy.exc import OperationalError

    orig = type("Err", (Exception,), {"sqlstate": sqlstate, "diag": None})()
    exc = OperationalError("INSERT ...", {}, orig)
    assert svc._es_conflicto_de_concurrencia(exc) is es_conflicto
    if es_conflicto:
        assert svc._traducir_choque(exc).codigo == "cupo_no_disponible" and svc._traducir_choque(exc).status == 409


# ======================= confirmar =======================
def test_confirmar_y_no_confirmar_una_cancelada(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    assert svc.confirmar(db, c.id, STAFF).estado == EstadoCita.CONFIRMADA and c.confirmada_en is not None
    assert svc.confirmar(db, c.id, STAFF).estado == EstadoCita.CONFIRMADA  # idempotente
    svc.cancelar(db, c.id, None, STAFF)
    assert codigo_error(svc.confirmar, db, c.id, STAFF) == "estado_no_permite"


# ======================= cancelar =======================
def test_cancelar_libera_el_cupo_y_guarda_motivo_e_historial(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    cancelada, _cand = svc.cancelar(db, c.id, "  Viaje  ", STAFF)
    assert cancelada.estado == EstadoCita.CANCELADA and cancelada.motivo_cancelacion == "Viaje" and cancelada.cancelada_en
    assert agenda_service.buscar_slot_libre(db, b.prof1.id, local(9), especialidad_id=b.esp.id) is not None
    acciones = [h.accion for h in db.scalars(select(CitaHistorial).where(CitaHistorial.cita_id == c.id).order_by(CitaHistorial.id))]
    assert acciones == [AccionCita.CREADA, AccionCita.CANCELADA]
    assert codigo_error(svc.cancelar, db, c.id, None, STAFF) == "estado_no_permite"


def test_una_cita_ajena_responde_no_existe(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    for operacion in (lambda: svc.cancelar(db, c.id, None, svc.AGENTE, solo_paciente_id=b.pac2.id),
                      lambda: svc.confirmar(db, c.id, svc.AGENTE, solo_paciente_id=b.pac2.id),
                      lambda: svc.reprogramar(db, c.id, local(10), svc.AGENTE, solo_paciente_id=b.pac2.id),
                      lambda: svc.obtener_por_codigo(db, c.codigo_reserva, solo_paciente_id=b.pac2.id)):
        assert codigo_error(operacion) == "cita_no_encontrada"
    assert svc.obtener_por_codigo(db, c.codigo_reserva.lower(), solo_paciente_id=b.pac1.id).id == c.id  # el dueño sí, sin importar mayúsculas


def test_no_se_cancela_una_cita_que_ya_paso(db):
    b = crear_base(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc))
    assert codigo_error(svc.cancelar, db, c.id, None, STAFF) == "cita_pasada"


# ======================= políticas =======================
def test_sin_politica_configurada_no_se_inventa_ningun_limite(db):
    b = crear_base(db)
    c = cita_cercana(db, b, horas=1)
    assert svc.cancelar(db, c.id, None, svc.AGENTE)[0].estado == EstadoCita.CANCELADA


def test_politica_de_cancelacion_se_aplica_y_usa_el_mensaje_de_la_institucion(db):
    b = crear_base(db)
    politica(db, TipoPolitica.CANCELACION, {"horas_minimas_cancelar": 24}, mensaje_paciente="Cancela con 24 horas de anticipación, por favor.")
    c = cita_cercana(db, b, horas=2)
    with pytest.raises(ErrorNegocio) as e:
        svc.cancelar(db, c.id, None, svc.AGENTE)
    assert e.value.codigo == "politica_incumplida" and "24 horas" in e.value.mensaje
    assert codigo_error(svc.cancelar, db, c.id, None, STAFF, forzar=True) == "politica_incumplida"  # el personal común no puede saltársela
    lejos = cita_cercana(db, b, horas=48, pac=b.pac2, prof=b.prof2, cons=b.cons2)
    assert svc.cancelar(db, lejos.id, None, svc.AGENTE)[0].estado == EstadoCita.CANCELADA


def test_coordinacion_puede_forzar_y_queda_registrado(db):
    b = crear_base(db)
    politica(db, TipoPolitica.CANCELACION, {"horas_minimas_cancelar": 24})
    c = cita_cercana(db, b, horas=2)
    assert codigo_error(svc.cancelar, db, c.id, None, COORD) == "politica_incumplida"  # sin pedirlo expresamente, no se fuerza
    svc.cancelar(db, c.id, "Excepción autorizada", COORD, forzar=True)
    detalle = db.scalars(select(CitaHistorial).where(CitaHistorial.accion == AccionCita.CANCELADA)).one().detalle
    assert detalle["forzado"] is True and detalle["horas_minimas"] == 24


def test_la_politica_mas_especifica_gana_y_las_inactivas_o_vencidas_no_cuentan(db):
    b = crear_base(db)
    general = politica(db, TipoPolitica.CANCELACION, {"horas_minimas_cancelar": 48})
    de_especialidad = politica(db, TipoPolitica.CANCELACION, {"horas_minimas_cancelar": 4}, especialidad_id=b.esp.id)
    from app.services import politica_service as ps
    assert ps.aplicable(db, TipoPolitica.CANCELACION, sede_id=b.sede.id, especialidad_id=b.esp.id).id == de_especialidad.id
    assert ps.aplicable(db, TipoPolitica.CANCELACION, sede_id=b.sede.id, especialidad_id=999999).id == general.id
    de_especialidad.activa = False
    general.vigente_hasta = date(2020, 12, 31)
    db.flush()
    assert ps.aplicable(db, TipoPolitica.CANCELACION, sede_id=b.sede.id, especialidad_id=b.esp.id) is None


def test_politica_de_reprogramacion_limita_cantidad_y_anticipacion(db):
    b, _ = escenario(db)
    politica(db, TipoPolitica.REPROGRAMACION, {"maximo_reprogramaciones": 1})
    c = agendar(db, b)
    svc.reprogramar(db, c.id, local(10), STAFF)
    assert codigo_error(svc.reprogramar, db, c.id, local(11), STAFF) == "politica_incumplida"
    assert svc.reprogramar(db, c.id, local(11), COORD, forzar=True).reprogramaciones == 2


# ======================= reprogramar =======================
def test_reprogramar_conserva_el_codigo_y_exige_volver_a_confirmar(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    svc.confirmar(db, c.id, STAFF)
    codigo = c.codigo_reserva
    r = svc.reprogramar(db, c.id, local(10, 20), STAFF)
    assert r.codigo_reserva == codigo and r.inicio == local(10, 20) and r.reprogramaciones == 1
    assert r.estado == EstadoCita.PROGRAMADA and r.confirmada_en is None
    h = db.scalars(select(CitaHistorial).where(CitaHistorial.accion == AccionCita.REPROGRAMADA)).one()
    assert h.inicio_anterior == local(9) and h.estado_anterior == EstadoCita.CONFIRMADA
    assert agenda_service.buscar_slot_libre(db, b.prof1.id, local(9), especialidad_id=b.esp.id) is not None  # el cupo viejo quedó libre


def test_reprogramar_a_un_cupo_que_se_traslapa_con_la_propia_cita(db):
    b, _ = escenario(db)
    serv = Servicio(especialidad_id=b.esp.id, nombre="Control largo", duracion_minutos=40)
    db.add(serv)
    db.flush()
    c = agendar(db, b, servicio_id=serv.id)  # 09:00-09:40
    r = svc.reprogramar(db, c.id, local(9, 20), STAFF)  # 09:20-10:00 se cruza con su propio horario anterior
    assert r.inicio == local(9, 20)


def test_reprogramar_a_cupo_ocupado_falla_y_no_cambia_nada(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    agendar(db, b, local(10), paciente=b.pac2)
    assert codigo_error(svc.reprogramar, db, c.id, local(10), STAFF) == "cupo_no_disponible"
    db.refresh(c)
    assert c.inicio == local(9) and c.reprogramaciones == 0


def test_reprogramar_con_otro_profesional(db):
    b, _ = escenario(db)
    horario(db, b, prof=b.prof2, cons="cons2")
    c = agendar(db, b)
    r = svc.reprogramar(db, c.id, local(9), STAFF, profesional_id=b.prof2.id)
    assert r.profesional_id == b.prof2.id and r.consultorio_id == b.cons2.id


# ======================= atención e inasistencias =======================
def test_marcar_atendida_solo_si_ya_paso_la_hora(db):
    b, _ = escenario(db)
    futura = agendar(db, b)
    assert codigo_error(svc.marcar_atendida, db, futura.id, STAFF) == "estado_no_permite"
    pasada = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc), pac=b.pac2, prof=b.prof2, cons=b.cons2)
    assert svc.marcar_atendida(db, pasada.id, STAFF).estado == EstadoCita.ATENDIDA


def test_inasistencia_pendiente_no_cuenta_hasta_que_se_verifica(db):
    b = crear_base(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc))
    reg = svc.registrar_inasistencia(db, c.id, "No llegó", STAFF)
    assert c.estado == EstadoCita.INASISTENCIA and reg.verificada is False and reg.verificada_en is None
    assert svc.contar_inasistencias_verificadas(db, b.pac1.id) == 0
    svc.verificar_inasistencia(db, reg.id, True, "Revisado con recepción", False, COORD)
    assert svc.contar_inasistencias_verificadas(db, b.pac1.id) == 1 and reg.consecuencia_aplicada is None
    assert codigo_error(svc.verificar_inasistencia, db, reg.id, True, None, False, COORD) == "ya_verificada"


def test_no_se_registra_inasistencia_de_una_cita_futura(db):
    b, _ = escenario(db)
    c = agendar(db, b)
    assert codigo_error(svc.registrar_inasistencia, db, c.id, None, STAFF) == "estado_no_permite"


def test_descartar_inasistencia_deja_la_cita_como_atendida(db):
    b = crear_base(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc))
    reg = svc.registrar_inasistencia(db, c.id, None, STAFF)
    svc.verificar_inasistencia(db, reg.id, False, "Sí asistió", False, COORD)
    assert c.estado == EstadoCita.ATENDIDA and reg.verificada is False
    assert svc.contar_inasistencias_verificadas(db, b.pac1.id) == 0


def test_la_consecuencia_solo_se_aplica_si_la_institucion_la_autorizo(db):
    b = crear_base(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc))
    reg = svc.registrar_inasistencia(db, c.id, None, STAFF)
    assert codigo_error(svc.verificar_inasistencia, db, reg.id, True, None, True, COORD) == "sin_consecuencia_autorizada"
    assert reg.verificada_en is None  # no quedó a medias
    politica(db, TipoPolitica.INASISTENCIA, {"inasistencias_para_aviso": 2}, consecuencia_autorizada="Texto de consecuencia definido por la institución")
    svc.verificar_inasistencia(db, reg.id, True, None, True, COORD)
    assert reg.consecuencia_aplicada == "Texto de consecuencia definido por la institución"


# ======================= lista de espera =======================
def test_lista_espera_candidatos_al_cancelar_y_asignacion(db):
    b, _ = escenario(db)
    esperando = nuevo_paciente(db)
    item = svc.agregar_lista_espera(db, STAFF, paciente_id=esperando.id, especialidad_id=b.esp.id, desde=LUNES - timedelta(days=1), hasta=LUNES + timedelta(days=3))
    assert codigo_error(svc.agregar_lista_espera, db, STAFF, paciente_id=esperando.id, especialidad_id=b.esp.id, desde=LUNES, hasta=LUNES) == "ya_en_lista_espera"
    c = agendar(db, b)
    _, candidatos = svc.cancelar(db, c.id, None, STAFF)
    assert candidatos == 1
    nueva = svc.asignar_desde_lista_espera(db, item.id, b.prof1.id, local(9), STAFF)
    assert nueva.paciente_id == esperando.id and item.estado == EstadoListaEspera.ASIGNADA and item.cita_id == nueva.id
    assert codigo_error(svc.asignar_desde_lista_espera, db, item.id, b.prof1.id, local(10), STAFF) == "no_encontrado"


def test_candidatos_respetan_rango_de_fechas_y_preferencias(db):
    b, _ = escenario(db)
    otro = nuevo_paciente(db)
    fuera_de_rango = svc.agregar_lista_espera(db, STAFF, paciente_id=otro.id, especialidad_id=b.esp.id, desde=LUNES + timedelta(days=10), hasta=LUNES + timedelta(days=20))
    c = agendar(db, b)
    assert svc.candidatos_lista_espera(db, c) == []
    svc.cancelar_lista_espera(db, fuera_de_rango.id, STAFF)
    assert fuera_de_rango.estado == EstadoListaEspera.CANCELADA


# ======================= API =======================
A = "/api/v1"


def con_rol(client, db, rol):
    crear_usuario(db, email=f"{rol.value.lower()}@ejemplo.com", rol=rol)
    return cabecera(client, f"{rol.value.lower()}@ejemplo.com")


def test_flujo_completo_por_api(client, db):
    b, _ = escenario(db)
    h = con_rol(client, db, Rol.RECEPCIONISTA)
    cuerpo = {"paciente_id": b.pac1.id, "profesional_id": b.prof1.id, "especialidad_id": b.esp.id, "inicio": local(9).isoformat(), "canal": "TELEFONO"}
    r = client.post(f"{A}/citas", headers=h, json=cuerpo)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["estado"] == "PROGRAMADA" and c["paciente"].startswith("Paciente") and c["consultorio"] == "C1"
    assert client.post(f"{A}/citas", headers=h, json={**cuerpo, "paciente_id": b.pac2.id}).json()["error"]["codigo"] == "cupo_no_disponible"
    assert client.post(f"{A}/citas/{c['id']}/confirmar", headers=h).json()["estado"] == "CONFIRMADA"
    r = client.post(f"{A}/citas/{c['id']}/reprogramar", headers=h, json={"inicio": local(10).isoformat()})
    assert r.status_code == 200 and r.json()["codigo_reserva"] == c["codigo_reserva"] and r.json()["reprogramaciones"] == 1
    r = client.post(f"{A}/citas/{c['id']}/cancelar", headers=h, json={"motivo": "Prueba"})
    assert r.json()["estado"] == "CANCELADA" and r.json()["lista_espera_candidatos"] == 0
    historial = client.get(f"{A}/citas/{c['id']}/historial", headers=h).json()
    assert [x["accion"] for x in historial] == ["CREADA", "CONFIRMADA", "REPROGRAMADA", "CANCELADA"]
    assert client.get(f"{A}/citas/codigo/{c['codigo_reserva'].lower()}", headers=h).json()["id"] == c["id"]


def test_fecha_sin_zona_horaria_se_rechaza(client, db):
    b, _ = escenario(db)
    h = con_rol(client, db, Rol.RECEPCIONISTA)
    r = client.post(f"{A}/citas", headers=h, json={"paciente_id": b.pac1.id, "profesional_id": b.prof1.id, "especialidad_id": b.esp.id, "inicio": "2030-03-04T09:00:00"})
    assert r.status_code == 422


def test_permisos_de_citas(client, db):
    b, _ = escenario(db)
    c = agendar(db, b)
    cuerpo = {"paciente_id": b.pac2.id, "profesional_id": b.prof1.id, "especialidad_id": b.esp.id, "inicio": local(10).isoformat()}
    consulta = con_rol(client, db, Rol.CONSULTA)
    profesional = con_rol(client, db, Rol.PROFESIONAL_SALUD)
    assert client.get(f"{A}/citas", headers=consulta).status_code == 200
    assert client.post(f"{A}/citas", headers=consulta, json=cuerpo).status_code == 403
    assert client.post(f"{A}/citas", headers=profesional, json=cuerpo).status_code == 403
    assert client.post(f"{A}/citas/{c.id}/cancelar", headers=profesional, json={}).status_code == 403
    assert client.get(f"{A}/citas").status_code == 401


def test_el_rol_consulta_no_ve_nombres_de_pacientes_en_las_citas(client, db):
    b, _ = escenario(db)
    agendar(db, b)
    lista = client.get(f"{A}/citas", headers=con_rol(client, db, Rol.CONSULTA)).json()
    assert len(lista) == 1 and lista[0]["paciente"] is None and lista[0]["paciente_id"] is None
    con_nombre = client.get(f"{A}/citas", headers=con_rol(client, db, Rol.RECEPCIONISTA)).json()
    assert con_nombre[0]["paciente"] is not None


def test_filtros_de_citas(client, db):
    b, _ = escenario(db)
    agendar(db, b)
    h = con_rol(client, db, Rol.RECEPCIONISTA)
    assert len(client.get(f"{A}/citas", headers=h, params={"desde": "2030-03-04", "hasta": "2030-03-04"}).json()) == 1
    assert client.get(f"{A}/citas", headers=h, params={"desde": "2030-03-05"}).json() == []
    assert client.get(f"{A}/citas", headers=h, params={"estado": "CANCELADA"}).json() == []


def test_solo_coordinacion_verifica_inasistencias(client, db):
    b = crear_base(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc))
    rec = con_rol(client, db, Rol.RECEPCIONISTA)
    coord = con_rol(client, db, Rol.COORDINADOR)
    assert client.post(f"{A}/citas/{c.id}/inasistencia", headers=rec, json={"observacion": "No llegó"}).json()["estado"] == "INASISTENCIA"
    assert client.get(f"{A}/inasistencias", headers=rec).status_code == 403
    pendientes = client.get(f"{A}/inasistencias", headers=coord, params={"pendientes": True}).json()
    assert len(pendientes) == 1 and pendientes[0]["pendiente"] is True
    assert client.post(f"{A}/inasistencias/{pendientes[0]['id']}/verificar", headers=rec, json={"confirmada": True}).status_code == 403
    r = client.post(f"{A}/inasistencias/{pendientes[0]['id']}/verificar", headers=coord, json={"confirmada": True})
    assert r.status_code == 200 and r.json()["verificada"] is True and r.json()["pendiente"] is False


def test_api_de_politicas(client, db):
    b = crear_base(db)
    coord = con_rol(client, db, Rol.COORDINADOR)
    rec = con_rol(client, db, Rol.RECEPCIONISTA)
    cuerpo = {"tipo": "CANCELACION", "titulo": "Cancelación de prueba", "parametros": {"horas_minimas_cancelar": 12}, "mensaje_paciente": "Texto de prueba"}
    assert client.post(f"{A}/politicas", headers=rec, json=cuerpo).status_code == 403
    r = client.post(f"{A}/politicas", headers=coord, json=cuerpo)
    assert r.status_code == 201 and r.json()["parametros"] == {"horas_minimas_cancelar": 12}
    malo = client.post(f"{A}/politicas", headers=coord, json={**cuerpo, "parametros": {"multa_en_pesos": 5000}})
    assert malo.status_code == 422 and malo.json()["error"]["codigo"] == "parametro_desconocido"
    assert client.post(f"{A}/politicas", headers=coord, json={**cuerpo, "parametros": {"horas_minimas_cancelar": -1}}).status_code == 422
    aplicable = client.get(f"{A}/politicas/aplicable", headers=rec, params={"tipo": "CANCELACION", "sede_id": b.sede.id}).json()
    assert aplicable["id"] == r.json()["id"]
    assert client.get(f"{A}/politicas/aplicable", headers=rec, params={"tipo": "INASISTENCIA"}).json() is None
    vacio = client.patch(f"{A}/politicas/{r.json()['id']}", headers=coord, json={"vigente_desde": None, "titulo": None, "mensaje_paciente": None})
    assert vacio.status_code == 200 and vacio.json()["titulo"] == "Cancelación de prueba" and vacio.json()["mensaje_paciente"] is None
    assert client.patch(f"{A}/politicas/{r.json()['id']}", headers=coord, json={"activa": False}).json()["activa"] is False
    assert client.get(f"{A}/politicas/aplicable", headers=rec, params={"tipo": "CANCELACION"}).json() is None


def test_api_de_lista_de_espera(client, db):
    b, _ = escenario(db)
    h = con_rol(client, db, Rol.RECEPCIONISTA)
    cuerpo = {"paciente_id": b.pac2.id, "especialidad_id": b.esp.id, "desde": "2030-03-01", "hasta": "2030-03-10"}
    r = client.post(f"{A}/lista-espera", headers=h, json=cuerpo)
    assert r.status_code == 201 and r.json()["estado"] == "ACTIVA"
    assert client.post(f"{A}/lista-espera", headers=h, json={**cuerpo, "hasta": "2030-02-01"}).status_code == 422
    asignada = client.post(f"{A}/lista-espera/{r.json()['id']}/asignar", headers=h, json={"profesional_id": b.prof1.id, "inicio": local(9).isoformat()})
    assert asignada.status_code == 201 and asignada.json()["estado"] == "PROGRAMADA"
    assert client.get(f"{A}/lista-espera", headers=h).json() == []
    assert db.get(ListaEspera, r.json()["id"]).estado == EstadoListaEspera.ASIGNADA

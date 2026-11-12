"""Fase 16: reportes (cifras correctas, N/D, sin datos personales), exportación a Excel/PDF (sin inyección) y consulta de auditoría."""
import io
from datetime import date, datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import select, text

from app.models import (AuditoriaLog, Cita, Conversacion, EjecucionHerramienta, Especialidad, ListaEspera, Mensaje, Notificacion, RegistroInasistencia, Solicitud)
from app.models.enums import (CanalConversacion, CanalNotificacion, EstadoCita, EstadoConversacion, EstadoListaEspera, EstadoNotificacion, ResultadoHerramienta, Rol,
                              RolMensaje, TipoNotificacion, TipoSolicitud)
from app.core.errores import ErrorNegocio
from app.services import auditoria, exportacion, reporte_service as rs
from tests.factories import LUNES_10, crear_base, nueva_cita, nuevo_paciente
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"
DIA = date(2030, 3, 4)  # LUNES_10 = 2030-03-04 10:00 UTC = 05:00 en Bogotá


def reporte(db, clave, desde=DIA, hasta=DIA):
    return rs.generar(db, clave, desde, hasta)


def indicadores(r):
    return dict(r.indicadores)


def tabla(r, titulo):
    return next(t for t in r.tablas if t.titulo.startswith(titulo))


def citas_de_ejemplo(db):
    """4 citas el mismo día: 2 atendidas, 1 inasistencia verificada, 1 cancelada. Una quinta, otro día, no debe contarse."""
    b = crear_base(db)
    p3, p4, p5 = nuevo_paciente(db), nuevo_paciente(db), nuevo_paciente(db)
    hora = lambda h: LUNES_10 + timedelta(minutes=20 * h)  # noqa: E731
    nueva_cita(db, b, inicio=hora(0), estado=EstadoCita.ATENDIDA, pac=b.pac1)
    nueva_cita(db, b, inicio=hora(1), estado=EstadoCita.ATENDIDA, pac=b.pac2)
    falta = nueva_cita(db, b, inicio=hora(2), estado=EstadoCita.INASISTENCIA, pac=p3)
    nueva_cita(db, b, inicio=hora(3), estado=EstadoCita.CANCELADA, pac=p4)
    nueva_cita(db, b, inicio=LUNES_10 + timedelta(days=3), estado=EstadoCita.ATENDIDA, pac=p5)
    db.add(RegistroInasistencia(cita_id=falta.id, paciente_id=p3.id, verificada=True, verificada_en=datetime.now(timezone.utc)))
    db.flush()
    return b


# ======================= rango =======================
def test_el_periodo_por_defecto_son_los_ultimos_30_dias():
    d, h, ini, fin = rs.rango(None, None)
    assert (h - d).days == 29 and fin - ini >= timedelta(days=30)


@pytest.mark.parametrize("desde, hasta, codigo", [(date(2030, 3, 5), date(2030, 3, 4), "rango_invalido"), (date(2029, 1, 1), date(2030, 3, 4), "rango_muy_largo")])
def test_periodos_invalidos(desde, hasta, codigo):
    with pytest.raises(ErrorNegocio) as e:
        rs.rango(desde, hasta)
    assert e.value.codigo == codigo and e.value.status == 422


def test_un_reporte_desconocido_se_rechaza(db):
    with pytest.raises(ErrorNegocio) as e:
        rs.generar(db, "inventado", None, None)
    assert e.value.status == 404


# ======================= citas =======================
def test_el_reporte_de_citas_cuenta_bien(db):
    citas_de_ejemplo(db)
    r = reporte(db, "citas")
    i = indicadores(r)
    assert i["Citas en el período"] == 4 and i["Atendida"] == 2 and i["Cancelada"] == 1 and i["Inasistencia"] == 1
    assert i["Tasa de cancelación"] == "25.0 %" and i["Inasistencias verificadas"] == 1 and i["Inasistencias por verificar"] == 0
    assert next(v for k, v in r.indicadores if k.startswith("Tasa de inasistencia verificada")) == "33.3 %"  # 1 de las 3 citas no canceladas cuya hora ya pasó
    esp, = tabla(r, "Por especialidad").filas
    assert esp[1:] == [4, 2, 1, 1]
    assert tabla(r, "Por día").filas == [["2030-03-04", 4]]
    assert tabla(r, "Por canal").filas == [["WEB", 4]]


def test_una_inasistencia_sin_verificar_no_cuenta_en_la_tasa(db):
    b = citas_de_ejemplo(db)
    db.execute(text("UPDATE inasistencias SET verificada = false, verificada_en = NULL"))
    r = reporte(db, "citas")
    i = indicadores(r)
    assert i["Inasistencias verificadas"] == 0 and i["Inasistencias por verificar"] == 1
    assert next(v for k, v in r.indicadores if k.startswith("Tasa de inasistencia verificada")) == "0.0 %"


def test_sin_datos_las_tasas_son_nd_y_no_cero(db):
    r = reporte(db, "citas", date(2031, 1, 1), date(2031, 1, 2))
    i = indicadores(r)
    assert i["Citas en el período"] == 0 and i["Tasa de cancelación"] == "N/D"
    assert next(v for k, v in r.indicadores if k.startswith("Tasa de inasistencia verificada")) == "N/D"
    assert all(t.filas == [] for t in r.tablas)


def test_el_limite_del_dia_usa_la_hora_de_la_institucion(db):
    b = crear_base(db)
    # 2030-03-05 03:00 UTC = 2030-03-04 22:00 en Bogotá: pertenece al día 4, no al 5
    nueva_cita(db, b, inicio=datetime(2030, 3, 5, 3, 0, tzinfo=timezone.utc))
    assert indicadores(reporte(db, "citas", date(2030, 3, 4), date(2030, 3, 4)))["Citas en el período"] == 1
    assert indicadores(reporte(db, "citas", date(2030, 3, 5), date(2030, 3, 5)))["Citas en el período"] == 0


def test_lista_de_espera_en_el_reporte_de_citas(db):
    b = crear_base(db)
    hoy = datetime.now(timezone.utc).date()
    db.add(ListaEspera(paciente_id=b.pac1.id, especialidad_id=b.esp.id, desde=hoy, hasta=hoy, estado=EstadoListaEspera.NOTIFICADA))
    db.flush()
    r = rs.generar(db, "citas", hoy - timedelta(days=1), hoy + timedelta(days=1))
    assert tabla(r, "Lista de espera").filas == [["NOTIFICADA", 1]]


# ======================= asistente =======================
def conversacion(db, canal=CanalConversacion.TEXTO, estado=EstadoConversacion.RESUELTA, prueba=False, verificada=False, fin=True):
    ahora = datetime.now(timezone.utc)
    c = Conversacion(canal=canal, estado=estado, es_prueba=prueba, identidad_verificada=verificada, iniciada_en=ahora - timedelta(minutes=10),
                     finalizada_en=ahora - timedelta(minutes=4) if fin else None)
    db.add(c)
    db.flush()
    return c


def mensaje(db, c, intencion, rol=RolMensaje.AGENTE):
    m = Mensaje(conversacion_id=c.id, rol=rol, contenido="texto secreto del paciente 1020304050", intencion=intencion)
    db.add(m)
    db.flush()
    return m


def hoy_rango():
    hoy = datetime.now(timezone.utc).date()
    return hoy - timedelta(days=1), hoy + timedelta(days=1)


def test_el_reporte_del_asistente_ignora_las_conversaciones_de_prueba_y_no_trae_contenido(db):
    c1 = conversacion(db, verificada=True)
    c2 = conversacion(db, canal=CanalConversacion.VOZ, estado=EstadoConversacion.TRANSFERIDA)
    c3 = conversacion(db, estado=EstadoConversacion.ABANDONADA, fin=False)
    prueba = conversacion(db, prueba=True)
    for c, intenciones in ((c1, ["saludo", "agendar_cita"]), (c2, ["saludo", "emergencia", "no_entendido"]), (c3, ["saludo"]), (prueba, ["saludo", "emergencia"])):
        for i in intenciones:
            mensaje(db, c, i)
    mensaje(db, c1, None, RolMensaje.PACIENTE)  # lo del paciente no se cuenta como respuesta
    db.add_all([EjecucionHerramienta(conversacion_id=c1.id, nombre="buscar_paciente", resultado=ResultadoHerramienta.EXITO, duracion_ms=100),
                EjecucionHerramienta(conversacion_id=c1.id, nombre="agendar_cita", resultado=ResultadoHerramienta.RECHAZADA, codigo_error="cupo_no_disponible", duracion_ms=300),
                EjecucionHerramienta(conversacion_id=prueba.id, nombre="agendar_cita", resultado=ResultadoHerramienta.ERROR, duracion_ms=5000)])
    db.flush()
    d, h = hoy_rango()
    r = rs.generar(db, "asistente", d, h)
    i = indicadores(r)
    assert i["Conversaciones (sin las de prueba)"] == 3 and i["Transferidas a una persona"] == 1 and i["% transferidas"] == "33.3 %"
    assert i["Con identidad verificada"] == 1 and i["Abandonadas"] == 1 and i["Posibles emergencias detectadas"] == 1 and i["Respuestas «no entendí el audio»"] == 1
    assert i["Duración media de las conversaciones terminadas"] == "6.0 min"
    assert dict(map(tuple, tabla(r, "Por canal").filas)) == {"TEXTO": 2, "VOZ": 1}
    assert dict(map(tuple, tabla(r, "Respuestas por tipo").filas))["saludo"] == 3
    herramientas = {f[0]: f[1:] for f in tabla(r, "Herramientas").filas}
    assert herramientas == {"agendar_cita": [1, 0, 1, 0, 300], "buscar_paciente": [1, 1, 0, 0, 100]}  # la ejecución de prueba no cuenta
    assert "secreto" not in str(r) and "1020304050" not in str(r)


def test_asistente_sin_conversaciones_da_nd(db):
    i = indicadores(reporte(db, "asistente", date(2031, 1, 1), date(2031, 1, 1)))
    assert i["Conversaciones (sin las de prueba)"] == 0 and i["% transferidas"] == "N/D" and i["Duración media de las conversaciones terminadas"] == "N/D"


# ======================= avisos y solicitudes =======================
def test_el_reporte_de_avisos(db):
    b = crear_base(db)
    ahora = datetime.now(timezone.utc)
    for n, (tipo, estado, motivo) in enumerate([(TipoNotificacion.CITA_AGENDADA, EstadoNotificacion.ENVIADA, None), (TipoNotificacion.CITA_AGENDADA, EstadoNotificacion.ENVIADA, None),
                                                (TipoNotificacion.RECORDATORIO, EstadoNotificacion.FALLIDA, "smtp_no_disponible"),
                                                (TipoNotificacion.RECORDATORIO, EstadoNotificacion.OMITIDA, "sin_email"), (TipoNotificacion.CUPO_DISPONIBLE, EstadoNotificacion.PENDIENTE, None)]):
        db.add(Notificacion(clave_unica=f"t:{n}", paciente_id=b.pac1.id, tipo=tipo, canal=CanalNotificacion.EMAIL, estado=estado, motivo=motivo, programada_para=ahora, cuerpo="privado"))
    db.flush()
    d, h = hoy_rango()
    r = rs.generar(db, "avisos", d, h)
    i = indicadores(r)
    assert (i["Avisos generados"], i["Enviados"], i["Fallidos"], i["Omitidos"], i["Pendientes"]) == (5, 2, 1, 1, 1) and i["% enviados sobre los que se intentaron enviar"] == "66.7 %"
    cruzado = {f[0]: f[1:] for f in tabla(r, "Por tipo").filas}
    assert cruzado["CITA_AGENDADA"] == [0, 2, 0, 0] and cruzado["RECORDATORIO"] == [0, 0, 1, 1]
    assert dict(map(tuple, tabla(r, "Motivos").filas)) == {"smtp_no_disponible": 1, "sin_email": 1} and "privado" not in str(r)


def test_el_reporte_de_solicitudes_no_trae_texto(db):
    ahora = datetime.now(timezone.utc)
    db.add_all([Solicitud(tipo=TipoSolicitud.QUEJA, asunto="Me atendieron mal, mi cédula 1020304050", descripcion="detalle privado"),
                Solicitud(tipo=TipoSolicitud.TRANSFERENCIA, asunto="a", descripcion="b", estado="CERRADA", cerrada_en=ahora + timedelta(minutes=30))])
    db.flush()
    d, h = hoy_rango()
    r = rs.generar(db, "solicitudes", d, h)
    i = indicadores(r)
    assert i["Solicitudes en el período"] == 2 and i["Todavía abiertas o en proceso"] == 1 and i["Tiempo medio hasta cerrarlas"].endswith("min")
    assert "1020304050" not in str(r) and "privado" not in str(r)


def test_ningun_reporte_trae_datos_de_pacientes(db):
    b = crear_base(db)
    b.pac1.nombres, b.pac1.email, b.pac1.telefono = "Zenobia", "zenobia@ejemplo.com", "3001234567"
    nueva_cita(db, b)
    db.flush()
    for clave in rs.CLAVES:
        d, h = (DIA, DIA) if clave == "citas" else hoy_rango()
        texto = str(rs.generar(db, clave, d, h))
        assert "Zenobia" not in texto and "zenobia@" not in texto and "3001234567" not in texto and "****" not in texto


# ======================= exportación =======================
def hostil(db):
    """Una especialidad con nombre malicioso: fórmula de Excel y marcas de ReportLab."""
    b = crear_base(db)
    b.esp.nombre = '=HYPERLINK("http://malo.example","clic")'
    otra = Especialidad(nombre="<b>negrita</b> <font color='red'>roja</font> & más")
    db.add(otra)
    db.flush()
    nueva_cita(db, b, inicio=LUNES_10)
    c = nueva_cita(db, b, inicio=LUNES_10 + timedelta(minutes=20), pac=b.pac2)
    c.especialidad_id = otra.id
    db.flush()
    return b


def test_excel_tiene_hojas_cifras_reales_y_no_ejecuta_formulas(db):
    hostil(db)
    r = reporte(db, "citas")
    libro = load_workbook(io.BytesIO(exportacion.reporte_a_excel(r)))
    assert libro.sheetnames[0] == "Resumen" and "Por especialidad" in libro.sheetnames and "Por día" in libro.sheetnames
    resumen = {fila[0].value: fila[1].value for fila in libro["Resumen"].iter_rows(min_row=6) if fila[1].value is not None}
    assert resumen["Citas en el período"] == 2 and isinstance(resumen["Citas en el período"], int)
    celdas = [c for fila in libro["Por especialidad"].iter_rows(min_row=2) for c in fila]
    assert not any(c.data_type == "f" for c in libro["Por especialidad"]._cells.values())  # ninguna celda es fórmula
    assert any(str(c.value).startswith("'=HYPERLINK") for c in celdas)
    assert libro["Por especialidad"]["A1"].font.bold and libro["Por especialidad"].freeze_panes == "A2"


def test_seguro_excel_neutraliza_todo_lo_peligroso():
    for peligroso in ("=1+1", "+cmd", "-2+3", "@SUM(A1)", "\t=x", "\r=x"):
        assert exportacion.seguro_excel(peligroso).startswith("'")
    assert exportacion.seguro_excel("normal") == "normal" and exportacion.seguro_excel(5) == 5 and exportacion.seguro_excel("a\x00b") == "ab"


def test_pdf_es_valido_trae_el_texto_y_no_interpreta_marcas(db):
    hostil(db)
    datos = exportacion.reporte_a_pdf(reporte(db, "citas"))
    assert datos.startswith(b"%PDF")
    lector = PdfReader(io.BytesIO(datos))
    texto = "\n".join(p.extract_text() for p in lector.pages)
    plano = "".join(texto.split())  # las celdas largas se parten en varias líneas: se compara sin espacios
    assert "Citas" in texto and "Período: 2030-03-04 a 2030-03-04" in texto and "Citas en el período" in texto and "Página 1" in texto
    assert "".join("<b>negrita</b>".split()) in plano and "".join("<font color='red'>roja</font> & más".split()) in plano  # las marcas se muestran como texto, no se aplican


def test_pdf_de_un_reporte_vacio_y_sin_nombres_de_hoja_repetidos(db):
    r = reporte(db, "avisos", date(2031, 1, 1), date(2031, 1, 1))
    assert PdfReader(io.BytesIO(exportacion.reporte_a_pdf(r))).pages
    libro = load_workbook(io.BytesIO(exportacion.reporte_a_excel(r)))
    assert len(set(libro.sheetnames)) == len(libro.sheetnames)


# ======================= API de reportes =======================
def test_api_reportes_permisos_y_descargas(client, db):
    citas_de_ejemplo(db)
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    db.commit()
    assert client.get(f"{A}/reportes").status_code == 401
    assert client.get(f"{A}/reportes", headers=cabecera(client, "rec@ejemplo.com")).status_code == 403  # recepción no ve reportes
    h = cabecera(client, "consulta@ejemplo.com")
    assert [r["clave"] for r in client.get(f"{A}/reportes", headers=h).json()] == list(rs.CLAVES)
    j = client.get(f"{A}/reportes/citas", headers=h, params={"desde": "2030-03-04", "hasta": "2030-03-04"}).json()
    assert dict(map(tuple, j["indicadores"]))["Citas en el período"] == 4
    x = client.get(f"{A}/reportes/citas/exportar", headers=h, params={"desde": "2030-03-04", "hasta": "2030-03-04"})
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxmlformats") and x.content[:2] == b"PK"
    assert x.headers["content-disposition"] == 'attachment; filename="reporte-citas-2030-03-04-2030-03-04.xlsx"' and x.headers["cache-control"] == "no-store"
    p = client.get(f"{A}/reportes/avisos/exportar", headers=h, params={"formato": "pdf"})
    assert p.headers["content-type"] == "application/pdf" and p.content.startswith(b"%PDF")


def test_api_reportes_errores(client, db):
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    h = cabecera(client, "consulta@ejemplo.com")
    assert client.get(f"{A}/reportes/inventado", headers=h).status_code == 404
    assert client.get(f"{A}/reportes/citas", headers=h, params={"desde": "2030-03-05", "hasta": "2030-03-04"}).json()["error"]["codigo"] == "rango_invalido"
    assert client.get(f"{A}/reportes/citas", headers=h, params={"desde": "no-es-fecha"}).status_code == 422
    assert client.get(f"{A}/reportes/citas/exportar", headers=h, params={"formato": "docx"}).status_code == 422


def test_la_exportacion_queda_auditada_sin_contenido(client, db):
    citas_de_ejemplo(db)
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    db.commit()
    client.get(f"{A}/reportes/citas/exportar", headers=cabecera(client, "consulta@ejemplo.com"), params={"desde": "2030-03-04", "hasta": "2030-03-04", "formato": "pdf"})
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "reporte_exportado")).one()
    assert a.detalle == {"reporte": "citas", "formato": "pdf", "desde": "2030-03-04", "hasta": "2030-03-04"} and a.usuario_id is not None


# ======================= auditoría =======================
def sembrar_auditoria(db):
    admin = crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    otro = crear_usuario(db, email="otro@ejemplo.com", rol=Rol.COORDINADOR)
    auditoria.registrar(db, accion="cita_creada", usuario_id=otro.id, entidad="cita", entidad_id=7)
    auditoria.registrar(db, accion="cita_cancelada", usuario_id=otro.id, entidad="cita", entidad_id=7, detalle={"motivo": "llamar a ana@ejemplo.com o al 3001234567"})
    auditoria.registrar(db, accion="acceso_denegado", usuario_id=otro.id, exito=False, detalle={"permiso": "auditoria:ver"})
    auditoria.registrar(db, accion="notificacion_enviada", actor="sistema", entidad="notificacion", entidad_id=3)
    auditoria.registrar(db, accion="descuento_100%_prueba", actor="sistema")
    db.commit()
    return admin, otro


def test_solo_el_administrador_ve_la_auditoria(client, db):
    sembrar_auditoria(db)
    assert client.get(f"{A}/auditoria").status_code == 401
    assert client.get(f"{A}/auditoria", headers=cabecera(client, "otro@ejemplo.com")).status_code == 403  # ni el coordinador
    assert client.get(f"{A}/auditoria/exportar", headers=cabecera(client, "otro@ejemplo.com")).status_code == 403
    assert client.get(f"{A}/auditoria", headers=cabecera(client, "admin@ejemplo.com")).status_code == 200


def test_listado_filtros_y_paginacion(client, db):
    admin, otro = sembrar_auditoria(db)
    h = cabecera(client, "admin@ejemplo.com")
    todo = client.get(f"{A}/auditoria", headers=h).json()
    assert todo["total"] >= 6 and todo["registros"][0]["id"] > todo["registros"][1]["id"]  # lo más reciente primero
    por_accion = client.get(f"{A}/auditoria", headers=h, params={"accion": "cita_", "usuario_id": otro.id}).json()
    assert {r["accion"] for r in por_accion["registros"]} == {"cita_creada", "cita_cancelada"} and por_accion["registros"][0]["usuario"] == "Persona Ficticia"
    assert [r["accion"] for r in client.get(f"{A}/auditoria", headers=h, params={"exito": "false", "usuario_id": otro.id}).json()["registros"]] == ["acceso_denegado"]
    assert {r["accion"] for r in client.get(f"{A}/auditoria", headers=h, params={"usuario_id": otro.id}).json()["registros"]} == {"cita_creada", "cita_cancelada", "acceso_denegado"}
    assert [r["accion"] for r in client.get(f"{A}/auditoria", headers=h, params={"actor": "sistema", "entidad": "notificacion", "accion": "notificacion_enviada"}).json()["registros"]][:1] == ["notificacion_enviada"]
    pag = client.get(f"{A}/auditoria", headers=h, params={"limite": 2, "pagina": 2, "accion": "cita_", "usuario_id": otro.id}).json()
    assert pag["total"] == 2 and pag["registros"] == []
    hoy = date.today().isoformat()
    assert client.get(f"{A}/auditoria", headers=h, params={"desde": hoy, "hasta": hoy}).json()["total"] >= 6
    assert client.get(f"{A}/auditoria", headers=h, params={"desde": "2000-01-01", "hasta": "2000-01-02"}).json()["total"] == 0


def test_los_comodines_del_filtro_no_son_comodines(client, db):
    sembrar_auditoria(db)
    h = cabecera(client, "admin@ejemplo.com")
    assert [r["accion"] for r in client.get(f"{A}/auditoria", headers=h, params={"accion": "100%"}).json()["registros"]] == ["descuento_100%_prueba"]
    assert client.get(f"{A}/auditoria", headers=h, params={"accion": "%"}).json()["total"] == 1  # un % solo encuentra acciones que lo contienen
    assert client.get(f"{A}/auditoria", headers=h, params={"accion": "_"}).json()["total"] > 0  # «_» existe en casi todas, pero literal
    assert client.get(f"{A}/auditoria", headers=h, params={"accion": "x" * 81}).status_code == 422
    assert client.get(f"{A}/auditoria", headers=h, params={"limite": 500}).status_code == 422


def test_el_detalle_ya_viene_sin_datos_personales(client, db):
    sembrar_auditoria(db)
    h = cabecera(client, "admin@ejemplo.com")
    cancelada = client.get(f"{A}/auditoria", headers=h, params={"accion": "cita_cancelada"}).json()["registros"][0]
    assert "ana@ejemplo.com" not in str(cancelada["detalle"]) and "3001234567" not in str(cancelada["detalle"])


def test_acciones_para_el_filtro(client, db):
    sembrar_auditoria(db)
    acciones = client.get(f"{A}/auditoria/acciones", headers=cabecera(client, "admin@ejemplo.com")).json()
    assert acciones == sorted(acciones) and "cita_creada" in acciones and len(set(acciones)) == len(acciones)


def test_exportar_auditoria_a_excel_y_queda_registrado(client, db):
    _, otro = sembrar_auditoria(db)  # otras pruebas dejan filas confirmadas en la misma base: se filtra por el usuario de esta
    h = cabecera(client, "admin@ejemplo.com")
    r = client.get(f"{A}/auditoria/exportar", headers=h, params={"accion": "cita_", "usuario_id": otro.id})
    assert r.status_code == 200 and r.content[:2] == b"PK" and r.headers["content-disposition"] == 'attachment; filename="auditoria.xlsx"'
    filas = [[c.value for c in fila] for fila in load_workbook(io.BytesIO(r.content)).active.iter_rows()]
    assert "Bitácora de auditoría" in filas[0][0] and filas[1][:5] == ["Id", "Fecha", "Usuario", "Actor", "Acción"]
    assert {f[4] for f in filas[2:]} == {"cita_creada", "cita_cancelada"} and all(f[2] == "Persona Ficticia" for f in filas[2:])
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "auditoria_exportada")).one()
    assert a.detalle == {"filas": 2, "truncado": False}
    assert client.get(f"{A}/auditoria/exportar", headers=h, params={"accion": "no_existe_esta_accion"}).status_code == 404


def test_la_exportacion_de_auditoria_se_acota(client, db, monkeypatch):
    sembrar_auditoria(db)
    monkeypatch.setattr("app.api.v1.auditoria.MAX_EXPORTAR", 2)
    monkeypatch.setattr(exportacion, "MAX_FILAS_EXCEL", 2)
    r = client.get(f"{A}/auditoria/exportar", headers=cabecera(client, "admin@ejemplo.com"))
    filas = [[c.value for c in fila] for fila in load_workbook(io.BytesIO(r.content)).active.iter_rows()]
    assert len(filas) == 5 and "solo los 2 registros" in str(filas[1])  # 2 notas + encabezado + 2 filas
    assert db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "auditoria_exportada")).one().detalle["truncado"] is True


def test_la_bitacora_sigue_siendo_de_solo_escritura(db):
    auditoria.registrar(db, accion="prueba_inmutable")
    with pytest.raises(Exception, match="solo escritura|append|no se permite|inmutable|permitido"):
        db.execute(text("UPDATE auditoria_log SET accion = 'x' WHERE accion = 'prueba_inmutable'"))
    db.rollback()

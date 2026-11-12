"""Fase 15: avisos a pacientes por correo (cola, reintentos, recordatorios, lista de espera, confidencialidad). Sin enviar nada real:
el correo se prueba con un emisor falso y con un servidor SMTP local de mentira que habla el protocolo de verdad."""
import socketserver
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditoriaLog, Cita, ListaEspera, Notificacion, Paciente
from app.models.enums import EstadoCita, EstadoListaEspera, EstadoNotificacion as E, Rol, TipoNotificacion as T
from app.notificaciones import emisores, plantillas
from app.notificaciones.emisores import EmisorCorreo, ErrorDeEnvio
from app.services import cita_service as svc
from app.services import notificacion_service as ns
from tests.factories import crear_base, nueva_cita, nuevo_paciente
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_citas import STAFF, agendar, escenario
from tests.test_agenda import LUNES, local

A = "/api/v1"
BOG = ZoneInfo("America/Bogota")


class EmisorFalso:
    nombre = "falso"

    def __init__(self, configurado=True):
        self._configurado, self.enviados, self.falla = configurado, [], None

    def configurado(self):
        return self._configurado

    def enviar(self, destino, asunto, cuerpo):
        if self.falla:
            raise self.falla
        self.enviados.append((destino, asunto, cuerpo))


@pytest.fixture
def correo(monkeypatch):
    e = EmisorFalso()
    monkeypatch.setattr(emisores, "emisor", lambda canal: e)
    return e


def con_email(db, email="ana@ejemplo.com", **extra):
    return nuevo_paciente(db, email=email, **extra)


def escenario_con_correo(db):
    b, h = escenario(db)
    b.pac1.email = "ana@ejemplo.com"
    b.pac2.email = "beto@ejemplo.com"
    db.flush()
    return b


def todas(db):
    db.expire_all()
    return list(db.scalars(select(Notificacion).order_by(Notificacion.id)))


def del_tipo(db, tipo):
    return [n for n in todas(db) if n.tipo == tipo]


def proxima_hora_local(hora: int) -> datetime:
    """El próximo instante (UTC) en que en Bogotá son las ``hora`` en punto."""
    ahora = datetime.now(BOG)
    c = ahora.replace(hour=hora, minute=0, second=0, microsecond=0)
    return (c if c > ahora else c + timedelta(days=1)).astimezone(timezone.utc)


# ======================= texto =======================
DATOS = plantillas.Datos(nombre="Ana", institucion="Institución de Prueba", cuando="lunes 4 de marzo a las 10:00 a. m.", sede="Sede Norte", codigo="K7M2QXD9")


@pytest.mark.parametrize("tipo", list(T))
def test_los_textos_no_revelan_datos_clinicos(tipo):
    asunto, cuerpo = plantillas.render(tipo, DATOS)
    todo = f"{asunto}\n{cuerpo}".lower()
    assert "lunes 4 de marzo a las 10:00 a. m." in todo and "institución de prueba" in todo and cuerpo.startswith("Hola, Ana.")
    for prohibido in ("especialidad", "doctor", "dra.", "diagn", "motivo", "pediatr", "medicina"):
        assert prohibido not in todo


def test_el_aviso_de_cupo_aclara_que_no_reserva_y_no_trae_codigo_ajeno():
    _, cuerpo = plantillas.render(T.CUPO_DISPONIBLE, plantillas.Datos(nombre="Beto", institucion="Inst", cuando="lunes", sede="S", codigo=""))
    assert "NO reserva" in cuerpo and "Código" not in cuerpo
    assert "K7M2QXD9" in plantillas.render(T.CITA_AGENDADA, DATOS)[1] and "K7M2QXD9" in plantillas.render(T.RECORDATORIO, DATOS)[1]


# ======================= encolar =======================
def test_al_agendar_se_encola_el_aviso_con_el_destino_enmascarado(db, correo):
    b = escenario_con_correo(db)
    c = agendar(db, b)
    n, = todas(db)
    assert (n.tipo, n.estado, n.paciente_id, n.cita_id) == (T.CITA_AGENDADA, E.PENDIENTE, b.pac1.id, c.id)
    assert n.destino_enmascarado == "a***@ejemplo.com" and "ana@ejemplo.com" not in n.cuerpo + n.asunto
    assert c.codigo_reserva in n.cuerpo and "lunes 4 de marzo a las 9:00 a. m." in n.cuerpo and b.sede.nombre in n.cuerpo
    assert b.esp.nombre not in n.cuerpo and "Ficticia" not in n.cuerpo  # sin especialidad ni profesional


@pytest.mark.parametrize("ajuste, motivo", [({"email": None}, "sin_email"), ({"notificaciones_activas": False}, "paciente_no_acepta")])
def test_si_no_se_puede_avisar_queda_omitida_con_su_motivo_y_sin_texto(db, correo, ajuste, motivo):
    b = escenario_con_correo(db)
    for campo, valor in ajuste.items():
        setattr(b.pac1, campo, valor)
    db.flush()
    agendar(db, b)
    n, = todas(db)
    assert n.estado == E.OMITIDA and n.motivo == motivo and n.cuerpo == "" and n.destino_enmascarado is None
    assert ns.procesar_pendientes(db) == {}  # nada que enviar
    assert correo.enviados == []


def test_sin_correo_configurado_se_registra_como_omitida_y_no_como_enviada(db, monkeypatch):
    monkeypatch.setattr(emisores, "emisor", lambda canal: EmisorFalso(configurado=False))
    agendar(db, escenario_con_correo(db))
    n, = todas(db)
    assert n.estado == E.OMITIDA and n.motivo == "canal_no_configurado"


def test_con_los_avisos_apagados_no_se_registra_nada(db, correo, monkeypatch):
    monkeypatch.setattr(get_settings(), "notificaciones_activas", False)
    agendar(db, escenario_con_correo(db))
    assert todas(db) == []


def test_el_mismo_aviso_no_se_encola_dos_veces(db, correo):
    b = escenario_con_correo(db)
    c = agendar(db, b)
    otra = ns.encolar(db, paciente=b.pac1, tipo=T.CITA_AGENDADA, cita=c, clave=f"cita_agendada:{c.id}")
    assert len(todas(db)) == 1 and otra.id == todas(db)[0].id


def test_un_fallo_al_encolar_no_deshace_la_cita(db, correo, monkeypatch):
    def roto(*a, **k):
        raise RuntimeError("falla de prueba")

    monkeypatch.setattr(ns, "encolar", roto)
    b = escenario_con_correo(db)
    c = agendar(db, b)
    assert db.get(Cita, c.id).estado == EstadoCita.PROGRAMADA and todas(db) == []


# ======================= enviar =======================
def test_se_envia_al_correo_real_del_paciente_y_se_audita_sin_datos(db, correo):
    b = escenario_con_correo(db)
    agendar(db, b)
    assert ns.procesar_pendientes(db) == {"enviada": 1}
    (destino, asunto, cuerpo), = correo.enviados
    n, = todas(db)
    assert destino == "ana@ejemplo.com" and asunto == "Su cita quedó agendada" and cuerpo == n.cuerpo
    assert n.estado == E.ENVIADA and n.enviada_en is not None and n.intentos == 1
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "notificacion_enviada")).one()
    assert a.actor == "sistema" and "ana@ejemplo.com" not in str(a.detalle) and "Hola" not in str(a.detalle)
    assert ns.procesar_pendientes(db) == {}  # no se reenvía


def test_un_fallo_temporal_se_reintenta_con_espera_y_al_agotarse_queda_fallida(db, correo, monkeypatch):
    monkeypatch.setattr(get_settings(), "notificaciones_max_intentos", 3)
    agendar(db, escenario_con_correo(db))
    correo.falla = ErrorDeEnvio("smtp_no_disponible")
    t0 = datetime.now(timezone.utc)
    assert ns.procesar_pendientes(db, t0) == {"reintento": 1}
    n, = todas(db)
    assert n.estado == E.PENDIENTE and n.intentos == 1 and n.motivo == "smtp_no_disponible" and n.programada_para == t0 + timedelta(minutes=5)
    assert ns.procesar_pendientes(db, t0 + timedelta(minutes=1)) == {}  # todavía no toca
    assert ns.procesar_pendientes(db, t0 + timedelta(minutes=6)) == {"reintento": 1}
    assert todas(db)[0].programada_para == t0 + timedelta(minutes=6 + 10)  # la espera crece con cada intento
    assert ns.procesar_pendientes(db, t0 + timedelta(minutes=30)) == {"fallida": 1}
    n = todas(db)[0]
    assert n.estado == E.FALLIDA and n.intentos == 3 and ns.procesar_pendientes(db, t0 + timedelta(days=1)) == {}


def test_un_rechazo_definitivo_no_se_reintenta_y_una_persona_puede_reencolarlo(db, correo):
    agendar(db, escenario_con_correo(db))
    correo.falla = ErrorDeEnvio("smtp_destino_rechazado", reintentable=False)
    assert ns.procesar_pendientes(db) == {"fallida": 1}
    n = todas(db)[0]
    assert n.estado == E.FALLIDA and n.intentos == 1
    correo.falla = None
    ns.reintentar(db, n.id)
    assert ns.procesar_pendientes(db) == {"enviada": 1}


def test_si_el_paciente_apaga_los_avisos_antes_del_envio_no_se_envia(db, correo):
    b = escenario_con_correo(db)
    agendar(db, b)
    b.pac1.notificaciones_activas = False
    db.flush()
    assert ns.procesar_pendientes(db) == {"omitida": 1} and correo.enviados == []
    n = todas(db)[0]
    assert n.motivo == "paciente_no_acepta" and n.cuerpo == ""


def test_si_la_cita_cambia_antes_del_envio_el_aviso_viejo_no_sale_y_el_nuevo_si(db, correo):
    b = escenario_con_correo(db)
    c = agendar(db, b)
    svc.reprogramar(db, c.id, local(10), STAFF)
    assert ns.procesar_pendientes(db) == {"omitida": 1, "enviada": 1}
    por_tipo = {n.tipo: n for n in todas(db)}
    assert por_tipo[T.CITA_AGENDADA].motivo == "cita_cambio" and por_tipo[T.CITA_REPROGRAMADA].estado == E.ENVIADA
    assert "10:00 a. m." in correo.enviados[0][2] and "9:00" not in correo.enviados[0][2]


def test_cancelar_avisa_y_el_aviso_de_agendada_pendiente_ya_no_sale(db, correo):
    b = escenario_con_correo(db)
    c = agendar(db, b)
    svc.cancelar(db, c.id, "prueba", STAFF)
    assert ns.procesar_pendientes(db) == {"omitida": 1, "enviada": 1}
    assert [e[1] for e in correo.enviados] == ["Su cita fue cancelada"]


def test_dos_reprogramaciones_distintas_generan_dos_avisos(db, correo):
    b = escenario_con_correo(db)
    c = agendar(db, b)
    svc.reprogramar(db, c.id, local(10), STAFF)
    svc.reprogramar(db, c.id, local(11), STAFF)
    assert len(del_tipo(db, T.CITA_REPROGRAMADA)) == 2


# ======================= lista de espera =======================
def espera(db, b, paciente):
    return svc.agregar_lista_espera(db, STAFF, paciente_id=paciente.id, especialidad_id=b.esp.id, desde=LUNES - timedelta(days=1), hasta=LUNES + timedelta(days=3))


def test_al_liberar_un_cupo_se_avisa_por_orden_de_llegada_hasta_el_maximo(db, correo, monkeypatch):
    monkeypatch.setattr(get_settings(), "espera_notificar_maximo", 2)
    b = escenario_con_correo(db)
    sin_correo = nuevo_paciente(db)  # sin email: no se le puede avisar, sigue en espera
    p3, p4 = con_email(db, "c@ejemplo.com"), con_email(db, "d@ejemplo.com")
    items = [espera(db, b, p) for p in (sin_correo, b.pac2, p3, p4)]
    c = agendar(db, b)
    _, candidatos = svc.cancelar(db, c.id, "prueba", STAFF)
    assert candidatos == 4
    avisos = del_tipo(db, T.CUPO_DISPONIBLE)
    assert [n.paciente_id for n in avisos if n.estado == E.PENDIENTE] == [b.pac2.id, p3.id]  # el 1.º no tiene correo y no cuenta; p4 queda fuera del máximo
    db.expire_all()
    assert [i.estado for i in items] == [EstadoListaEspera.ACTIVA, EstadoListaEspera.NOTIFICADA, EstadoListaEspera.NOTIFICADA, EstadoListaEspera.ACTIVA]
    assert [n.motivo for n in avisos if n.estado == E.OMITIDA] == ["sin_email"]


def test_el_aviso_de_cupo_no_trae_el_codigo_de_otra_persona_ni_se_enlaza_a_su_cita(db, correo):
    b = escenario_con_correo(db)
    espera(db, b, b.pac2)
    c = agendar(db, b)
    svc.cancelar(db, c.id, "prueba", STAFF)
    n, = del_tipo(db, T.CUPO_DISPONIBLE)
    assert c.codigo_reserva not in n.cuerpo and n.cita_id is None and "NO reserva" in n.cuerpo and n.lista_espera_id is not None


def test_de_madrugada_se_aplaza_y_de_dia_se_envia(db, correo):
    b = escenario_con_correo(db)
    espera(db, b, b.pac2)
    c = agendar(db, b)
    svc.cancelar(db, c.id, "prueba", STAFF)
    madrugada = proxima_hora_local(3)
    assert ns.procesar_pendientes(db, madrugada) == {"omitida": 1, "enviada": 1, "aplazada": 1}  # la agendada vieja se omite, la cancelación sale (es inmediata) y el cupo espera
    assert [e[1] for e in correo.enviados] == ["Su cita fue cancelada"]
    assert del_tipo(db, T.CUPO_DISPONIBLE)[0].estado == E.PENDIENTE
    mediodia = proxima_hora_local(12)
    assert ns.procesar_pendientes(db, mediodia).get("enviada") == 1
    assert del_tipo(db, T.CUPO_DISPONIBLE)[0].estado == E.ENVIADA


def test_si_la_persona_sale_de_la_lista_antes_del_envio_no_se_le_avisa(db, correo):
    b = escenario_con_correo(db)
    item = espera(db, b, b.pac2)
    svc.cancelar(db, agendar(db, b).id, "prueba", STAFF)
    svc.cancelar_lista_espera(db, item.id, STAFF)
    ns.procesar_pendientes(db, proxima_hora_local(12))
    assert del_tipo(db, T.CUPO_DISPONIBLE)[0].motivo == "espera_cambio" and all("cupo" not in e[1] for e in correo.enviados)


# ======================= recordatorios =======================
def cita_en(db, b, horas, **kw):
    return nueva_cita(db, b, inicio=(datetime.now(timezone.utc) + timedelta(hours=horas)).replace(second=0, microsecond=0), **kw)


def test_el_recordatorio_se_genera_una_sola_vez_y_solo_dentro_de_la_ventana(db, correo):
    b = escenario_con_correo(db)
    dentro = cita_en(db, b, 20)
    cita_en(db, b, 1, prof=b.prof2, cons=b.cons2, pac=b.pac2)  # a menos de 2 h: tarde para recordar
    cita_en(db, b, 30, prof=b.prof2, cons=b.cons2, pac=b.pac1)  # a más de 24 h: todavía no toca
    cancelada = cita_en(db, b, 10, prof=b.prof2, cons=b.cons2, pac=b.pac2)
    cancelada.estado = EstadoCita.CANCELADA
    db.flush()
    assert ns.generar_recordatorios(db) == 1 and ns.generar_recordatorios(db) == 0
    n, = del_tipo(db, T.RECORDATORIO)
    assert n.cita_id == dentro.id and n.estado == E.PENDIENTE and "Le recordamos" in n.cuerpo


def test_el_recordatorio_se_envia_y_si_la_cita_se_reprograma_se_omite_y_se_genera_otro(db, correo):
    b = escenario_con_correo(db)
    c = cita_en(db, b, 20)
    ns.generar_recordatorios(db)
    c.inicio, c.fin = c.inicio + timedelta(hours=1), c.fin + timedelta(hours=1)
    db.flush()
    assert ns.procesar_pendientes(db, proxima_hora_local(12)).get("omitida") == 1 and del_tipo(db, T.RECORDATORIO)[0].motivo == "cita_cambio"
    assert ns.generar_recordatorios(db) == 1
    assert len(del_tipo(db, T.RECORDATORIO)) == 2


def test_el_recordatorio_no_sale_de_madrugada(db, correo):
    b = escenario_con_correo(db)
    cita_en(db, b, 20)
    ns.generar_recordatorios(db)
    madrugada = proxima_hora_local(3)
    assert ns.procesar_pendientes(db, madrugada) == {"aplazada": 1} and correo.enviados == []


def test_recordatorio_enviado_de_dia(db, correo):
    b = escenario_con_correo(db)
    cita_en(db, b, 20)
    ns.generar_recordatorios(db)
    manana_mediodia = proxima_hora_local(12)
    # la cita debe seguir en el futuro respecto del instante simulado
    c = db.scalars(select(Cita)).first()
    c.inicio = manana_mediodia + timedelta(hours=5)
    c.fin = c.inicio + timedelta(minutes=20)
    n = del_tipo(db, T.RECORDATORIO)[0]
    n.contexto = {"cita_inicio": ns._iso(c.inicio)}
    db.flush()
    assert ns.procesar_pendientes(db, manana_mediodia) == {"enviada": 1} and correo.enviados[0][1] == "Recordatorio de su cita"


# ======================= servidor SMTP de verdad (local) =======================
class SmtpDeMentira(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, auth=None, rechazar_destino=False):
        self.recibidos, self.auth, self.rechazar_destino = [], auth, rechazar_destino
        super().__init__(("127.0.0.1", 0), _ManejadorSmtp)
        self.hilo = threading.Thread(target=self.serve_forever, daemon=True)
        self.hilo.start()

    @property
    def puerto(self):
        return self.server_address[1]

    def cerrar(self):
        self.shutdown()
        self.server_close()


class _ManejadorSmtp(socketserver.StreamRequestHandler):
    def responder(self, texto):
        self.wfile.write((texto + "\r\n").encode())

    def handle(self):
        srv, datos, de, para = self.server, [], None, []
        self.responder("220 smtp.prueba ESMTP")
        while True:
            linea = self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")
            if not linea:
                return
            orden = linea.upper()
            if orden.startswith("EHLO"):
                self.wfile.write(b"250-smtp.prueba\r\n" + (b"250-AUTH PLAIN LOGIN\r\n" if srv.auth else b"") + b"250 8BITMIME\r\n")
            elif orden.startswith("AUTH"):
                self.responder("235 ok" if srv.auth == "bien" else "535 5.7.8 credenciales incorrectas")
            elif orden.startswith("MAIL FROM"):
                de = linea
                self.responder("250 ok")
            elif orden.startswith("RCPT TO"):
                if srv.rechazar_destino:
                    self.responder("550 no existe ese buzón")
                else:
                    para.append(linea)
                    self.responder("250 ok")
            elif orden == "DATA":
                self.responder("354 adelante")
                cuerpo = []
                while True:
                    l = self.rfile.readline().decode("utf-8", "replace")
                    if l.rstrip("\r\n") == ".":
                        break
                    cuerpo.append(l)
                srv.recibidos.append({"de": de, "para": para, "datos": "".join(cuerpo)})
                self.responder("250 recibido")
            elif orden == "QUIT":
                self.responder("221 adiós")
                return
            else:
                self.responder("250 ok")


@pytest.fixture
def smtp(monkeypatch):
    servidores = []

    def levantar(**kw):
        s = SmtpDeMentira(**kw)
        servidores.append(s)
        ajustes = get_settings()
        for campo, valor in (("smtp_host", "127.0.0.1"), ("smtp_port", s.puerto), ("smtp_remitente", "avisos@institucion.example"), ("smtp_starttls", False),
                             ("smtp_usuario", "usuario" if kw.get("auth") else ""), ("smtp_clave", "x"), ("smtp_timeout_s", 3)):
            monkeypatch.setattr(ajustes, campo, valor)
        return s

    yield levantar
    for s in servidores:
        s.cerrar()


def test_smtp_real_entrega_el_mensaje_con_tildes(smtp):
    s = smtp()
    EmisorCorreo().enviar("ana@ejemplo.com", "Su cita quedó agendada", "Hola, Ana.\n\nSu cita es el lunes a las 9:00 a. m. — ¡hasta pronto!")
    m, = s.recibidos
    assert "avisos@institucion.example" in m["de"] and "ana@ejemplo.com" in m["para"][0]
    import email
    from email import policy

    msg = email.message_from_string(m["datos"], policy=policy.default)
    assert msg["Subject"] == "Su cita quedó agendada" and msg["To"] == "ana@ejemplo.com" and msg["Message-ID"] and msg["Date"]
    assert "hasta pronto" in msg.get_content() and "9:00 a. m." in msg.get_content()


def test_smtp_con_clave_incorrecta_no_se_reintenta(smtp):
    smtp(auth="mal")
    with pytest.raises(ErrorDeEnvio) as e:
        EmisorCorreo().enviar("ana@ejemplo.com", "a", "b")
    assert e.value.codigo == "smtp_autenticacion" and e.value.reintentable is False


def test_smtp_con_clave_correcta_entrega(smtp):
    s = smtp(auth="bien")
    EmisorCorreo().enviar("ana@ejemplo.com", "a", "b")
    assert len(s.recibidos) == 1


def test_smtp_destino_rechazado_no_se_reintenta(smtp):
    smtp(rechazar_destino=True)
    with pytest.raises(ErrorDeEnvio) as e:
        EmisorCorreo().enviar("nadie@ejemplo.com", "a", "b")
    assert e.value.codigo == "smtp_destino_rechazado" and e.value.reintentable is False


def test_smtp_apagado_es_un_fallo_temporal(smtp, monkeypatch):
    s = smtp()
    s.cerrar()
    with pytest.raises(ErrorDeEnvio) as e:
        EmisorCorreo().enviar("ana@ejemplo.com", "a", "b")
    assert e.value.codigo == "smtp_no_disponible" and e.value.reintentable is True


def test_smtp_sin_configurar_se_reconoce(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_host", "")
    assert EmisorCorreo().configurado() is False


def test_de_punta_a_punta_agendar_y_recibir_el_correo(db, smtp):
    s = smtp()
    b = escenario_con_correo(db)
    c = agendar(db, b)
    assert ns.procesar_pendientes(db) == {"enviada": 1}
    m, = s.recibidos
    assert "ana@ejemplo.com" in m["para"][0] and c.codigo_reserva in m["datos"]


def test_el_registro_de_logs_no_filtra_el_correo(db, correo, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    b = escenario_con_correo(db)
    agendar(db, b)
    correo.falla = ErrorDeEnvio("smtp_no_disponible")
    ns.procesar_pendientes(db)
    assert "ana@ejemplo.com" not in caplog.text


# ======================= API =======================
def test_api_estado_listado_y_permisos(client, db, correo, monkeypatch):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    assert client.get(f"{A}/notificaciones/estado").status_code == 401
    b = escenario_con_correo(db)
    agendar(db, b)
    db.commit()
    h = cabecera(client, "rec@ejemplo.com")
    e = client.get(f"{A}/notificaciones/estado", headers=h).json()
    assert e["canales"]["SMS"] is False and e["conteo"]["PENDIENTE"] == 1 and e["activas"] is True
    lista = client.get(f"{A}/notificaciones", headers=h).json()
    assert len(lista) == 1 and lista[0]["destino_enmascarado"] == "a***@ejemplo.com" and "cuerpo" not in lista[0]  # el listado no trae el texto
    assert client.get(f"{A}/notificaciones", headers=h, params={"estado": "ENVIADA"}).json() == []
    assert client.get(f"{A}/notificaciones", headers=h, params={"estado": "inventado"}).status_code == 422
    hc = cabecera(client, "consulta@ejemplo.com")
    assert client.post(f"{A}/notificaciones/procesar", headers=hc).status_code == 403


def test_api_procesar_y_reintentar(client, db, correo):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    agendar(db, escenario_con_correo(db))
    db.commit()
    h = cabecera(client, "rec@ejemplo.com")
    correo.falla = ErrorDeEnvio("smtp_destino_rechazado", reintentable=False)
    assert client.post(f"{A}/notificaciones/procesar", headers=h).json() == {"recordatorios_nuevos": 0, "fallida": 1}
    n, = client.get(f"{A}/notificaciones", headers=h, params={"estado": "FALLIDA"}).json()
    correo.falla = None
    r = client.post(f"{A}/notificaciones/{n['id']}/reintentar", headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "PENDIENTE"
    assert client.post(f"{A}/notificaciones/{n['id']}/reintentar", headers=h).status_code == 409  # ya no está fallida
    assert client.post(f"{A}/notificaciones/99999/reintentar", headers=h).status_code == 404
    assert client.post(f"{A}/notificaciones/procesar", headers=h).json()["enviada"] == 1


def test_api_el_paciente_puede_apagar_los_avisos(client, db, correo):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    p = con_email(db)
    db.commit()
    h = cabecera(client, "rec@ejemplo.com")
    assert client.get(f"{A}/pacientes/{p.id}", headers=h).json()["notificaciones_activas"] is True
    r = client.patch(f"{A}/pacientes/{p.id}", headers=h, json={"notificaciones_activas": False})
    assert r.status_code == 200 and r.json()["notificaciones_activas"] is False
    r = client.patch(f"{A}/pacientes/{p.id}", headers=h, json={"notificaciones_activas": None, "activo": None})
    assert r.status_code == 200 and r.json()["notificaciones_activas"] is False and r.json()["activo"] is True  # un null no rompe ni apaga nada

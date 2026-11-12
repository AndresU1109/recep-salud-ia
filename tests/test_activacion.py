"""Activación por voz: el panel escucha en modo de espera y el servidor responde solo sí o no a «¿esto es la frase de activación?»."""
import pytest
from sqlalchemy import select

from app.models import AuditoriaLog
from app.models.enums import Rol
from app.services import activacion_service as act
from app.services import config_ia_service
from app.stt import transcriptor as tr
from app.stt.transcriptor import ResultadoSTT
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_voz import pcm

A = "/api/v1"
FRASES = ["hola asistente", "oye asistente", "hola sofia"]


@pytest.mark.parametrize("texto", ["Hola asistente", "hola, asistente.", "ola asistente", "Hola asistente virtual", "oye asistente quiero una cita", "Hola Sofía", "¡Oye, asistente!"])
def test_reconoce_la_frase_aunque_el_reconocimiento_de_voz_la_deforme(texto):
    assert act.coincide(texto, FRASES)


@pytest.mark.parametrize("texto", ["quiero una cita", "buenos días", "hola buenas tardes", "", "asistente", "el asistente virtual de la clínica me atendió muy bien el otro día la verdad",
                                   "hola amigo cómo estás"])
def test_no_se_activa_con_otras_conversaciones_ni_con_frases_largas(texto):
    assert not act.coincide(texto, FRASES)


def test_las_frases_salen_del_nombre_del_asistente_y_de_la_que_elija_el_administrador(db):
    admin = crear_usuario(db, email="adm@ejemplo.com", rol=Rol.ADMINISTRADOR)
    assert act.frases(db) == ["hola asistente", "oye asistente"]
    config_ia_service.actualizar(db, admin, {"nombre_asistente": "Sofía"})
    assert act.frases(db) == ["hola sofia", "oye sofia", "hola asistente", "oye asistente"]
    config_ia_service.actualizar(db, admin, {"frase_activacion": "Buen día Sofía"})
    assert act.frases(db)[0] == "buen dia sofia"


class STTFalso:
    def __init__(self, texto="", **kw):
        self.texto, self.kw, self.llamadas = texto, kw, 0

    def transcribir(self, datos, formato="auto", idioma=None):
        self.llamadas += 1
        return ResultadoSTT(texto=self.texto, confianza=self.kw.get("confianza", 0.8), silencio=self.kw.get("silencio", False), motivo=None, baja_confianza=self.kw.get("baja", False),
                            duracion_audio_s=2.0, duracion_proceso_ms=100, dispositivo="cpu")


@pytest.fixture
def conectado(client, db, monkeypatch):
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    h = cabecera(client, "rec@ejemplo.com")

    def con(texto="", **kw):
        stt = STTFalso(texto, **kw)
        monkeypatch.setattr(tr, "obtener_transcriptor", lambda: stt)
        return stt
    return client, h, con


def enviar(client, h, segundos=2.0):
    return client.post(f"{A}/voz/activacion", headers={**h, "Content-Type": "application/octet-stream"}, content=pcm(segundos))


def test_la_frase_de_activacion_responde_si_y_no_devuelve_lo_que_se_dijo(conectado, db):
    client, h, con = conectado
    con("Hola asistente.")
    r = enviar(client, h)
    assert r.status_code == 200 and r.json() == {"activar": True}  # solo sí o no: el texto no sale del servidor
    con("quiero cancelar mi cita, mi cédula es 1020304050")
    assert enviar(client, h).json() == {"activar": False}
    assert db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion.like("stt%"))).all() == []  # ni siquiera se audita cada frase captada


def test_con_poca_confianza_o_silencio_no_se_activa(conectado):
    client, h, con = conectado
    con("hola asistente", baja=True)
    assert enviar(client, h).json() == {"activar": False}
    con("", silencio=True)
    assert enviar(client, h).json() == {"activar": False}


def test_el_audio_vacio_o_demasiado_largo_se_rechaza(conectado):
    client, h, con = conectado
    stt = con("hola asistente")
    assert client.post(f"{A}/voz/activacion", headers={**h, "Content-Type": "application/octet-stream"}, content=b"").status_code == 422
    assert enviar(client, h, segundos=9).status_code == 422
    assert stt.llamadas == 0


def test_hace_falta_iniciar_sesion_y_permiso_de_conversar(client, db, monkeypatch):
    monkeypatch.setattr(tr, "obtener_transcriptor", lambda: STTFalso("hola asistente"))
    assert client.post(f"{A}/voz/activacion", content=pcm(1)).status_code == 401
    assert client.get(f"{A}/voz/activacion").status_code == 401
    crear_usuario(db, email="consulta@ejemplo.com", rol=Rol.CONSULTA)
    h = cabecera(client, "consulta@ejemplo.com")
    assert client.post(f"{A}/voz/activacion", headers=h, content=pcm(1)).status_code == 403
    assert client.get(f"{A}/voz/activacion", headers=h).status_code == 403


def test_el_panel_pide_las_frases(conectado):
    client, h, _ = conectado
    assert client.get(f"{A}/voz/activacion", headers=h).json() == {"frases": ["hola asistente", "oye asistente"]}

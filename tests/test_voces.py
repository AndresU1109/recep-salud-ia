"""Elegir la voz del asistente (Piper, Kokoro o la de Windows): catálogo, elección, muestra, caché por voz y el motor Kokoro (sin el modelo real)."""
import numpy as np
import pytest

from app.core.config import Settings, get_settings
from app.models import ConfiguracionIA
from app.models.enums import Rol
from app.services import config_ia_service as cfg
from app.tts import motores
from app.tts import servicio as modulo_servicio
from app.tts.motores import Audio, MotorKokoro, voces_kokoro
from app.tts.servicio import ServicioTTS
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_tts import MotorFalso, wav

A = "/api/v1"


@pytest.fixture
def archivos_kokoro(tmp_path):
    """Un «modelo» y un archivo de voces de mentira: lo justo para que el motor se declare disponible."""
    modelo, voces = tmp_path / "kokoro.onnx", tmp_path / "voces.bin"
    modelo.write_bytes(b"x")
    with open(voces, "wb") as f:
        np.savez(f, ef_dora=np.zeros(3), em_alex=np.zeros(3), af_bella=np.zeros(3))  # af_bella es inglés: no debe ofrecerse
    return {"tts_kokoro_modelo": str(modelo), "tts_kokoro_voces": str(voces)}


@pytest.fixture(autouse=True)
def ajustes_limpios():
    yield
    cfg.restaurar_ajustes()


@pytest.fixture
def kokoro_falso(monkeypatch):
    llamadas = []

    def sintetizar(self, texto, velocidad):
        llamadas.append(self.voz)
        return Audio(wav(1.0), 24000, 1.0, "kokoro")
    monkeypatch.setattr(MotorKokoro, "sintetizar", sintetizar)
    return llamadas


def servicio_con(archivos, *extra, **ajustes) -> ServicioTTS:
    s = Settings(_env_file=None, **archivos, **ajustes)
    return ServicioTTS(s, [MotorFalso("piper"), MotorKokoro(s), *extra, MotorFalso("sapi")])


# ======================= catálogo =======================
def test_solo_se_ofrecen_las_voces_en_espanol_instaladas(archivos_kokoro):
    assert voces_kokoro(archivos_kokoro["tts_kokoro_voces"]) == ["ef_dora", "em_alex"]
    ids = [v["id"] for v in servicio_con(archivos_kokoro).voces()]
    assert ids == ["", "piper", "kokoro:ef_dora", "kokoro:em_alex", "sapi"]  # sin af_bella y sin em_santa (no está en el archivo)


def test_sin_los_archivos_de_kokoro_no_se_ofrece_y_todo_sigue_funcionando(tmp_path):
    s = servicio_con({"tts_kokoro_modelo": str(tmp_path / "no.onnx"), "tts_kokoro_voces": str(tmp_path / "no.bin")})
    assert [v["id"] for v in s.voces()] == ["", "piper", "sapi"] and s.motor().nombre == "piper"


# ======================= elegir =======================
def test_la_voz_elegida_se_usa_y_cada_voz_tiene_su_cache(archivos_kokoro, kokoro_falso):
    s = servicio_con(archivos_kokoro, tts_voz="kokoro:em_alex")
    assert s.motor().voz == "em_alex"
    s.sintetizar("Hola, buenos días")
    s.sintetizar("Hola, buenos días")  # misma voz y texto: de la caché
    assert kokoro_falso == ["em_alex"]
    assert s.sintetizar("Hola, buenos días", voz="kokoro:ef_dora").motor == "kokoro" and kokoro_falso == ["em_alex", "ef_dora"]  # otra voz: no reutiliza el audio


def test_una_voz_elegida_que_ya_no_esta_cae_a_la_automatica(archivos_kokoro):
    s = servicio_con(archivos_kokoro, tts_voz="kokoro:em_santa")  # no está en el archivo de voces
    assert s.motor().nombre == "piper"


def test_pedir_expresamente_una_voz_inexistente_da_error_claro(archivos_kokoro):
    from app.core.errores import ErrorNegocio

    with pytest.raises(ErrorNegocio) as e:
        servicio_con(archivos_kokoro).motor("kokoro:nadie")
    assert e.value.codigo == "voz_no_disponible" and e.value.status == 422


def test_cambiar_la_voz_no_recarga_el_modelo(archivos_kokoro, monkeypatch):
    cargas = []
    motores._KOKORO.clear()
    monkeypatch.setitem(__import__("sys").modules, "kokoro_onnx", type("M", (), {"Kokoro": lambda modelo, voces: cargas.append(modelo) or type("K", (), {"create": lambda self, *a, **k: (np.zeros(2400, dtype="float32"), 24000)})()}))
    s = Settings(_env_file=None, **archivos_kokoro)
    MotorKokoro(s, "ef_dora").sintetizar("hola", 1.0)
    MotorKokoro(s, "em_alex").sintetizar("hola", 1.0)
    assert len(cargas) == 1
    motores._KOKORO.clear()


def test_kokoro_devuelve_un_wav_valido(archivos_kokoro, monkeypatch):
    import io
    import wave

    motores._KOKORO.clear()
    monkeypatch.setitem(__import__("sys").modules, "kokoro_onnx", type("M", (), {"Kokoro": lambda modelo, voces: type("K", (), {"create": lambda self, t, **k: (np.sin(np.arange(24000) / 10).astype("float32") * 2, 24000)})()}))
    a = MotorKokoro(Settings(_env_file=None, **archivos_kokoro), "ef_dora").sintetizar("hola", 1.0)
    with wave.open(io.BytesIO(a.wav)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getnframes() == 24000  # y los valores fuera de rango se recortan
    assert a.motor == "kokoro" and a.duracion_s == pytest.approx(1.0)
    motores._KOKORO.clear()


# ======================= API =======================
@pytest.fixture
def admin(client, db):
    crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    return cabecera(client, "admin@ejemplo.com")


@pytest.fixture
def tts_falso(monkeypatch, archivos_kokoro, kokoro_falso):
    s = servicio_con(archivos_kokoro)
    monkeypatch.setattr(modulo_servicio, "obtener_tts", lambda: s)
    monkeypatch.setattr(s, "precargar", lambda: None)
    return s


def test_el_panel_lista_las_voces_y_guarda_la_elegida(client, db, admin, tts_falso):
    antes = client.get(f"{A}/config-ia", headers=admin).json()["voz"]
    assert antes["elegida"] == "" and [v["id"] for v in antes["voces"]] == ["", "piper", "kokoro:ef_dora", "kokoro:em_alex", "sapi"]
    r = client.put(f"{A}/config-ia/voz", headers=admin, json={"voz": "kokoro:ef_dora", "velocidad": 0.9})
    assert r.status_code == 200, r.text
    assert r.json()["voz"]["elegida"] == "kokoro:ef_dora" and r.json()["voz"]["velocidad"] == 0.9
    assert get_settings().tts_voz == "kokoro:ef_dora" and get_settings().tts_velocidad == 0.9
    assert db.get(ConfiguracionIA, "voz_asistente").valor == {"v": "kokoro:ef_dora"}
    # volver a la automática borra la preferencia
    client.put(f"{A}/config-ia/voz", headers=admin, json={"voz": ""})
    assert db.get(ConfiguracionIA, "voz_asistente") is None and get_settings().tts_voz == ""


def test_una_voz_que_no_existe_se_rechaza_y_no_cambia_nada(client, db, admin, tts_falso):
    r = client.put(f"{A}/config-ia/voz", headers=admin, json={"voz": "kokoro:af_bella"})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "voz_no_disponible"
    assert db.get(ConfiguracionIA, "voz_asistente") is None and get_settings().tts_voz == ""
    assert client.put(f"{A}/config-ia/voz", headers=admin, json={"voz": "piper", "velocidad": 3}).status_code == 422


def test_la_muestra_usa_la_voz_pedida_sin_cambiar_la_elegida(client, db, tts_falso, kokoro_falso):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    r = client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": "Buenos días, esta es una muestra", "voz": "kokoro:em_alex"})
    assert r.status_code == 200 and r.headers["x-motor"] == "kokoro" and kokoro_falso == ["em_alex"]
    assert get_settings().tts_voz == ""
    assert client.post(f"{A}/tts/sintetizar", headers=h, json={"texto": "hola", "voz": "kokoro:nadie"}).status_code == 422


def test_solo_el_administrador_cambia_la_voz(client, db, tts_falso):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    assert client.put(f"{A}/config-ia/voz", headers=cabecera(client, "coord@ejemplo.com"), json={"voz": "piper"}).status_code == 403
    assert client.put(f"{A}/config-ia/voz", json={"voz": "piper"}).status_code == 401


def test_la_voz_elegida_se_aplica_al_arrancar(db, tts_falso):
    db.add(ConfiguracionIA(clave="voz_asistente", valor={"v": "piper"}))
    db.add(ConfiguracionIA(clave="voz_velocidad", valor={"v": 0.8}))
    db.flush()
    cfg.aplicar_a_ajustes(db)
    assert get_settings().tts_voz == "piper" and get_settings().tts_velocidad == 0.8

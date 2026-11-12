import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import CLAVE_POR_DEFECTO, Settings
from app.core.logging import FiltroDatosPersonales, redactar
from app.main import app

client = TestClient(app)


def test_health_responde_ok():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "ok"


def test_dependencias_no_filtran_credenciales(monkeypatch):
    r = client.get("/api/v1/health/dependencias")
    assert r.status_code == 200
    cuerpo = r.text
    assert "password" not in cuerpo.lower()
    assert "postgresql+psycopg" not in cuerpo
    assert set(r.json()) >= {"estado", "base_de_datos", "ollama"}


def test_produccion_exige_clave_real():
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key=CLAVE_POR_DEFECTO, _env_file=None)
    with pytest.raises(ValueError):
        Settings(app_env="production", secret_key="corta", _env_file=None)
    with pytest.raises(ValueError):  # falta la clave de cifrado de datos personales
        Settings(app_env="production", secret_key="x" * 40, _env_file=None)
    ok = Settings(app_env="production", secret_key="x" * 40, data_encryption_key="k" * 44, _env_file=None)
    assert ok.app_env == "production"


@pytest.mark.parametrize(
    "texto, no_debe_contener",
    [
        ("Paciente con cédula 1023456789 pidió cita", "1023456789"),
        ("Documento 1.023.456.789 verificado", "1.023.456.789"),
        ("Escribir a maria.ficticia@example.com", "maria.ficticia@example.com"),
        ("Celular 300 123 4567", "123 4567"),
        ("Celular +57 3001234567", "3001234567"),
    ],
)
def test_logs_ocultan_datos_personales(texto, no_debe_contener):
    assert no_debe_contener not in redactar(texto)


def test_filtro_de_logging_redacta_argumentos():
    registro = logging.LogRecord("t", logging.INFO, __file__, 1, "doc %s", ("1023456789",), None)
    FiltroDatosPersonales().filter(registro)
    assert "1023456789" not in registro.getMessage()


def test_texto_normal_no_se_altera():
    assert redactar("Cita de medicina general el 5 de marzo a las 10") == "Cita de medicina general el 5 de marzo a las 10"

"""Fase 17: configuración de producción, cabeceras, retención de datos y coherencia de los archivos de despliegue."""
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text

from app.core.config import Settings, get_settings
from app.models import AuditoriaLog, Conversacion, Mensaje, Notificacion
from app.models.enums import CanalConversacion, CanalNotificacion, EstadoNotificacion, RolMensaje, TipoNotificacion
from app.services import retencion_service
from tests.factories import crear_base

RAIZ = Path(__file__).resolve().parent.parent
FERNET = "x" * 43 + "="  # forma de una clave Fernet; aquí solo se valida que exista
SEGURA = {"secret_key": "s" * 40, "data_encryption_key": FERNET}


# ======================= producción =======================
@pytest.mark.parametrize("ajustes, mensaje", [({"secret_key": "corta"}, "SECRET_KEY"), ({"secret_key": "s" * 40}, "DATA_ENCRYPTION_KEY"),
                                              ({**SEGURA, "cors_origins": ["http://midominio.co"]}, "https")])
def test_produccion_exige_secretos_y_https(ajustes, mensaje):
    with pytest.raises(ValidationError, match=mensaje):
        Settings(_env_file=None, app_env="production", **ajustes)


def test_produccion_acepta_una_configuracion_correcta():
    s = Settings(_env_file=None, app_env="production", **SEGURA, cors_origins=["https://recepcion.institucion.co", "http://localhost:8000"])
    assert s.app_env == "production"


def app_en(monkeypatch, entorno):
    import app.main as principal

    ajustes = Settings(_env_file=None, app_env=entorno, notificaciones_activas=False, **SEGURA)
    monkeypatch.setattr(principal, "get_settings", lambda: ajustes)
    return principal.create_app()


def test_en_produccion_no_se_publica_la_documentacion(monkeypatch):
    cliente = TestClient(app_en(monkeypatch, "production"))
    for ruta in ("/docs", "/redoc", "/openapi.json"):
        assert cliente.get(ruta).status_code == 404, ruta
    assert cliente.get("/api/v1/health").status_code == 200


def test_en_desarrollo_la_documentacion_sigue_disponible(monkeypatch):
    assert TestClient(app_en(monkeypatch, "development")).get("/openapi.json").status_code == 200


# ======================= cabeceras =======================
def test_las_respuestas_de_la_api_no_se_guardan_en_cache(client, db):
    r = client.get("/api/v1/health")
    assert r.headers["cache-control"] == "no-store"
    assert client.post("/api/v1/auth/login", json={"email": "a@b.co", "clave": "x" * 12}).headers["cache-control"] == "no-store"  # también los errores


def test_el_panel_se_revalida_y_bootstrap_se_guarda(client):
    js = client.get("/panel/js/app.js")
    assert js.status_code == 200 and js.headers["cache-control"] == "no-cache" and js.headers.get("etag")
    assert client.get("/panel/js/app.js", headers={"If-None-Match": js.headers["etag"]}).status_code == 304  # revalidar no vuelve a descargar
    assert client.get("/vendor/bootstrap/bootstrap.bundle.min.js").headers["cache-control"] == "public, max-age=86400"
    r = client.get("/panel/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"


# ======================= retención =======================
def _envejecer(db, tabla, columna, id_, dias):
    db.execute(text(f"UPDATE {tabla} SET {columna} = now() - make_interval(days => :d) WHERE id = :i"), {"d": dias, "i": id_})


def aviso(db, paciente, estado, dias, n):
    x = Notificacion(clave_unica=f"ret:{n}", paciente_id=paciente.id, tipo=TipoNotificacion.CITA_AGENDADA, canal=CanalNotificacion.EMAIL, estado=estado,
                     programada_para=datetime.now(timezone.utc), asunto="Su cita", cuerpo="texto del aviso", destino_enmascarado="a***@ejemplo.com")
    db.add(x)
    db.flush()
    _envejecer(db, "notificaciones", "updated_at", x.id, dias)
    return x


def conversacion(db, dias, prueba=False):
    c = Conversacion(canal=CanalConversacion.TEXTO, es_prueba=prueba)
    db.add(c)
    db.flush()
    db.add(Mensaje(conversacion_id=c.id, rol=RolMensaje.PACIENTE, contenido="contenido sensible", intencion="x"))
    db.add(Mensaje(conversacion_id=c.id, rol=RolMensaje.AGENTE, contenido="respuesta", intencion="saludo"))
    db.flush()
    db.execute(text("UPDATE conversaciones SET iniciada_en = now() - make_interval(days => :d) WHERE id = :i"), {"d": dias, "i": c.id})
    return c


def test_los_avisos_viejos_pierden_el_texto_pero_no_el_registro(db):
    b = crear_base(db)
    viejo, reciente, pendiente = aviso(db, b.pac1, EstadoNotificacion.ENVIADA, 120, 1), aviso(db, b.pac1, EstadoNotificacion.ENVIADA, 10, 2), aviso(db, b.pac1, EstadoNotificacion.PENDIENTE, 400, 3)
    assert retencion_service.aplicar(db)["avisos_sin_texto"] == 1
    db.refresh(viejo), db.refresh(reciente), db.refresh(pendiente)
    assert (viejo.cuerpo, viejo.asunto, viejo.destino_enmascarado) == ("", "", None) and viejo.estado == EstadoNotificacion.ENVIADA  # queda para las cifras
    assert reciente.cuerpo == "texto del aviso" and pendiente.cuerpo == "texto del aviso"  # lo reciente y lo pendiente se conservan
    assert retencion_service.aplicar(db)["avisos_sin_texto"] == 0  # idempotente


def test_las_conversaciones_reales_viejas_pierden_el_contenido_y_las_de_prueba_se_borran(db):
    vieja, reciente, prueba_vieja, prueba_nueva = conversacion(db, 400), conversacion(db, 20), conversacion(db, 45, prueba=True), conversacion(db, 5, prueba=True)
    ids = {c: c.id for c in (vieja, reciente, prueba_vieja, prueba_nueva)}  # tras borrar, la instancia ya no deja leer su id
    hecho = retencion_service.aplicar(db)
    assert hecho["mensajes_sin_contenido"] == 2 and hecho["conversaciones_de_prueba_borradas"] == 1
    mensajes = lambda c: [(m.contenido, m.intencion) for m in db.scalars(select(Mensaje).where(Mensaje.conversacion_id == ids[c]).order_by(Mensaje.id))]  # noqa: E731
    db.expire_all()
    assert mensajes(vieja) == [(None, "x"), (None, "saludo")]  # queda la intención para los reportes
    assert mensajes(reciente)[0][0] == "contenido sensible" and mensajes(prueba_nueva)[0][0] == "contenido sensible"
    assert db.get(Conversacion, ids[prueba_vieja]) is None and mensajes(prueba_vieja) == []  # los mensajes caen con la conversación


def test_cero_significa_no_borrar_nunca(db, monkeypatch):
    for campo in ("retencion_avisos_dias", "retencion_conversaciones_dias", "retencion_pruebas_dias"):
        monkeypatch.setattr(get_settings(), campo, 0)
    b = crear_base(db)
    x = aviso(db, b.pac1, EstadoNotificacion.ENVIADA, 5000, 9)
    conversacion(db, 5000, prueba=True)
    assert retencion_service.aplicar(db) == {"avisos_sin_texto": 0, "mensajes_sin_contenido": 0, "conversaciones_de_prueba_borradas": 0}
    db.refresh(x)
    assert x.cuerpo == "texto del aviso"


def test_la_retencion_queda_auditada_con_cifras_y_nunca_toca_la_bitacora(db):
    b = crear_base(db)
    aviso(db, b.pac1, EstadoNotificacion.OMITIDA, 200, 4)
    db.execute(text("SELECT 1"))
    antes = db.scalars(select(AuditoriaLog.id)).all()
    retencion_service.aplicar(db)
    a = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "retencion_aplicada")).one()
    assert a.actor == "sistema" and a.detalle["avisos_sin_texto"] == 1
    assert set(antes) <= set(db.scalars(select(AuditoriaLog.id)).all())  # nada de lo anterior desapareció


# ======================= archivos de despliegue =======================
def test_el_compose_no_publica_la_base_ni_la_api_fuera_de_la_maquina():
    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:${POSTGRES_PORT:-5432}:5432"' in texto and '"127.0.0.1:8000:8000"' in texto
    assert "POSTGRES_PASSWORD:?" in texto  # sin contraseña definida, no arranca


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker no está instalado")
def test_el_compose_es_valido_y_el_proxy_es_el_unico_expuesto(tmp_path):
    entorno = tmp_path / ".env"
    entorno.write_text("POSTGRES_PASSWORD=prueba-prueba-prueba\nDOMINIO=localhost\n", encoding="utf-8")
    r = subprocess.run(["docker", "compose", "--env-file", str(entorno), "--profile", "produccion", "config", "--format", "json"], cwd=RAIZ, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    servicios = json.loads(r.stdout)["services"]
    assert {"db", "api", "proxy"} <= set(servicios)
    publicados = {n: [(p.get("host_ip"), p["published"]) for p in s.get("ports", [])] for n, s in servicios.items()}
    assert {pub for _, pub in publicados["proxy"]} == {"80", "443"}
    assert all(ip == "127.0.0.1" for ip, _ in publicados["db"] + publicados["api"])  # solo el proxy (TLS) da la cara a la red


def test_la_configuracion_del_proxy_pone_las_cabeceras_de_seguridad():
    caddy = (RAIZ / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    for pieza in ("Strict-Transport-Security", "reverse_proxy api:8000", "{$DOMINIO"):
        assert pieza in caddy


def test_el_dockerfile_no_corre_como_root_ni_copia_secretos_ni_modelos():
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8").split()
    assert "USER recep" in dockerfile and "HEALTHCHECK" in dockerfile
    assert {".env", "data", ".venv", "tests"} <= set(ignorados)  # ni claves ni modelos de 1 GB dentro de la imagen


def test_el_env_de_ejemplo_no_trae_secretos_reales():
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    for linea in ejemplo.splitlines():
        if linea.startswith(("SECRET_KEY=", "DATA_ENCRYPTION_KEY=", "POSTGRES_PASSWORD=", "SMTP_CLAVE=")):
            valor = linea.split("=", 1)[1]
            assert valor in ("", "cambiar-esta-clave"), linea

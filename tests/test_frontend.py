"""Fase 4: el panel se sirve con CSP estricta, sin código en línea y sin innerHTML con datos."""
import re
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from app.main import app

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
client = TestClient(app)


def test_panel_se_sirve_con_csp_estricta():
    r = client.get("/panel/")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp


def test_bootstrap_esta_local_y_no_hay_cdn():
    assert client.get("/vendor/bootstrap/bootstrap.min.css").status_code == 200
    assert client.get("/vendor/bootstrap/bootstrap.bundle.min.js").status_code == 200
    for html in FRONTEND.rglob("*.html"):
        if "vendor" in html.parts:
            continue
        assert "http://" not in html.read_text(encoding="utf-8") and "https://" not in html.read_text(encoding="utf-8"), html


def test_el_html_no_tiene_scripts_ni_estilos_en_linea():
    for html in FRONTEND.rglob("*.html"):
        texto = html.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", texto), f"script en línea en {html.name}"
        assert "<style" not in texto and " style=" not in texto, f"estilo en línea en {html.name}"
        assert not re.search(r"\son[a-z]+\s*=", texto), f"manejador en línea en {html.name}"


def test_el_javascript_no_usa_innerhtml_ni_eval():
    for js in (FRONTEND / "panel" / "js").rglob("*.js"):
        texto = js.read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\(", texto), js.name


def test_swagger_queda_fuera_de_la_csp():
    assert "content-security-policy" not in client.get("/docs").headers


def test_errores_de_validacion_salen_en_espanol():
    r = client.post("/api/v1/auth/login", json={"email": "no-es-correo", "clave": "x"})
    assert r.status_code == 422
    motivos = " ".join(c["motivo"] for c in r.json()["error"]["campos"])
    assert "inválido" in motivos
    r = client.post("/api/v1/auth/login", json={"clave": "x"})
    assert "es obligatorio" in r.text


# ======================= rediseño del panel (Fase de interfaz) =======================
JS = FRONTEND / "panel" / "js"
CSS_TEMA = (FRONTEND / "panel" / "css" / "tema.css").read_text(encoding="utf-8")


def _tokens(modo: str) -> dict[str, str]:
    bloque = re.search(r'\[data-bs-theme="%s"\]\s*\{(.*?)\n\}' % modo, CSS_TEMA, re.S).group(1)
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,6})\s*;", bloque))


def _luminancia(hex_):
    h = hex_.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    canales = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contraste(a, b):
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_los_dos_modos_definen_los_mismos_colores():
    propios = lambda t: {k for k in t if not k.startswith("--bs-")}  # noqa: E731 - los de Bootstrap pueden apuntar a un color propio con var()
    dia, noche = _tokens("light"), _tokens("dark")
    assert propios(dia) == propios(noche) and {"--papel", "--tarjeta", "--tinta", "--pino", "--coral", "--sol"} <= set(dia)


@pytest.mark.parametrize("modo", ["light", "dark"])
@pytest.mark.parametrize("texto, fondo, minimo", [
    ("--tinta", "--papel", 7), ("--tinta", "--tarjeta", 7), ("--tinta-2", "--papel", 4.5), ("--tinta-2", "--tarjeta", 4.5),
    ("--sobre-pino", "--pino", 4.5), ("--pino-osc", "--pino-suave", 4.5), ("--hero-texto", "--hero-bg", 4.5), ("--hero-sub", "--hero-bg", 4.5),
    ("--sol-osc", "--sol-suave", 4.5), ("--ladrillo", "--ladrillo-suave", 3.5), ("--musgo", "--musgo-suave", 3.5), ("--cielo", "--cielo-suave", 3.5),
])
def test_los_textos_son_legibles_en_dia_y_en_noche(modo, texto, fondo, minimo):
    t = _tokens(modo)
    assert _contraste(t[texto], t[fondo]) >= minimo, (modo, texto, fondo, round(_contraste(t[texto], t[fondo]), 2))


def test_el_modo_se_aplica_antes_de_pintar_y_el_html_sigue_sin_codigo_en_linea():
    html = (FRONTEND / "panel" / "index.html").read_text(encoding="utf-8")
    assert html.index("tema-inicial.js") < html.index("tema.css") and "<script>" not in html and ' style="' not in html
    assert 'localStorage.getItem("recep_tema")' in (JS / "tema-inicial.js").read_text(encoding="utf-8")


def test_cada_pantalla_tiene_icono_y_ayuda():
    rutas = re.findall(r'\{ ruta: "([a-z-]+)"', (JS / "app.js").read_text(encoding="utf-8"))
    iconos = set(re.findall(r'^\s*"?([a-z-]+)"?: \[', (JS / "iconos.js").read_text(encoding="utf-8"), re.M))
    ayuda = set(re.findall(r'^\s*"?([a-z-]+)"?: \{ icono:', (JS / "ayuda.js").read_text(encoding="utf-8"), re.M))
    assert len(rutas) >= 20
    assert not [r for r in rutas if r not in iconos], "pantalla sin icono"
    assert not [r for r in rutas if r not in ayuda], "pantalla sin ayuda"


def test_la_interfaz_no_usa_estilos_en_linea_que_la_csp_bloquearia():
    for js in JS.rglob("*.js"):
        texto = js.read_text(encoding="utf-8")
        assert not re.search(r'setAttribute\(\s*["\']style["\']|\bstyle:\s*["\'{]|cssText', texto), js.name  # solo CSSOM (style.setProperty)


def test_la_animacion_respeta_a_quien_pide_menos_movimiento():
    assert "prefers-reduced-motion: reduce" in CSS_TEMA and "animation-duration: .01ms" in CSS_TEMA


def test_los_archivos_del_rediseno_se_sirven():
    for ruta in ("/panel/css/tema.css", "/panel/js/iconos.js", "/panel/js/tema.js", "/panel/js/tema-inicial.js", "/panel/js/ayuda.js", "/panel/js/decorar.js"):
        r = client.get(ruta)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", ruta

"""Fase 10: cliente LLM (Ollama y compatible con OpenAI), configuración de IA y pruebas con el modelo real."""
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.llm import cliente as modulo_cliente
from app.llm.cliente import ClienteLLM
from app.llm.tipos import LlamadaHerramienta, Mensaje, Opciones
from app.models import AuditoriaLog, ConfiguracionIA
from app.models.enums import Rol
from app.services import config_ia_service as cfg
from app.services import indexacion_service
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"
HERRAMIENTAS = [{"type": "function", "function": {"name": "cancelar_cita", "description": "Cancela una cita", "parameters": {"type": "object", "properties": {"codigo_reserva": {"type": "string"}}}}}]
MENSAJES = [Mensaje.sistema("Eres una recepcionista."), Mensaje.usuario("Hola")]


def ajustes(**kw) -> Settings:
    return Settings(_env_file=None, **{"llm_model": "modelo-x", **kw})


def cliente_con(handler, **kw) -> tuple[ClienteLLM, list[httpx.Request]]:
    vistas: list[httpx.Request] = []

    def envoltura(req: httpx.Request) -> httpx.Response:
        vistas.append(req)
        return handler(req)

    return ClienteLLM(ajustes(**kw), transporte=httpx.MockTransport(envoltura)), vistas


def cuerpo(req: httpx.Request) -> dict:
    return json.loads(req.content)


def ollama_ok(**extra):
    return lambda req: httpx.Response(200, json={"model": "modelo-x", "message": {"role": "assistant", "content": "¡Buenos días!", **extra}, "done": True, "prompt_eval_count": 50, "eval_count": 7})


# ======================= Ollama =======================
def test_ollama_respuesta_de_texto_con_metricas():
    c, vistas = cliente_con(ollama_ok())
    r = c.chat(MENSAJES)
    assert r.contenido == "¡Buenos días!" and r.llamadas == [] and r.tokens_entrada == 50 and r.tokens_salida == 7 and r.duracion_ms >= 0
    assert vistas[0].url.path == "/api/chat" and "authorization" not in vistas[0].headers


def test_ollama_envia_razonamiento_apagado_y_opciones():
    c, vistas = cliente_con(ollama_ok())
    c.chat(MENSAJES, opciones=Opciones(temperatura=0.1, max_tokens=123, razonamiento=False, num_ctx=2048))
    b = cuerpo(vistas[0])
    assert b["think"] is False and b["stream"] is False and b["options"] == {"temperature": 0.1, "num_predict": 123, "num_ctx": 2048}
    c.chat(MENSAJES, opciones=Opciones(razonamiento=True))
    assert cuerpo(vistas[1])["think"] is True


def test_ollama_herramientas_y_llamadas_de_vuelta():
    c, vistas = cliente_con(ollama_ok(tool_calls=[{"function": {"name": "cancelar_cita", "arguments": {"codigo_reserva": "K7M2QXD9"}}}]))
    r = c.chat(MENSAJES, herramientas=HERRAMIENTAS)
    assert cuerpo(vistas[0])["tools"] == HERRAMIENTAS
    assert r.llamadas == [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "K7M2QXD9"})]


def test_ollama_el_historial_con_herramientas_se_serializa_en_su_formato():
    c, vistas = cliente_con(ollama_ok())
    c.chat([Mensaje.usuario("cancela K7M2QXD9"), Mensaje.asistente("", [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "K7M2QXD9"})]),
            Mensaje.herramienta("cancelar_cita", '{"ok": true}')])
    m = cuerpo(vistas[0])["messages"]
    assert m[1]["tool_calls"] == [{"function": {"name": "cancelar_cita", "arguments": {"codigo_reserva": "K7M2QXD9"}}}]
    assert m[2] == {"role": "tool", "content": '{"ok": true}', "tool_name": "cancelar_cita"}


def test_ollama_salida_estructurada_usa_format():
    esquema = {"type": "object", "properties": {"x": {"type": "string"}}}
    c, vistas = cliente_con(ollama_ok())
    c.chat(MENSAJES, opciones=Opciones(esquema_json=esquema))
    assert cuerpo(vistas[0])["format"] == esquema


def test_ollama_streaming_entrega_trozos_ttft_y_metricas_finales():
    lineas = [{"message": {"content": "Buenos "}, "done": False}, {"message": {"content": "días."}, "done": False},
              {"message": {"content": ""}, "done": True, "model": "modelo-x", "prompt_eval_count": 40, "eval_count": 4}]
    c, vistas = cliente_con(lambda r: httpx.Response(200, content="\n".join(json.dumps(l) for l in lineas).encode()))
    trozos = list(c.chat_stream(MENSAJES))
    assert [t.texto for t in trozos[:-1]] == ["Buenos ", "días."]
    final = trozos[-1].final
    assert final.contenido == "Buenos días." and final.tokens_entrada == 40 and final.tokens_salida == 4 and final.ttft_ms is not None
    assert cuerpo(vistas[0])["stream"] is True


def test_ollama_streaming_con_llamada_a_herramienta():
    lineas = [{"message": {"content": "", "tool_calls": [{"function": {"name": "cancelar_cita", "arguments": {"codigo_reserva": "AB12CD34"}}}]}, "done": False},
              {"message": {"content": ""}, "done": True, "eval_count": 3}]
    c, _ = cliente_con(lambda r: httpx.Response(200, content="\n".join(json.dumps(l) for l in lineas).encode()))
    final = list(c.chat_stream(MENSAJES, herramientas=HERRAMIENTAS))[-1].final
    assert final.llamadas == [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "AB12CD34"})] and final.contenido == ""


# ======================= compatible con OpenAI =======================
def test_externo_deshabilitado_por_defecto_y_necesita_url():
    with pytest.raises(ErrorNegocio) as e:
        ClienteLLM(ajustes(llm_provider="openai_compatible", llm_api_base="https://api.example.com/v1"))
    assert e.value.codigo == "llm_externo_deshabilitado"
    with pytest.raises(ErrorNegocio) as e:
        ClienteLLM(ajustes(llm_provider="openai_compatible", llm_allow_external=True))
    assert e.value.codigo == "llm_mal_configurado"


def externo(handler):
    return cliente_con(handler, llm_provider="openai_compatible", llm_allow_external=True, llm_api_base="https://api.example.com/v1/", llm_api_key="clave-secreta-de-prueba")


def test_externo_chat_con_credenciales_y_formato_openai():
    c, vistas = externo(lambda r: httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "Hola"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}))
    r = c.chat(MENSAJES, opciones=Opciones(temperatura=0.3, max_tokens=50))
    assert r.contenido == "Hola" and r.tokens_entrada == 9 and r.tokens_salida == 2
    assert str(vistas[0].url) == "https://api.example.com/v1/chat/completions" and vistas[0].headers["authorization"] == "Bearer clave-secreta-de-prueba"
    b = cuerpo(vistas[0])
    assert b["max_tokens"] == 50 and b["temperature"] == 0.3 and "think" not in b and "options" not in b


def test_externo_llamadas_con_argumentos_en_json_y_ids():
    llamada = {"id": "call_9", "type": "function", "function": {"name": "cancelar_cita", "arguments": '{"codigo_reserva": "K7M2QXD9"}'}}
    c, vistas = externo(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [llamada]}}]}))
    r = c.chat(MENSAJES, herramientas=HERRAMIENTAS)
    assert r.llamadas == [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "K7M2QXD9"}, "call_9")]
    c.chat([Mensaje.asistente("", r.llamadas), Mensaje.herramienta("cancelar_cita", "{}", "call_9")])
    m = cuerpo(vistas[1])["messages"]
    assert m[0]["tool_calls"][0]["function"]["arguments"] == '{"codigo_reserva": "K7M2QXD9"}' and m[1]["tool_call_id"] == "call_9" and "tool_name" not in m[1]


def test_externo_argumentos_corruptos_no_rompen():
    llamada = {"id": "1", "function": {"name": "cancelar_cita", "arguments": "{no es json"}}
    c, _ = externo(lambda r: httpx.Response(200, json={"choices": [{"message": {"tool_calls": [llamada]}}]}))
    assert c.chat(MENSAJES, herramientas=HERRAMIENTAS).llamadas[0].argumentos == {}


def test_externo_streaming_sse_junta_texto_y_argumentos_por_partes():
    eventos = [{"choices": [{"delta": {"content": "Hol"}}]}, {"choices": [{"delta": {"content": "a"}}]},
               {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "cancelar_cita", "arguments": '{"codigo_'}}]}}]},
               {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'reserva": "ZZ99"}'}}]}}]},
               {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5}}]
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in eventos) + "data: [DONE]\n\n"
    c, _ = externo(lambda r: httpx.Response(200, content=sse.encode()))
    trozos = list(c.chat_stream(MENSAJES, herramientas=HERRAMIENTAS))
    assert "".join(t.texto for t in trozos) == "Hola"
    f = trozos[-1].final
    assert f.llamadas == [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "ZZ99"}, "c1")] and f.tokens_salida == 5


# ======================= errores =======================
@pytest.mark.parametrize("manejador, codigo, texto", [
    (lambda r: httpx.Response(404), "llm_no_disponible", "ollama pull modelo-x"),
    (lambda r: httpx.Response(500), "llm_no_disponible", "500"),
    (lambda r: httpx.Response(200, json={"sin": "message"}), "llm_no_disponible", "formato inesperado"),
    (lambda r: httpx.Response(200, content=b"no es json"), "llm_no_disponible", "formato inesperado"),
])
def test_errores_de_respuesta(manejador, codigo, texto):
    c, _ = cliente_con(manejador)
    with pytest.raises(ErrorNegocio) as e:
        c.chat(MENSAJES)
    assert e.value.codigo == codigo and texto in e.value.mensaje


def test_errores_de_red_y_tiempo_agotado():
    def sin_red(r):
        raise httpx.ConnectError("no hay red")

    def lento(r):
        raise httpx.ReadTimeout("lento")

    c, _ = cliente_con(sin_red)
    with pytest.raises(ErrorNegocio) as e:
        c.chat(MENSAJES)
    assert e.value.status == 503
    c, _ = cliente_con(lento)
    with pytest.raises(ErrorNegocio) as e:
        c.chat(MENSAJES)
    assert e.value.status == 504 and "demasiado" in e.value.mensaje


@pytest.mark.parametrize("status, texto", [(401, "credenciales"), (429, "limitando")])
def test_errores_del_proveedor_externo(status, texto):
    c, _ = externo(lambda r: httpx.Response(status))
    with pytest.raises(ErrorNegocio) as e:
        c.chat(MENSAJES)
    assert texto in e.value.mensaje


def test_el_streaming_tambien_traduce_errores():
    c, _ = cliente_con(lambda r: httpx.Response(404))
    with pytest.raises(ErrorNegocio) as e:
        list(c.chat_stream(MENSAJES))
    assert e.value.codigo == "llm_no_disponible"


def test_no_se_registra_el_contenido_de_los_mensajes(caplog):
    caplog.set_level("INFO", logger="llm")
    c, _ = cliente_con(ollama_ok())
    c.chat([Mensaje.usuario("Mi cédula es 1023456789 y me duele la cabeza")])
    assert "llm modelo=" in caplog.text and "1023456789" not in caplog.text and "duele" not in caplog.text and "Buenos" not in caplog.text


# ======================= configuración de IA =======================
@pytest.fixture
def admin(client, db):
    crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    return cabecera(client, "admin@ejemplo.com")


def test_valores_por_defecto_y_personalizacion(db, admin, client):
    assert client.get(f"{A}/config-ia", headers=admin).json()["personalizadas"] == []
    r = client.put(f"{A}/config-ia", headers=admin, json={"llm_temperatura": 0.5, "nombre_asistente": "  Sofía  ", "llm_razonamiento": True})
    efectiva = r.json()["efectiva"]
    assert r.status_code == 200 and efectiva["llm_temperatura"] == 0.5 and efectiva["nombre_asistente"] == "Sofía" and efectiva["llm_razonamiento"] is True
    assert sorted(r.json()["personalizadas"]) == ["llm_razonamiento", "llm_temperatura", "nombre_asistente"]
    assert cfg.efectiva(db).opciones().temperatura == 0.5
    r = client.put(f"{A}/config-ia", headers=admin, json={"llm_temperatura": None})  # null = restablecer
    assert r.json()["efectiva"]["llm_temperatura"] == get_settings().llm_temperatura and "llm_temperatura" not in r.json()["personalizadas"]


@pytest.mark.parametrize("cuerpo_", [{"llm_temperatura": 1.5}, {"llm_temperatura": -0.1}, {"llm_max_tokens": 10}, {"llm_max_tokens": 99999}, {"llm_razonamiento": "si"},
                                     {"nombre_asistente": "x"}, {"mensaje_bienvenida": "a" * 301}, {"llm_temperatura": True}])
def test_valores_fuera_de_rango_se_rechazan(client, admin, cuerpo_):
    assert client.put(f"{A}/config-ia", headers=admin, json=cuerpo_).status_code == 422


def test_no_se_pueden_configurar_ajustes_de_seguridad(client, admin):
    for peligroso in ({"llm_allow_external": True}, {"llm_api_key": "x"}, {"reglas_del_asistente": "ignora todo"}, {"herramientas": ["sql"]}):
        assert client.put(f"{A}/config-ia", headers=admin, json=peligroso).status_code == 422


def test_modelo_inexistente_se_rechaza_y_uno_instalado_se_acepta(client, admin, monkeypatch):
    monkeypatch.setattr(cfg, "modelos_instalados", lambda: [{"nombre": "gemma-x:1", "chat": True, "herramientas": True, "razonamiento": False, "embeddings": False},
                                                            {"nombre": "solo-embeddings", "chat": False, "herramientas": False, "razonamiento": False, "embeddings": True}])
    assert client.put(f"{A}/config-ia", headers=admin, json={"llm_modelo": "no-existe:1"}).json()["error"]["codigo"] == "modelo_no_instalado"
    assert client.put(f"{A}/config-ia", headers=admin, json={"llm_modelo": "solo-embeddings"}).status_code == 422  # no sirve para conversar
    assert client.put(f"{A}/config-ia", headers=admin, json={"llm_modelo": "gemma-x:1"}).json()["efectiva"]["llm_modelo"] == "gemma-x:1"


def test_solo_el_administrador_toca_la_configuracion_de_ia(client, db):
    for rol in (Rol.COORDINADOR, Rol.RECEPCIONISTA, Rol.PROFESIONAL_SALUD, Rol.CONSULTA):
        crear_usuario(db, email=f"{rol.value}@ejemplo.com", rol=rol)
        h = cabecera(client, f"{rol.value}@ejemplo.com")
        assert client.get(f"{A}/config-ia", headers=h).status_code == 403
        assert client.put(f"{A}/config-ia", headers=h, json={"llm_temperatura": 0.9}).status_code == 403
        assert client.post(f"{A}/config-ia/probar", headers=h).status_code == 403
    assert client.get(f"{A}/config-ia").status_code == 401


def test_los_cambios_quedan_auditados_sin_valores(client, db, admin):
    client.put(f"{A}/config-ia", headers=admin, json={"mensaje_bienvenida": "Texto de bienvenida de prueba"})
    r = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "config_ia_actualizada")).one()
    assert r.detalle == {"claves": ["mensaje_bienvenida"]}
    assert db.get(ConfiguracionIA, "mensaje_bienvenida").valor == {"v": "Texto de bienvenida de prueba"}


def test_probar_conexion_exitosa_y_fallida(client, admin, monkeypatch):
    from app.llm.tipos import RespuestaLLM, Trozo

    class Falso:
        def chat_stream(self, mensajes, herramientas=None, opciones=None):
            yield Trozo("listo")
            yield Trozo(final=RespuestaLLM("listo", [], "modelo-falso", 20, 20, 1120, 120))  # 20 tokens en 1 s tras el primer texto

        def cerrar(self):
            pass

    monkeypatch.setattr(modulo_cliente, "obtener_cliente", lambda modelo=None: Falso())
    r = client.post(f"{A}/config-ia/probar", headers=admin).json()
    assert r["ok"] is True and r["modelo"] == "modelo-falso" and r["ttft_ms"] == 120 and r["tokens_por_segundo"] == 20.0

    def roto(modelo=None):
        raise ErrorNegocio("llm_no_disponible", "El modelo de lenguaje no está disponible: sin conexión.", 503)

    monkeypatch.setattr(modulo_cliente, "obtener_cliente", roto)
    r = client.post(f"{A}/config-ia/probar", headers=admin).json()
    assert r["ok"] is False and "no está disponible" in r["error"]


# ======================= con el modelo real =======================
@pytest.fixture
def ollama_llm():
    if not indexacion_service._ollama_ok():
        pytest.skip("Ollama no está disponible")
    if not any(m["nombre"] == get_settings().llm_model for m in cfg.modelos_instalados()):
        pytest.skip(f"El modelo {get_settings().llm_model} no está instalado")


@pytest.mark.ollama
def test_modelo_real_responde_en_streaming_y_rapido(ollama_llm):
    c = modulo_cliente.ClienteLLM()
    trozos = list(c.chat_stream([Mensaje.sistema("Eres la recepcionista de una institución de salud. Responde en español en una frase."), Mensaje.usuario("Buenos días")]))
    final = trozos[-1].final
    assert final.contenido.strip() and final.ttft_ms is not None and final.ttft_ms < 4000 and final.duracion_ms < 15000, (final.ttft_ms, final.duracion_ms)


@pytest.mark.ollama
def test_modelo_real_sin_razonamiento_es_rapido_y_sin_el_responde_tambien(ollama_llm):
    c = modulo_cliente.ClienteLLM()
    r = c.chat([Mensaje.usuario("Responde solo con la palabra: listo")], opciones=Opciones(0.0, 20, False))
    assert "listo" in r.contenido.lower() and r.duracion_ms < 8000


@pytest.mark.ollama
def test_modelo_real_llama_a_una_herramienta_con_ejemplos(ollama_llm):
    ejemplo = [Mensaje.usuario("quiero cancelar la cita AB12CD34"), Mensaje.asistente("", [LlamadaHerramienta("cancelar_cita", {"codigo_reserva": "AB12CD34"})]),
               Mensaje.herramienta("cancelar_cita", '{"ok": true}'), Mensaje.asistente("Listo, su cita quedó cancelada.")]
    c = modulo_cliente.ClienteLLM()
    r = c.chat([Mensaje.sistema("Eres la recepcionista de una institución de salud. Usa las herramientas cuando corresponda.")] + ejemplo + [Mensaje.usuario("cancela mi cita K7M2QXD9 por favor")],
               herramientas=HERRAMIENTAS, opciones=Opciones(0.0, 200, False))
    assert r.llamadas and r.llamadas[0].nombre == "cancelar_cita" and r.llamadas[0].argumentos.get("codigo_reserva") == "K7M2QXD9", r


@pytest.mark.ollama
def test_modelo_real_salida_estructurada_valida(ollama_llm):
    esquema = {"type": "object", "properties": {"intencion": {"type": "string", "enum": ["saludo", "cancelar", "otra"]}}, "required": ["intencion"]}
    c = modulo_cliente.ClienteLLM()
    r = c.chat([Mensaje.sistema("Clasifica la intención del usuario."), Mensaje.usuario("hola, buenas")], opciones=Opciones(0.0, 60, False, 4096, esquema))
    assert json.loads(r.contenido)["intencion"] in {"saludo", "cancelar", "otra"}

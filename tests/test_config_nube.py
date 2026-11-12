"""IA en la nube desde el panel (solo la clave de API + probar conexión) y modelo de embeddings Gemma elegible.
El «proveedor» es un servidor HTTP falso en este mismo equipo que habla el protocolo OpenAI (se permite http solo hacia 127.0.0.1)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.llm import proveedores
from app.llm.cliente import ClienteLLM
from app.llm.tipos import Mensaje, Opciones
from app.models import AuditoriaLog, ConfiguracionIA
from app.models.enums import Rol
from app.security import crypto
from app.services import config_ia_service as cfg
from app.services import indexacion_service
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"
CLAVE = "sk-prueba-0123456789abcdef"


class ProveedorFalso:
    """Servidor OpenAI-compatible mínimo: /models, /chat/completions (con y sin streaming). Recuerda lo que recibió."""

    def __init__(self):
        self.peticiones, self.clave_valida, self.modelos = [], CLAVE, ["gpt-prueba", "text-embedding-prueba", "whisper-prueba", "models/otro-chat"]
        self.error_chat: tuple[int, dict] | None = None
        outer = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencio
                pass

            def _autorizado(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {outer.clave_valida}"

            def _responder(self, estado: int, cuerpo: dict):
                datos = json.dumps(cuerpo).encode()
                self.send_response(estado)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            def do_GET(self):
                outer.peticiones.append(("GET", self.path, None))
                if not self._autorizado():
                    return self._responder(401, {"error": {"message": "clave incorrecta"}})
                self._responder(200, {"data": [{"id": m} for m in outer.modelos]})

            def do_POST(self):
                cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.peticiones.append(("POST", self.path, cuerpo))
                if not self._autorizado():
                    return self._responder(401, {"error": {"message": "clave incorrecta"}})
                if outer.error_chat:
                    return self._responder(*outer.error_chat)
                if not cuerpo.get("stream"):
                    return self._responder(200, {"model": cuerpo["model"], "choices": [{"message": {"content": "Hola, soy la recepcionista."}}], "usage": {"prompt_tokens": 5, "completion_tokens": 6}})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for trozo in ("Hola, ", "soy la recepcionista."):
                    self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': trozo}}]})}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.url = f"http://127.0.0.1:{self.servidor.server_port}/v1"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def cerrar(self):
        self.servidor.shutdown()
        self.servidor.server_close()


@pytest.fixture
def nube():
    p = ProveedorFalso()
    yield p
    p.cerrar()


@pytest.fixture(autouse=True)
def ajustes_limpios():
    """Guardar la nube cambia los ajustes del proceso: se deshace al terminar cada prueba."""
    yield
    cfg.restaurar_ajustes()


@pytest.fixture
def admin(client, db):
    crear_usuario(db, email="admin@ejemplo.com", rol=Rol.ADMINISTRADOR)
    return cabecera(client, "admin@ejemplo.com")


def cuerpo_nube(nube, **extra) -> dict:
    return {"proveedor": "otro", "direccion": nube.url, "api_key": CLAVE, **extra}


# ======================= probar la conexión =======================
def test_probar_con_una_clave_buena_lista_los_modelos_de_conversacion(client, admin, nube):
    r = client.post(f"{A}/config-ia/nube/probar", headers=admin, json=cuerpo_nube(nube))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["modelo"] == "gpt-prueba"  # el primero de la lista: «otro» no sugiere ninguno
    assert d["modelos"] == ["gpt-prueba", "otro-chat"]  # sin embeddings ni audio, y sin el prefijo «models/»
    assert CLAVE not in r.text


def test_probar_con_una_clave_mala_lo_dice_sin_romper(client, admin, nube):
    r = client.post(f"{A}/config-ia/nube/probar", headers=admin, json=cuerpo_nube(nube, api_key="sk-equivocada-0000000000"))
    assert r.status_code == 200 and r.json()["ok"] is False and "rechazó la clave" in r.json()["error"]
    assert not any(m == "POST" for m, _, _ in nube.peticiones)  # ni siquiera intentó conversar


def test_probar_no_guarda_nada(client, admin, nube, db):
    client.post(f"{A}/config-ia/nube/probar", headers=admin, json=cuerpo_nube(nube))
    assert db.scalars(select(ConfiguracionIA)).all() == []
    assert get_settings().llm_provider == "ollama"


def test_un_error_del_modelo_trae_el_motivo_del_proveedor(client, admin, nube):
    nube.error_chat = (400, {"error": {"message": "El modelo no existe en tu cuenta"}})
    d = client.post(f"{A}/config-ia/nube/probar", headers=admin, json=cuerpo_nube(nube, modelo="gpt-inventado")).json()
    assert d["ok"] is False and "no existe en tu cuenta" in d["error"] and d["modelo"] == "gpt-inventado"


@pytest.mark.parametrize("cuerpo, codigo", [
    ({"proveedor": "otro", "direccion": "http://api.ejemplo.com/v1", "api_key": CLAVE}, "base_insegura"),
    ({"proveedor": "otro", "direccion": "https://10.0.0.5/v1", "api_key": CLAVE}, "base_interna"),
    ({"proveedor": "otro", "direccion": "https://169.254.169.254/latest", "api_key": CLAVE}, "base_interna"),
    ({"proveedor": "otro", "direccion": "https://usuario:clave@api.ejemplo.com/v1", "api_key": CLAVE}, "base_invalida"),
    ({"proveedor": "otro", "api_key": CLAVE}, "base_requerida"),
    ({"proveedor": "inventado", "api_key": CLAVE}, "proveedor_desconocido"),
    ({"proveedor": "openai"}, "falta_clave"),
    ({"proveedor": "openai", "api_key": "sk con espacios dentro"}, "clave_invalida"),
])
def test_entradas_peligrosas_o_incompletas_se_rechazan(client, admin, cuerpo, codigo):
    r = client.post(f"{A}/config-ia/nube/probar", headers=admin, json=cuerpo)
    assert r.status_code == 422 and r.json()["error"]["codigo"] == codigo


def test_los_proveedores_de_la_lista_apuntan_a_https():
    for p in proveedores.PROVEEDORES.values():
        if p.base:
            assert proveedores.base_efectiva(p, None).startswith("https://")


# ======================= guardar y usar =======================
def test_sin_la_confirmacion_no_se_activa_la_nube(client, admin, nube):
    r = client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube))
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "falta_autorizacion"
    assert get_settings().llm_provider == "ollama"


def test_guardar_cifra_la_clave_y_pone_al_asistente_en_la_nube(client, admin, nube, db):
    r = client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    assert r.status_code == 200, r.text
    # lo que ve el panel: nunca la clave completa
    assert CLAVE not in r.text and client.get(f"{A}/config-ia", headers=admin).text.count(CLAVE) == 0
    otro = next(p for p in r.json()["nube"]["proveedores"] if p["id"] == "otro")
    assert otro["clave_guardada"] and otro["clave_ultimos4"] == CLAVE[-4:]
    assert r.json()["nube"]["activo"] == "otro" and r.json()["nube"]["modelo"] == "gpt-prueba"
    # lo que hay en la base: cifrada
    fila = db.get(ConfiguracionIA, "nube_clave:otro")
    assert CLAVE not in json.dumps(fila.valor) and crypto.descifrar(fila.valor["v"]) == CLAVE
    # lo que usa el proceso
    s = get_settings()
    assert (s.llm_provider, s.llm_api_base, s.llm_api_key, s.llm_allow_external) == ("openai_compatible", nube.url, CLAVE, True)
    assert cfg.efectiva(db).modelo_activo == "gpt-prueba"


def test_con_la_nube_activa_el_cliente_conversa_con_el_proveedor(client, admin, nube, db):
    client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    from app.llm import cliente

    c = cliente.obtener_cliente(cfg.efectiva(db).modelo_activo)
    r = c.chat([Mensaje.usuario("Hola")], opciones=Opciones(0.2, 50))
    c.cerrar()
    assert r.contenido == "Hola, soy la recepcionista."
    peticion = [p for p in nube.peticiones if p[0] == "POST"][-1]
    assert peticion[1] == "/v1/chat/completions" and peticion[2]["model"] == "gpt-prueba"


def test_si_la_conexion_falla_no_se_guarda_ni_se_cambia_nada(client, admin, nube, db):
    r = client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, api_key="sk-equivocada-0000000000", autoriza=True))
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "conexion_fallida"
    assert db.get(ConfiguracionIA, "nube_clave:otro") is None and db.get(ConfiguracionIA, "llm_proveedor") is None
    assert get_settings().llm_provider == "ollama"


def test_la_auditoria_registra_la_activacion_sin_la_clave(client, admin, nube, db):
    client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    r = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "config_ia_nube_activada")).one()
    assert r.detalle == {"proveedor": "otro", "modelo": "gpt-prueba"}
    assert CLAVE not in json.dumps([a.detalle for a in db.scalars(select(AuditoriaLog))], default=str)


def test_con_la_clave_ya_guardada_se_puede_probar_y_cambiar_de_modelo_sin_pegarla(client, admin, nube):
    client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    r = client.post(f"{A}/config-ia/nube/probar", headers=admin, json={"proveedor": "otro", "direccion": nube.url, "modelo": "otro-chat"})
    assert r.json()["ok"] and r.json()["modelo"] == "otro-chat"
    assert client.put(f"{A}/config-ia/nube", headers=admin, json={"proveedor": "otro", "direccion": nube.url, "modelo": "otro-chat", "autoriza": True}).json()["nube"]["modelo"] == "otro-chat"


def test_volver_al_modelo_local_conserva_la_clave_y_se_puede_borrar_despues(client, admin, nube, db):
    client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    assert client.delete(f"{A}/config-ia/nube/otro/clave", headers=admin).status_code == 409  # en uso
    r = client.post(f"{A}/config-ia/nube/usar-local", headers=admin)
    assert r.status_code == 200 and r.json()["nube"]["activo"] == "ollama" and get_settings().llm_provider == "ollama"
    assert get_settings().llm_api_key == "" and get_settings().llm_allow_external is False
    assert db.get(ConfiguracionIA, "nube_clave:otro") is not None
    assert client.delete(f"{A}/config-ia/nube/otro/clave", headers=admin).status_code == 200
    assert db.get(ConfiguracionIA, "nube_clave:otro") is None


def test_una_clave_que_no_se_puede_descifrar_no_activa_la_nube(db):
    db.add(ConfiguracionIA(clave="nube_clave:openai", valor={"v": "no-es-un-token-fernet"}))
    db.add(ConfiguracionIA(clave="llm_proveedor", valor={"v": "openai"}))
    db.add(ConfiguracionIA(clave="llm_nube_autorizada", valor={"v": True}))
    db.flush()
    cfg.aplicar_a_ajustes(db)
    assert get_settings().llm_provider == "ollama" and get_settings().llm_api_key == ""


def test_sin_autorizacion_guardada_no_se_usa_la_nube_aunque_haya_clave(db):
    db.add(ConfiguracionIA(clave="nube_clave:openai", valor={"v": crypto.cifrar(CLAVE)}))
    db.add(ConfiguracionIA(clave="llm_proveedor", valor={"v": "openai"}))
    db.flush()
    cfg.aplicar_a_ajustes(db)
    assert get_settings().llm_provider == "ollama"


def test_la_ruta_generica_no_puede_activar_la_nube_ni_poner_la_clave(client, admin):
    for peligroso in ({"llm_proveedor": "openai"}, {"llm_nube_autorizada": True}, {"llm_api_key": CLAVE}, {"llm_api_base": "https://x.com/v1"}, {"embedding_modelo": "otro"}):
        assert client.put(f"{A}/config-ia", headers=admin, json=peligroso).status_code == 422


def test_solo_el_administrador_toca_la_nube_y_los_embeddings(client, db):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    h = cabecera(client, "coord@ejemplo.com")
    llamadas = [("post", "/config-ia/nube/probar", {"proveedor": "openai", "api_key": CLAVE}), ("put", "/config-ia/nube", {"proveedor": "openai", "api_key": CLAVE, "autoriza": True}),
                ("post", "/config-ia/nube/usar-local", None), ("delete", "/config-ia/nube/openai/clave", None),
                ("post", "/config-ia/embeddings/probar", {"modelo": "embeddinggemma:latest"}), ("put", "/config-ia/embeddings", {"modelo": "embeddinggemma:latest"})]
    for metodo, ruta, cuerpo in llamadas:
        r = getattr(client, metodo)(f"{A}{ruta}", headers=h, **({"json": cuerpo} if cuerpo else {}))
        assert r.status_code == 403, (metodo, ruta)


# ======================= cliente: detalles de los proveedores =======================
def _peticion(base: str, modelo: str):
    c = ClienteLLM(Settings(_env_file=None, llm_provider="openai_compatible", llm_api_base=base, llm_allow_external=True, llm_api_key="k"), modelo=modelo)
    return c._peticion([Mensaje.usuario("hola")], None, Opciones(0.3, 77), False)[1]


def test_openai_usa_max_completion_tokens_y_los_modelos_de_razonamiento_no_llevan_temperatura():
    normal = _peticion("https://api.openai.com/v1", "gpt-4o-mini")
    assert normal["max_completion_tokens"] == 77 and normal["temperature"] == 0.3 and "max_tokens" not in normal
    for modelo in ("gpt-5", "gpt-5-mini", "o3-mini", "o1"):
        assert "temperature" not in _peticion("https://api.openai.com/v1", modelo)
    otro = _peticion("https://api.groq.com/openai/v1", "llama")
    assert otro["max_tokens"] == 77 and otro["temperature"] == 0.3 and "max_completion_tokens" not in otro


def test_los_modelos_de_conversacion_se_separan_de_los_demas():
    assert proveedores.solo_chat(["models/gemini-x", "text-embedding-3-small", "whisper-1", "gpt-4o", "gpt-4o", "dall-e-3", "tts-1", "omni-moderation-latest"]) == ["gemini-x", "gpt-4o"]


# ======================= embeddings Gemma =======================
MODELOS = [
    {"nombre": "embeddinggemma:latest", "chat": False, "herramientas": False, "razonamiento": False, "embeddings": True},
    {"nombre": "embeddinggemma-2:latest", "chat": False, "herramientas": False, "razonamiento": False, "embeddings": True},
    {"nombre": "nomic-embed-text:latest", "chat": False, "herramientas": False, "razonamiento": False, "embeddings": True},
    {"nombre": "gemma4:e4b-it-qat", "chat": True, "herramientas": True, "razonamiento": False, "embeddings": False},
]


@pytest.fixture
def ollama_falso(monkeypatch):
    monkeypatch.setattr(cfg, "modelos_instalados", lambda: MODELOS)
    dims = {"embeddinggemma:latest": 768, "embeddinggemma-2:latest": 768, "embeddinggemma-grande:latest": 1024}
    monkeypatch.setattr(cfg.OllamaEmbeddings, "embed_consulta", lambda self, texto: [0.1] * dims.get(self.s.embedding_model, 768))
    lanzados = []
    monkeypatch.setattr(indexacion_service, "iniciar_reindexado", lambda db, solo_pendientes=True: lanzados.append(solo_pendientes) or {})
    return lanzados


def test_solo_se_ofrecen_los_embeddings_de_la_familia_gemma(client, admin, ollama_falso):
    e = client.get(f"{A}/config-ia", headers=admin).json()["embedding"]
    assert [m["nombre"] for m in e["instalados"]] == ["embeddinggemma:latest", "embeddinggemma-2:latest"]
    assert e["modelo"] == "embeddinggemma:latest" and e["dimensiones"] == 768


def test_probar_un_embedding_informa_dimensiones_y_umbral(client, admin, ollama_falso):
    d = client.post(f"{A}/config-ia/embeddings/probar", headers=admin, json={"modelo": "embeddinggemma-2:latest"}).json()
    assert d["ok"] and d["dimensiones"] == 768 and d["umbral"] == 0.71
    assert client.post(f"{A}/config-ia/embeddings/probar", headers=admin, json={"modelo": "embeddinggemma:latest"}).json()["umbral"] == 0.30
    malo = client.post(f"{A}/config-ia/embeddings/probar", headers=admin, json={"modelo": "embeddinggemma-grande:latest"}).json()
    assert malo["ok"] is False and "1024" in malo["error"] and "768" in malo["error"]


def test_cambiar_de_modelo_reindexa_y_usa_su_propio_umbral(client, admin, ollama_falso, db):
    r = client.put(f"{A}/config-ia/embeddings", headers=admin, json={"modelo": "embeddinggemma-2:latest"})
    assert r.status_code == 200, r.text
    assert r.json()["reindexando"] is True and ollama_falso == [True]
    assert get_settings().embedding_model == "embeddinggemma-2:latest" and get_settings().umbral_rag() == 0.71
    assert r.json()["embedding"]["modelo"] == "embeddinggemma-2:latest"
    # volver al de siempre borra la preferencia
    client.put(f"{A}/config-ia/embeddings", headers=admin, json={"modelo": "embeddinggemma:latest"})
    assert db.get(ConfiguracionIA, "embedding_modelo") is None and get_settings().umbral_rag() == 0.30


def test_el_mismo_modelo_no_reindexa(client, admin, ollama_falso):
    assert client.put(f"{A}/config-ia/embeddings", headers=admin, json={"modelo": "embeddinggemma:latest"}).json()["reindexando"] is False
    assert ollama_falso == []


@pytest.mark.parametrize("modelo", ["nomic-embed-text:latest", "gemma4:e4b-it-qat", "no-existe:1"])
def test_un_modelo_que_no_es_gemma_de_embeddings_se_rechaza(client, admin, ollama_falso, modelo):
    r = client.put(f"{A}/config-ia/embeddings", headers=admin, json={"modelo": modelo})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "modelo_no_instalado"


def test_un_modelo_con_otras_dimensiones_no_se_activa(client, admin, ollama_falso, monkeypatch):
    monkeypatch.setattr(cfg, "modelos_instalados", lambda: MODELOS + [{"nombre": "embeddinggemma-grande:latest", "chat": False, "herramientas": False, "razonamiento": False, "embeddings": True}])
    r = client.put(f"{A}/config-ia/embeddings", headers=admin, json={"modelo": "embeddinggemma-grande:latest"})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "embedding_no_valido"
    assert get_settings().embedding_model == "embeddinggemma:latest"


def test_el_formato_de_consulta_de_gemma_solo_se_aplica_a_gemma():
    from app.embeddings.cliente import OllamaEmbeddings

    gemma = OllamaEmbeddings(Settings(_env_file=None, embedding_model="embeddinggemma-2:latest"))
    otro = OllamaEmbeddings(Settings(_env_file=None, embedding_model="nomic-embed-text:latest"))
    assert gemma._formato_consulta("hola").startswith("task: search result | query:")
    assert otro._formato_consulta("hola") == "hola" and otro._formato_documento("texto") == "texto"


def test_con_la_nube_activa_el_agente_pide_a_ese_proveedor_el_modelo_de_la_nube(client, admin, nube, db):
    """El modelo local (gemma…) no existe en el proveedor: el agente debe usar el modelo elegido para la nube."""
    from app.agents.sesiones import SESIONES
    from app.models.enums import CanalConversacion
    from app.services import agente_service

    client.put(f"{A}/config-ia/nube", headers=admin, json=cuerpo_nube(nube, autoriza=True))
    conv, _ = agente_service.iniciar(db, CanalConversacion.TEXTO, es_prueba=True)
    try:
        _, r = agente_service.enviar(db, conv.id, "Cuéntame algo sobre cómo funciona la institución")
    finally:
        SESIONES.quitar(conv.id)
    chats = [p[2] for p in nube.peticiones if p[0] == "POST" and p[1].endswith("/chat/completions") and len(p[2].get("messages", [])) > 2]  # sin la prueba de conexión
    assert chats and all(c["model"] == "gpt-prueba" for c in chats)
    assert r.texto

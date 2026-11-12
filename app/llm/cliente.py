"""Cliente LLM: Ollama (por defecto, local) o cualquier API compatible con OpenAI (opcional y solo si se habilita).

Nunca registra en logs el contenido de los mensajes: solo métricas (modelo, tokens, milisegundos).
"""
import json
import logging
import re
import time
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.llm.tipos import LlamadaHerramienta, Mensaje, Opciones, RespuestaLLM, Trozo

log = logging.getLogger("llm")


def _no_disponible(detalle: str, status: int = 503) -> ErrorNegocio:
    return ErrorNegocio("llm_no_disponible", f"El modelo de lenguaje no está disponible: {detalle}", status)


class ClienteLLM:
    def __init__(self, settings: Settings | None = None, modelo: str | None = None, transporte: httpx.BaseTransport | None = None):
        self.s = settings or get_settings()
        self.modelo = modelo or self.s.llm_model
        self.externo = self.s.llm_provider == "openai_compatible"
        if self.externo:
            if not self.s.llm_allow_external:
                raise ErrorNegocio("llm_externo_deshabilitado", "El uso de una API externa de lenguaje está deshabilitado (LLM_ALLOW_EXTERNAL). "
                                   "Enviaría las conversaciones fuera de la institución y requiere autorización previa.", 403)
            if not self.s.llm_api_base:
                raise ErrorNegocio("llm_mal_configurado", "Falta LLM_API_BASE para el proveedor externo.", 500)
        self._http = httpx.Client(transport=transporte, timeout=self.s.llm_timeout_seconds)

    # ---------- construcción de la petición ----------
    def _mensajes(self, mensajes: list[Mensaje]) -> list[dict]:
        salida = []
        for m in mensajes:
            if m.rol == "assistant" and m.llamadas:
                if self.externo:
                    llamadas = [{"id": c.id or f"call_{i}", "type": "function", "function": {"name": c.nombre, "arguments": json.dumps(c.argumentos, ensure_ascii=False)}}
                                for i, c in enumerate(m.llamadas)]
                else:
                    llamadas = [{"function": {"name": c.nombre, "arguments": c.argumentos}} for c in m.llamadas]
                salida.append({"role": "assistant", "content": m.contenido, "tool_calls": llamadas})
            elif m.rol == "tool":
                salida.append({"role": "tool", "content": m.contenido, **({"tool_call_id": m.id} if self.externo else {"tool_name": m.nombre})})
            else:
                salida.append({"role": m.rol, "content": m.contenido})
        return salida

    def _peticion(self, mensajes: list[Mensaje], herramientas: list[dict] | None, o: Opciones, stream: bool) -> tuple[str, dict, dict]:
        if self.externo:
            cuerpo: dict = {"model": self.modelo, "messages": self._mensajes(mensajes), "stream": stream}
            if urlparse(self.s.llm_api_base).hostname == "api.openai.com":
                # OpenAI renombró el límite y sus modelos de razonamiento (o1, o3, gpt-5…) solo aceptan la temperatura por defecto.
                cuerpo["max_completion_tokens"] = o.max_tokens
                if not re.match(r"(o\d|gpt-5)", self.modelo):
                    cuerpo["temperature"] = o.temperatura
            else:
                cuerpo.update(temperature=o.temperatura, max_tokens=o.max_tokens)
            if herramientas:
                cuerpo["tools"] = herramientas
            if o.esquema_json:
                cuerpo["response_format"] = {"type": "json_schema", "json_schema": {"name": "respuesta", "schema": o.esquema_json}}
            cabeceras = {"Authorization": f"Bearer {self.s.llm_api_key}"} if self.s.llm_api_key else {}
            return f"{self.s.llm_api_base.rstrip('/')}/chat/completions", cuerpo, cabeceras
        cuerpo = {"model": self.modelo, "messages": self._mensajes(mensajes), "stream": stream, "think": o.razonamiento,
                  "options": {"temperature": o.temperatura, "num_predict": o.max_tokens, "num_ctx": o.num_ctx}}
        if self.s.llm_keep_alive and self.s.llm_keep_alive != "0":
            cuerpo["keep_alive"] = self.s.llm_keep_alive
        if herramientas:
            cuerpo["tools"] = herramientas
        if o.esquema_json:
            cuerpo["format"] = o.esquema_json
        return f"{self.s.ollama_base_url}/api/chat", cuerpo, {}

    # ---------- errores ----------
    def _revisar(self, r: httpx.Response) -> None:
        if r.status_code == 200:
            return
        if r.status_code == 404:
            raise _no_disponible(f"el modelo «{self.modelo}» no está instalado (ollama pull {self.modelo})" if not self.externo else f"endpoint o modelo «{self.modelo}» no encontrado{self._detalle(r)}", 503)
        if r.status_code in (401, 403):
            raise _no_disponible("el proveedor rechazó las credenciales", 503)
        if r.status_code == 429:
            raise _no_disponible("el proveedor está limitando las peticiones; intenta en unos segundos", 503)
        raise _no_disponible(f"el proveedor respondió {r.status_code}{self._detalle(r) if r.status_code in (400, 402, 422) else ''}", 503)

    def _detalle(self, r: httpx.Response) -> str:
        """El motivo que da el proveedor externo (p. ej. «el modelo no existe», «sin saldo»); es un texto del proveedor, nunca nuestro contenido."""
        if not self.externo:
            return ""
        try:
            d = r.json()
            d = d[0] if isinstance(d, list) and d else d
            texto = d.get("error", {}).get("message") if isinstance(d.get("error"), dict) else d.get("error") or d.get("message")
        except (ValueError, AttributeError):
            return ""
        return f": {str(texto)[:200]}" if texto else ""

    # ---------- respuestas ----------
    @staticmethod
    def _argumentos(a) -> dict:
        if isinstance(a, dict):
            return a
        try:
            v = json.loads(a or "{}")
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _llamadas(self, crudas: list[dict] | None) -> list[LlamadaHerramienta]:
        return [LlamadaHerramienta(nombre=c["function"]["name"], argumentos=self._argumentos(c["function"].get("arguments")), id=c.get("id"))
                for c in (crudas or []) if c.get("function", {}).get("name")]

    def chat(self, mensajes: list[Mensaje], herramientas: list[dict] | None = None, opciones: Opciones | None = None) -> RespuestaLLM:
        o = opciones or self.opciones_por_defecto()
        url, cuerpo, cabeceras = self._peticion(mensajes, herramientas, o, stream=False)
        t0 = time.perf_counter()
        try:
            r = self._http.post(url, json=cuerpo, headers=cabeceras)
        except httpx.TimeoutException as exc:
            raise _no_disponible("tardó demasiado en responder", 504) from exc
        except httpx.HTTPError as exc:
            raise _no_disponible(f"no hay conexión ({type(exc).__name__})") from exc
        self._revisar(r)
        try:
            d = r.json()
            if self.externo:
                msg = d["choices"][0]["message"]
                uso = d.get("usage", {})
                resp = RespuestaLLM(msg.get("content") or "", self._llamadas(msg.get("tool_calls")), d.get("model", self.modelo), uso.get("prompt_tokens"), uso.get("completion_tokens"), 0)
            else:
                msg = d["message"]
                resp = RespuestaLLM(msg.get("content") or "", self._llamadas(msg.get("tool_calls")), d.get("model", self.modelo), d.get("prompt_eval_count"), d.get("eval_count"), 0)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _no_disponible("respuesta con formato inesperado", 502) from exc
        resp.duracion_ms = int((time.perf_counter() - t0) * 1000)
        self._registrar(resp, bool(resp.llamadas))
        return resp

    def chat_stream(self, mensajes: list[Mensaje], herramientas: list[dict] | None = None, opciones: Opciones | None = None) -> Iterator[Trozo]:
        o = opciones or self.opciones_por_defecto()
        url, cuerpo, cabeceras = self._peticion(mensajes, herramientas, o, stream=True)
        t0 = time.perf_counter()
        ttft: int | None = None
        texto: list[str] = []
        llamadas: list[LlamadaHerramienta] = []
        parciales: dict[int, dict] = {}  # OpenAI manda los argumentos de una llamada en trozos
        modelo, t_in, t_out = self.modelo, None, None
        try:
            with self._http.stream("POST", url, json=cuerpo, headers=cabeceras) as r:
                self._revisar_stream(r)
                for linea in r.iter_lines():
                    if not linea or linea.startswith(":"):
                        continue
                    if self.externo:
                        if not linea.startswith("data:"):
                            continue
                        carga = linea[5:].strip()
                        if carga == "[DONE]":
                            break
                        d = json.loads(carga)
                        if not d.get("choices"):
                            t_in, t_out = (d.get("usage") or {}).get("prompt_tokens", t_in), (d.get("usage") or {}).get("completion_tokens", t_out)
                            continue
                        delta = d["choices"][0].get("delta", {})
                        pedazo = delta.get("content") or ""
                        for c in delta.get("tool_calls") or []:
                            p = parciales.setdefault(c.get("index", 0), {"id": c.get("id"), "nombre": "", "args": ""})
                            p["nombre"] += c.get("function", {}).get("name") or ""
                            p["args"] += c.get("function", {}).get("arguments") or ""
                    else:
                        d = json.loads(linea)
                        pedazo = d.get("message", {}).get("content") or ""
                        llamadas += self._llamadas(d.get("message", {}).get("tool_calls"))
                        if d.get("done"):
                            modelo, t_in, t_out = d.get("model", modelo), d.get("prompt_eval_count"), d.get("eval_count")
                    if pedazo:
                        if ttft is None:
                            ttft = int((time.perf_counter() - t0) * 1000)
                        texto.append(pedazo)
                        yield Trozo(texto=pedazo)
        except httpx.TimeoutException as exc:
            raise _no_disponible("tardó demasiado en responder", 504) from exc
        except httpx.HTTPError as exc:
            raise _no_disponible(f"no hay conexión ({type(exc).__name__})") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _no_disponible("respuesta con formato inesperado", 502) from exc
        for p in parciales.values():
            llamadas.append(LlamadaHerramienta(p["nombre"], self._argumentos(p["args"]), p["id"]))
        final = RespuestaLLM("".join(texto), llamadas, modelo, t_in, t_out, int((time.perf_counter() - t0) * 1000), ttft)
        self._registrar(final, bool(llamadas))
        yield Trozo(final=final)

    def _revisar_stream(self, r: httpx.Response) -> None:
        if r.status_code != 200:
            r.read()
        self._revisar(r)

    def opciones_por_defecto(self) -> Opciones:
        return Opciones(self.s.llm_temperatura, self.s.llm_max_tokens, self.s.llm_razonamiento, self.s.llm_num_ctx)

    def _registrar(self, r: RespuestaLLM, con_herramienta: bool) -> None:
        log.info("llm modelo=%s tokens_in=%s tokens_out=%s ms=%s ttft_ms=%s herramienta=%s", r.modelo, r.tokens_entrada, r.tokens_salida, r.duracion_ms, r.ttft_ms, con_herramienta)

    def cerrar(self) -> None:
        self._http.close()


def obtener_cliente(modelo: str | None = None) -> ClienteLLM:
    """Punto único para crear el cliente; las pruebas lo reemplazan por uno falso."""
    return ClienteLLM(modelo=modelo)

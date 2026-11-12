"""Cliente de embeddings (Ollama + EmbeddingGemma). Verifica en cada respuesta que la dimensión sea la configurada."""
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio


class Embedder(Protocol):
    def embed_documentos(self, textos: list[str]) -> list[list[float]]: ...
    def embed_consulta(self, consulta: str) -> list[float]: ...


def _no_disponible(detalle: str) -> ErrorNegocio:
    return ErrorNegocio("embeddings_no_disponible", f"El servicio de embeddings no está disponible: {detalle}", 503)


class OllamaEmbeddings:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    def _con_prompts(self) -> bool:
        return self.s.embedding_usar_prompts and "embeddinggemma" in self.s.embedding_model  # el formato es de la familia EmbeddingGemma

    def _formato_documento(self, texto: str) -> str:
        return f"title: none | text: {texto}" if self._con_prompts() else texto

    def _formato_consulta(self, consulta: str) -> str:
        return f"task: search result | query: {consulta}" if self._con_prompts() else consulta

    def _embed(self, textos: list[str]) -> list[list[float]]:
        salida: list[list[float]] = []
        for i in range(0, len(textos), self.s.embedding_batch_size):
            lote = textos[i:i + self.s.embedding_batch_size]
            try:
                r = httpx.post(f"{self.s.ollama_base_url}/api/embed", json={"model": self.s.embedding_model, "input": lote, **({"keep_alive": self.s.llm_keep_alive} if self.s.llm_keep_alive and self.s.llm_keep_alive != "0" else {})}, timeout=self.s.llm_timeout_seconds)
            except httpx.HTTPError as exc:
                raise _no_disponible(f"no hay conexión con Ollama ({type(exc).__name__})") from exc
            if r.status_code == 404:
                raise _no_disponible(f"el modelo «{self.s.embedding_model}» no está instalado (ollama pull {self.s.embedding_model})")
            if r.status_code != 200:
                raise _no_disponible(f"Ollama respondió {r.status_code}")
            vectores = r.json().get("embeddings", [])
            if len(vectores) != len(lote):
                raise _no_disponible("la respuesta no trae un vector por texto")
            for v in vectores:
                if len(v) != self.s.embedding_dimensions:
                    raise ErrorNegocio("dimension_incorrecta", f"El modelo devolvió vectores de {len(v)} dimensiones y la base espera {self.s.embedding_dimensions}. "
                                       "Revisa EMBEDDING_MODEL / EMBEDDING_DIMENSIONS (cambiar de dimensión exige migrar la columna y reindexar).", 500)
            salida.extend(vectores)
        return salida

    def embed_documentos(self, textos: list[str]) -> list[list[float]]:
        return self._embed([self._formato_documento(t) for t in textos])

    def embed_consulta(self, consulta: str) -> list[float]:
        return self._embed([self._formato_consulta(consulta)])[0]


def obtener_embedder() -> Embedder:
    """Punto único para obtener el cliente; las pruebas lo reemplazan por uno falso."""
    return OllamaEmbeddings()

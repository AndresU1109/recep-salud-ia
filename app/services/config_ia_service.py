"""Configuración de IA editable desde el panel. Solo estas claves, con límites: la seguridad del asistente
(reglas, herramientas permitidas) no es configurable desde aquí."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errores import ErrorNegocio
from app.embeddings.cliente import OllamaEmbeddings
from app.llm import cliente, proveedores
from app.llm.tipos import Mensaje, Opciones
from app.models import ConfiguracionIA, Usuario
from app.security import crypto
from app.services import auditoria, indexacion_service

# clave -> (tipo, mínimo, máximo)   [para str: longitud]
CLAVES = {
    "llm_modelo": (str, 1, 100),
    "llm_temperatura": (float, 0.0, 1.0),  # más de 1 vuelve errático a un asistente de recepción
    "llm_max_tokens": (int, 50, 1500),
    "llm_razonamiento": (bool, None, None),
    "nombre_asistente": (str, 2, 60),
    "mensaje_bienvenida": (str, 0, 300),
    # Versión de la política de tratamiento de datos que el asistente lee al registrar pacientes. Vacía = el asistente NO registra
    # pacientes (no se inventa una política): los deriva a una persona.
    "politica_datos_version": (str, 0, 30),
    # Proveedor de IA y modelo de embeddings. No se cambian por la ruta genérica: tienen sus propias operaciones (nube_* y embedding_*),
    # que prueban la conexión antes de guardar. La clave de API NO está aquí: va cifrada aparte (ver _clave_nube).
    "llm_proveedor": (str, 0, 30),  # vacío = el de la configuración del servidor; «ollama» o un id de proveedores.PROVEEDORES
    "llm_modelo_nube": (str, 0, 120),
    "llm_api_base": (str, 0, 200),  # solo para el proveedor «otro»
    "llm_nube_autorizada": (bool, None, None),
    "embedding_modelo": (str, 0, 100),
    # Voz del asistente (se cambia con guardar_voz, que comprueba que exista): «» = automática, «piper», «kokoro:ef_dora», «sapi».
    "frase_activacion": (str, 0, 40),  # lo que hay que decir para iniciar la llamada en el modo de espera (vacío = «hola asistente» y «hola <nombre del asistente>»)
    "encuesta_activa": (bool, None, None),  # pregunta al final de la llamada de voz cómo calificaría la atención (1 a 5)
    "voz_asistente": (str, 0, 40),
    "voz_velocidad": (float, 0.6, 1.5),
}
_PREFIJO_CLAVE = "nube_clave:"  # una fila por proveedor: «nube_clave:openai» → {"v": "<clave cifrada>"}


@dataclass
class ConfigIA:
    llm_modelo: str
    llm_temperatura: float
    llm_max_tokens: int
    llm_razonamiento: bool
    nombre_asistente: str
    mensaje_bienvenida: str
    politica_datos_version: str
    llm_proveedor: str
    llm_modelo_nube: str
    llm_api_base: str
    llm_nube_autorizada: bool
    embedding_modelo: str
    frase_activacion: str
    encuesta_activa: bool
    voz_asistente: str
    voz_velocidad: float
    personalizadas: list[str]

    @property
    def en_nube(self) -> bool:
        return self.llm_proveedor in proveedores.PROVEEDORES

    @property
    def modelo_activo(self) -> str:
        """El modelo con el que conversa el asistente ahora: el de la nube si hay proveedor en la nube; si no, el de Ollama."""
        return (self.llm_modelo_nube or proveedores.PROVEEDORES[self.llm_proveedor].modelo_sugerido) if self.en_nube else self.llm_modelo

    def opciones(self, esquema_json: dict | None = None) -> Opciones:
        return Opciones(self.llm_temperatura, self.llm_max_tokens, self.llm_razonamiento, get_settings().llm_num_ctx, esquema_json)


def por_defecto() -> dict:
    s = get_settings()
    return {"llm_modelo": s.llm_model, "llm_temperatura": s.llm_temperatura, "llm_max_tokens": s.llm_max_tokens, "llm_razonamiento": s.llm_razonamiento,
            "nombre_asistente": "Asistente virtual", "mensaje_bienvenida": "", "politica_datos_version": "",
            "llm_proveedor": "", "llm_modelo_nube": "", "llm_api_base": "", "llm_nube_autorizada": False, "embedding_modelo": "",
            "frase_activacion": "", "encuesta_activa": True, "voz_asistente": "", "voz_velocidad": s.tts_velocidad}


def efectiva(db: Session) -> ConfigIA:
    valores = por_defecto()
    guardadas = {f.clave: f.valor.get("v") for f in db.scalars(select(ConfiguracionIA)) if f.clave in CLAVES}
    valores.update({k: v for k, v in guardadas.items() if v is not None})
    return ConfigIA(**valores, personalizadas=sorted(guardadas))


def _validar(clave: str, valor):
    if clave not in CLAVES:
        raise ErrorNegocio("clave_desconocida", f"«{clave}» no es un ajuste configurable.", 422)
    tipo, minimo, maximo = CLAVES[clave]
    if tipo is bool:
        if not isinstance(valor, bool):
            raise ErrorNegocio("valor_invalido", f"«{clave}» debe ser verdadero o falso.", 422)
        return valor
    if tipo is str:
        if not isinstance(valor, str) or not minimo <= len(valor.strip()) <= maximo:
            raise ErrorNegocio("valor_invalido", f"«{clave}» debe ser texto de {minimo} a {maximo} caracteres.", 422)
        return valor.strip()
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not minimo <= valor <= maximo:
        raise ErrorNegocio("valor_invalido", f"«{clave}» debe ser un número entre {minimo} y {maximo}.", 422)
    return tipo(valor)


_CACHE_MODELOS: dict = {"hasta": 0.0, "valor": []}


def _capacidades(s: Settings, nombre: str) -> dict:
    try:
        caps = httpx.post(f"{s.ollama_base_url}/api/show", json={"model": nombre}, timeout=15).json().get("capabilities", [])
    except (httpx.HTTPError, ValueError):
        caps = []
    return {"nombre": nombre, "chat": "completion" in caps, "herramientas": "tools" in caps, "razonamiento": "thinking" in caps, "embeddings": caps == ["embedding"]}


def modelos_instalados() -> list[dict]:
    """Modelos de Ollama con sus capacidades (también con la nube activa, para poder volver). Vacío si Ollama no responde.
    Consultar las capacidades tarda ~1,5 s por modelo: se hace en paralelo y el resultado se recuerda un minuto."""
    if time.monotonic() < _CACHE_MODELOS["hasta"]:
        return _CACHE_MODELOS["valor"]
    s = get_settings()
    try:
        nombres = sorted(m["name"] for m in httpx.get(f"{s.ollama_base_url}/api/tags", timeout=4).json().get("models", []))
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    with ThreadPoolExecutor(max_workers=6) as pool:
        salida = list(pool.map(lambda n: _capacidades(s, n), nombres))
    _CACHE_MODELOS.update(hasta=time.monotonic() + 60, valor=salida)
    return salida


def actualizar(db: Session, actor: Usuario, cambios: dict) -> ConfigIA:
    """cambios: {clave: valor}; valor None = volver al valor por defecto."""
    for clave, valor in cambios.items():
        if valor is not None:
            valor = _validar(clave, valor)
        elif clave not in CLAVES:
            raise ErrorNegocio("clave_desconocida", f"«{clave}» no es un ajuste configurable.", 422)
        if clave == "llm_modelo" and valor is not None:
            instalados = modelos_instalados()
            if instalados and valor not in {m["nombre"] for m in instalados if m["chat"]}:
                raise ErrorNegocio("modelo_no_instalado", f"El modelo «{valor}» no está instalado en Ollama o no sirve para conversar.", 422)
        fila = db.get(ConfiguracionIA, clave)
        if valor is None:
            if fila:
                db.delete(fila)
        elif fila:
            fila.valor, fila.actualizado_por = {"v": valor}, actor.id
        else:
            db.add(ConfiguracionIA(clave=clave, valor={"v": valor}, actualizado_por=actor.id))
    auditoria.registrar(db, accion="config_ia_actualizada", usuario_id=actor.id, entidad="config_ia", detalle={"claves": sorted(cambios)})
    db.commit()
    return efectiva(db)


def probar(db: Session) -> dict:
    """Pide una respuesta mínima con la configuración efectiva y mide el tiempo (no usa datos de nadie)."""
    cfg = efectiva(db)
    try:
        c = cliente.obtener_cliente(cfg.modelo_activo)
        final = None
        for trozo in c.chat_stream([Mensaje.sistema("Eres la recepcionista de una institución de salud."), Mensaje.usuario("Saluda en una frase corta.")],
                                   opciones=Opciones(0.0, 40, cfg.llm_razonamiento)):
            final = trozo.final or final
        c.cerrar()
    except ErrorNegocio as e:
        return {"ok": False, "error": e.mensaje, "modelo": cfg.modelo_activo}
    # Velocidad de generación = tokens / tiempo después del primer texto (solo es fiable con una respuesta de cierto largo).
    generacion_s = (final.duracion_ms - (final.ttft_ms or 0)) / 1000
    velocidad = round(final.tokens_salida / generacion_s, 1) if final.tokens_salida and final.tokens_salida >= 8 and generacion_s > 0 else None
    return {"ok": True, "modelo": final.modelo, "respuesta": final.contenido.strip()[:80], "ttft_ms": final.ttft_ms, "duracion_ms": final.duracion_ms, "tokens_por_segundo": velocidad}



# ======================= IA en la nube =======================
# Se pega la clave de API de un proveedor y se prueba la conexión. La clave se guarda CIFRADA (Fernet, la misma clave maestra de los documentos
# de los pacientes), nunca se devuelve por la API ni se escribe en bitácoras. Activar la nube envía el texto de las conversaciones al proveedor:
# por eso se exige una confirmación expresa, que queda auditada.
def _origen() -> Settings:
    """Lo que dice el servidor (.env) antes de las preferencias guardadas en el panel."""
    return Settings()


def _clave_nube(db: Session, proveedor: str) -> str | None:
    fila = db.get(ConfiguracionIA, _PREFIJO_CLAVE + proveedor)
    if fila is None:
        return None
    try:
        return crypto.descifrar(fila.valor["v"])
    except (crypto.ErrorDeCifrado, KeyError, TypeError):
        return None  # cifrada con otra clave maestra: se trata como «no hay clave»; hay que pegarla de nuevo


def _ultimos4(clave: str | None) -> str | None:
    return clave[-4:] if clave and len(clave) >= 12 else None


def aplicar_a_ajustes(db: Session) -> None:
    """Pone en los ajustes del proceso lo elegido en el panel (proveedor de IA y modelo de embeddings), para que todo el código lo use
    sin cambiar. Sin nada guardado, vuelve a lo que dice el servidor. Se llama al arrancar y tras cada cambio."""
    s, origen, c = get_settings(), _origen(), efectiva(db)
    clave = _clave_nube(db, c.llm_proveedor) if c.en_nube else None
    if c.en_nube and clave and c.llm_nube_autorizada:
        p = proveedores.obtener(c.llm_proveedor)
        s.llm_provider, s.llm_api_base, s.llm_api_key, s.llm_allow_external = "openai_compatible", proveedores.base_efectiva(p, c.llm_api_base), clave, True
    elif c.llm_proveedor == "ollama":
        s.llm_provider, s.llm_api_base, s.llm_api_key, s.llm_allow_external = "ollama", origen.llm_api_base, origen.llm_api_key, origen.llm_allow_external
    else:  # sin preferencia (o nube sin clave utilizable): lo del servidor
        s.llm_provider, s.llm_api_base, s.llm_api_key, s.llm_allow_external = origen.llm_provider, origen.llm_api_base, origen.llm_api_key, origen.llm_allow_external
    s.embedding_model = c.embedding_modelo or origen.embedding_model
    s.tts_voz = c.voz_asistente or origen.tts_voz
    s.tts_velocidad = c.voz_velocidad if "voz_velocidad" in c.personalizadas else origen.tts_velocidad


def restaurar_ajustes() -> None:
    """Deshace aplicar_a_ajustes (para pruebas)."""
    s, origen = get_settings(), _origen()
    s.llm_provider, s.llm_api_base, s.llm_api_key, s.llm_allow_external, s.embedding_model = (
        origen.llm_provider, origen.llm_api_base, origen.llm_api_key, origen.llm_allow_external, origen.embedding_model)
    s.tts_voz, s.tts_velocidad = origen.tts_voz, origen.tts_velocidad


def _activo(c: ConfigIA) -> str:
    if c.en_nube:
        return c.llm_proveedor
    return "ollama" if get_settings().llm_provider == "ollama" else "externo_del_servidor"


def estado_nube(db: Session) -> dict:
    c = efectiva(db)
    lista = []
    for p in proveedores.PROVEEDORES.values():
        clave = _clave_nube(db, p.id)
        lista.append({"id": p.id, "nombre": p.nombre, "modelo_sugerido": p.modelo_sugerido, "nota": p.nota, "pide_direccion": p.id == "otro",
                      "clave_guardada": clave is not None, "clave_ultimos4": _ultimos4(clave)})
    return {"proveedores": lista, "activo": _activo(c), "modelo": c.modelo_activo if c.en_nube else None, "direccion": c.llm_api_base, "autorizada": c.llm_nube_autorizada}


def _ajustes_nube(proveedor: str, api_key: str, base_propia: str | None) -> Settings:
    base = proveedores.base_efectiva(proveedores.obtener(proveedor), base_propia)
    return get_settings().model_copy(update={"llm_provider": "openai_compatible", "llm_api_base": base, "llm_api_key": api_key, "llm_allow_external": True})


def _listar_modelos_nube(ajustes: Settings) -> tuple[list[str], str | None]:
    """(modelos de conversación, error). Un 401/403 es error; otros fallos (algunos proveedores no tienen /models) solo dejan la lista vacía."""
    try:
        r = httpx.get(f"{ajustes.llm_api_base}/models", headers={"Authorization": f"Bearer {ajustes.llm_api_key}"}, timeout=12)
    except httpx.HTTPError as exc:
        return [], f"No se pudo conectar con el proveedor ({type(exc).__name__}). Revisa la dirección y tu conexión a internet."
    if r.status_code in (401, 403):
        return [], "El proveedor rechazó la clave de API. Revisa que esté completa y activa."
    if r.status_code != 200:
        return [], None
    try:
        return proveedores.solo_chat([m["id"] for m in r.json().get("data", [])]), None
    except (ValueError, KeyError, TypeError, AttributeError):
        return [], None


def _resolver_clave(db: Session, proveedor: str, api_key: str | None) -> str:
    clave = (api_key or "").strip() or _clave_nube(db, proveedor)
    if not clave:
        raise ErrorNegocio("falta_clave", "Pega la clave de API del proveedor.", 422)
    if len(clave) > 400 or any(ch.isspace() for ch in clave):
        raise ErrorNegocio("clave_invalida", "La clave de API no tiene un formato válido (¿se copió con espacios?).", 422)
    return clave


def probar_nube(db: Session, proveedor: str, api_key: str | None = None, modelo: str | None = None, base_propia: str | None = None) -> dict:
    """Prueba una clave SIN guardarla: pide la lista de modelos y una respuesta mínima. Solo viaja un saludo fijo, ningún dato de pacientes."""
    clave = _resolver_clave(db, proveedor, api_key)
    ajustes = _ajustes_nube(proveedor, clave, base_propia)
    modelos, error = _listar_modelos_nube(ajustes)
    if error:
        return {"ok": False, "error": error, "modelos": []}
    sugerido = proveedores.obtener(proveedor).modelo_sugerido
    elegido = (modelo or "").strip() or (sugerido if (sugerido in modelos or not modelos) else modelos[0])
    if not elegido:
        return {"ok": False, "error": "El proveedor no listó modelos: escribe el nombre del modelo que quieres usar.", "modelos": modelos}
    try:
        c = cliente.ClienteLLM(ajustes, modelo=elegido)
        final = None
        try:
            for trozo in c.chat_stream([Mensaje.sistema("Eres la recepcionista de una institución de salud."), Mensaje.usuario("Saluda en una frase corta.")], opciones=Opciones(0.0, 40, False)):
                final = trozo.final or final
        finally:
            c.cerrar()
    except ErrorNegocio as e:
        return {"ok": False, "error": e.mensaje.removeprefix("El modelo de lenguaje no está disponible: "), "modelo": elegido, "modelos": modelos}
    return {"ok": True, "modelo": elegido, "modelos": modelos, "respuesta": final.contenido.strip()[:80], "ttft_ms": final.ttft_ms, "duracion_ms": final.duracion_ms}


def guardar_nube(db: Session, actor: Usuario, proveedor: str, *, api_key: str | None, modelo: str | None, base_propia: str | None, autoriza: bool) -> dict:
    """Guarda la clave (cifrada) y pasa el asistente a ese proveedor. Solo si la conexión funciona: no se deja el asistente roto."""
    if autoriza is not True:
        raise ErrorNegocio("falta_autorizacion", "Confirma que entiendes que las conversaciones se enviarán al proveedor de IA fuera de la institución.", 422)
    clave = _resolver_clave(db, proveedor, api_key)
    base = proveedores.base_efectiva(proveedores.obtener(proveedor), base_propia)
    prueba = probar_nube(db, proveedor, clave, modelo, base_propia)
    if not prueba["ok"]:
        raise ErrorNegocio("conexion_fallida", prueba["error"], 422)
    cifrada = {"v": crypto.cifrar(clave)}
    fila = db.get(ConfiguracionIA, _PREFIJO_CLAVE + proveedor)
    if fila:
        fila.valor, fila.actualizado_por = cifrada, actor.id
    else:
        db.add(ConfiguracionIA(clave=_PREFIJO_CLAVE + proveedor, valor=cifrada, actualizado_por=actor.id))
    db.flush()
    actualizar(db, actor, {"llm_proveedor": proveedor, "llm_modelo_nube": prueba["modelo"], "llm_nube_autorizada": True, "llm_api_base": base if proveedor == "otro" else None})
    auditoria.registrar(db, accion="config_ia_nube_activada", usuario_id=actor.id, entidad="config_ia", detalle={"proveedor": proveedor, "modelo": prueba["modelo"]})  # nunca la clave
    db.commit()
    aplicar_a_ajustes(db)
    return {**estado_nube(db), "prueba": prueba}


def usar_ollama(db: Session, actor: Usuario) -> dict:
    """Vuelve al modelo local. La clave guardada se conserva (inactiva) por si se quiere volver."""
    actualizar(db, actor, {"llm_proveedor": "ollama", "llm_nube_autorizada": None, "llm_modelo_nube": None, "llm_api_base": None})
    auditoria.registrar(db, accion="config_ia_nube_desactivada", usuario_id=actor.id, entidad="config_ia")
    db.commit()
    aplicar_a_ajustes(db)
    return estado_nube(db)


def borrar_clave_nube(db: Session, actor: Usuario, proveedor: str) -> dict:
    proveedores.obtener(proveedor)
    if efectiva(db).llm_proveedor == proveedor:
        raise ErrorNegocio("proveedor_en_uso", "Ese proveedor está en uso. Primero vuelve al modelo local.", 409)
    fila = db.get(ConfiguracionIA, _PREFIJO_CLAVE + proveedor)
    if fila:
        db.delete(fila)
    auditoria.registrar(db, accion="config_ia_clave_borrada", usuario_id=actor.id, entidad="config_ia", detalle={"proveedor": proveedor})
    db.commit()
    return estado_nube(db)


# ======================= modelo de embeddings (familia Gemma) =======================
def embeddings_instalados(modelos: list[dict] | None = None) -> list[dict]:
    """Modelos de embeddings Gemma de Ollama. Solo esta familia: el umbral de similitud y el formato de consulta están calibrados para ella."""
    return [m for m in (modelos if modelos is not None else modelos_instalados()) if m["embeddings"] and "gemma" in m["nombre"].lower()]


def probar_embedding(modelo: str) -> dict:
    """Pide un vector de prueba con ese modelo y comprueba que tenga las dimensiones de la columna de la base."""
    s = get_settings().model_copy(update={"embedding_model": modelo})
    try:
        t0 = time.perf_counter()
        v = OllamaEmbeddings(s).embed_consulta("¿Cómo cancelo mi cita?")
        ms = int((time.perf_counter() - t0) * 1000)
    except ErrorNegocio as e:
        return {"ok": False, "modelo": modelo, "error": e.mensaje}
    if len(v) != s.embedding_dimensions:
        return {"ok": False, "modelo": modelo, "dimensiones": len(v), "error": f"Este modelo da vectores de {len(v)} dimensiones y la base de datos guarda {s.embedding_dimensions}: no se puede usar sin migrar la base."}
    return {"ok": True, "modelo": modelo, "dimensiones": len(v), "duracion_ms": ms, "umbral": s.umbral_rag()}


def guardar_embedding(db: Session, actor: Usuario, modelo: str) -> dict:
    """Cambia el modelo de embeddings y reindexa el conocimiento en segundo plano: los vectores de un modelo no sirven para otro,
    y hasta reindexar la búsqueda solo encuentra lo ya indexado con el modelo nuevo."""
    modelo = (modelo or "").strip()
    instalados = embeddings_instalados()
    if not instalados or modelo not in {m["nombre"] for m in instalados}:
        raise ErrorNegocio("modelo_no_instalado", f"El modelo «{modelo}» no está instalado en Ollama o no es un modelo de embeddings de la familia Gemma.", 422)
    prueba = probar_embedding(modelo)
    if not prueba["ok"]:
        raise ErrorNegocio("embedding_no_valido", prueba["error"], 422)
    cambio = modelo != get_settings().embedding_model
    actualizar(db, actor, {"embedding_modelo": None if modelo == _origen().embedding_model else modelo})
    aplicar_a_ajustes(db)
    if cambio:
        try:
            indexacion_service.iniciar_reindexado(db, solo_pendientes=True)
        except ErrorNegocio:  # ya había un reindexado en curso (409): el panel muestra su avance
            pass
    return {"modelo": modelo, "prueba": prueba, "reindexando": cambio}


# ======================= voz del asistente =======================
def guardar_voz(db: Session, actor: Usuario, voz: str, velocidad: float | None = None) -> dict:
    """Elige la voz con la que habla el asistente (solo entre las instaladas) y su velocidad. «» = automática."""
    from app.tts import servicio

    tts = servicio.obtener_tts()
    if voz not in {v["id"] for v in tts.voces()}:
        raise ErrorNegocio("voz_no_disponible", f"La voz «{voz}» no está disponible en este equipo.", 422)
    origen = _origen()
    actualizar(db, actor, {"voz_asistente": voz or None, "voz_velocidad": None if velocidad is None or velocidad == origen.tts_velocidad else velocidad})
    aplicar_a_ajustes(db)
    threading.Thread(target=tts.precargar, daemon=True, name="precarga-voz").start()  # Kokoro tarda unos segundos en cargar: que no sea con un paciente esperando
    return {"elegida": get_settings().tts_voz, "velocidad": get_settings().tts_velocidad}

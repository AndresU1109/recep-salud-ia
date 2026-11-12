"""Configuración central. Todo se lee de variables de entorno o del archivo .env."""
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CLAVE_POR_DEFECTO = "cambiar-esta-clave"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Aplicación ---
    app_name: str = "RECEP-SALUD IA"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "production"] = "development"
    institution_name: str = "Institución de Salud (configurar en el panel)"
    timezone: str = "America/Bogota"
    log_level: str = "INFO"

    # --- Seguridad ---
    secret_key: str = CLAVE_POR_DEFECTO
    # Clave Fernet (32 bytes en base64 url-safe) para cifrar datos personales. En desarrollo, si
    # está vacía se deriva de SECRET_KEY; en producción es obligatoria y NO debe perderse.
    data_encryption_key: str = ""
    access_token_minutes: int = 30
    login_max_intentos: int = 5
    login_bloqueo_minutos: int = 15
    clave_longitud_minima: int = 12
    cors_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    # --- Base de datos (PostgreSQL + pgvector) ---
    database_url: str = "postgresql+psycopg://recep:recep@127.0.0.1:5432/recep_salud"

    # --- LLM (Ollama por defecto; cualquier API compatible con OpenAI es opcional) ---
    llm_provider: Literal["ollama", "openai_compatible"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "gemma4:e4b-it-qat"
    llm_api_base: str = ""
    llm_api_key: str = ""
    # Una API externa envía el contenido de las conversaciones fuera de la institución (transmisión de datos personales):
    # solo se usa si se habilita de forma expresa tras la evaluación legal correspondiente (Ley 1581 de 2012).
    llm_allow_external: bool = False
    llm_timeout_seconds: int = 120
    llm_keep_alive: str = "30m"  # Ollama descarga el modelo tras 5 min sin uso; recargarlo tarda 10-60 s y por voz sería un silencio incómodo. 0 = no enviar
    precargar_modelos_de_voz: bool = True  # al arrancar, Whisper y Piper se cargan en segundo plano (el primer paciente no espera ~40 s)
    llm_num_ctx: int = 4096
    llm_temperatura: float = 0.2
    llm_max_tokens: int = 400
    llm_razonamiento: bool = False  # medido: con razonamiento ~5 s por turno (demasiado lento para voz); sin él ~1 s

    # --- Embeddings (la dimensión debe coincidir con la columna vector de la BD) ---
    embedding_model: str = "embeddinggemma:latest"
    embedding_dimensions: int = 768
    embedding_usar_prompts: bool = True  # formato recomendado por EmbeddingGemma ("task: search result | query: ...")
    embedding_batch_size: int = 16
    # Similitud coseno mínima para considerar que un fragmento responde la consulta. Medido con embeddinggemma:latest:
    # consultas relevantes 0.44-0.65, irrelevantes <= 0.14. 0.30 queda en medio; recalibrar con documentos reales.
    rag_umbral_similitud: float = 0.30
    # Cada modelo de embeddings tiene su escala: embeddinggemma-2 (medido) da 0,74-0,80 a lo relevante y hasta 0,69 a lo que no tiene relación; con 0,30 todo «respondería».
    rag_umbral_por_modelo: dict[str, float] = {"embeddinggemma-2": 0.71}
    rag_k: int = 5

    # --- Documentos / RAG ---
    uploads_dir: str = "data/uploads"
    max_upload_mb: int = 15
    chunk_max_chars: int = 1000
    chunk_overlap_chars: int = 150

    # --- Voz (Fases 12-14) ---
    stt_model: str = "small"
    stt_models_dir: str = "data/models/whisper"  # caché de Hugging Face; se usa sin conexión (local_files_only)
    stt_device: Literal["auto", "cpu", "cuda"] = "cpu"  # ver CONTEXTO.md: la GPU está ocupada con el LLM y requiere librerías CUDA aparte
    stt_compute_type: str = "int8"
    # Whisper en la GPU (medido: ≈0,8 s por frase frente a 6-9 s en CPU en esta máquina). Necesita cuBLAS/cuDNN de CUDA 12: carpetas `bin` que los contienen,
    # separadas por «;» (si se instalaron con pip en este entorno se encuentran solas). Con STT_DEVICE=auto, si no hay librerías, memoria libre o falla, usa CPU.
    stt_cuda_dll_dirs: str = ""
    stt_vram_minima_mib: int = 1500
    stt_cpu_threads: int = 4  # medido en otro proyecto de este equipo: 4 ≈ 6 ≈ 8 hilos, 12 es peor
    stt_beam_size: int = 1  # medido: beam 1 y 5 transcriben igual en frases de recepción; 1 es más rápido
    stt_max_segundos: int = 30  # una intervención del paciente, no una grabación larga
    stt_max_bytes: int = 8 * 1024 * 1024
    stt_umbral_silencio_dbfs: float = -50.0
    stt_confianza_minima: float = 0.35  # por debajo, el asistente pide repetir en vez de actuar sobre lo transcrito
    # Voz (Fase 13). Medido con 12 frases críticas (ida y vuelta TTS→Whisper): Piper y la voz de Windows son igual de inteligibles (~9-10 %,
    # diferencias de normalización); Piper es neuronal y ~0.03 de factor de tiempo real. NINGUNA es colombiana (Piper solo tiene es_AR, es_ES y es_MX).
    tts_motor: Literal["auto", "piper", "kokoro", "sapi"] = "auto"  # auto = Piper si está la voz, si no la voz de Windows
    # Voz elegida en el panel («Configuración de IA»): "" = automática; "piper"; "kokoro:ef_dora"; "sapi". Gana sobre tts_motor.
    tts_voz: str = ""
    tts_kokoro_modelo: str = "data/models/tts/kokoro/kokoro-v1.0.onnx"
    tts_kokoro_voces: str = "data/models/tts/kokoro/voices-v1.0.bin"
    tts_piper_modelo: str = "data/models/tts/piper/es_MX-claude-high.onnx"
    tts_voice: str = "Microsoft Sabina Desktop"  # voz de Windows (respaldo)
    tts_velocidad: float = 1.0  # 1.0 = normal; 0.8 = más lento
    tts_max_caracteres: int = 600
    tts_cache_entradas: int = 64
    voice_language: str = "es"
    # --- Notificaciones (Fase 15). Solo hay canal de CORREO (SMTP): no hay proveedor de SMS/WhatsApp integrado (N/D). Sin SMTP_HOST los avisos se registran como omitidos. ---
    notificaciones_activas: bool = True
    notificaciones_intervalo_s: int = 60  # cada cuánto el servidor revisa recordatorios y envía lo pendiente
    notificaciones_max_intentos: int = 3
    notificaciones_espera_reintento_min: int = 5  # se multiplica por el nº de intento
    notificaciones_hora_desde: int = 7  # recordatorios y avisos de cupo solo en horario diurno (hora de la institución)
    notificaciones_hora_hasta: int = 20
    recordatorio_horas_antes: int = 24
    recordatorio_minimo_horas: int = 2  # no se manda un recordatorio a menos de esto de la cita
    espera_notificar_maximo: int = 3  # a cuántas personas de la lista de espera se avisa por cada cupo liberado (por orden de llegada)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_usuario: str = ""
    smtp_clave: str = ""
    smtp_remitente: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False  # SSL directo (puerto 465); si no, STARTTLS
    smtp_timeout_s: int = 15

    # --- Retención (Fase 17). Estos plazos son valores INICIALES de la aplicación, NO un criterio legal: la institución debe fijarlos con su asesor jurídico
    # (Ley 1581 de 2012 pide conservar datos solo el tiempo necesario). 0 = no borrar nunca. La bitácora de auditoría y las citas NO se purgan. ---
    retencion_avisos_dias: int = 90  # se borra el texto de los avisos enviados/omitidos/fallidos (queda el registro y las cifras)
    retencion_conversaciones_dias: int = 365  # se borra el contenido de los mensajes de conversaciones reales (queda intención y cifras)
    retencion_pruebas_dias: int = 30  # se borran por completo las conversaciones de prueba del personal

    # Interfaz de voz (Fase 14): WebSocket /api/v1/voz/ws. El navegador manda PCM16 mono 16 kHz y avisa cuándo terminó de hablar.
    voz_inactividad_s: int = 120  # sin mensajes del cliente durante este tiempo, la conversación se cierra como abandonada
    voz_espera_auth_s: int = 10
    voz_max_no_entendidos: int = 3  # tras tantos «no le entendí» seguidos, el asistente sugiere escribir o pedir a una persona

    @model_validator(mode="after")
    def _exigir_secretos_reales_en_produccion(self) -> "Settings":
        if self.app_env == "production" and (
            self.secret_key == CLAVE_POR_DEFECTO or len(self.secret_key) < 32
        ):
            raise ValueError("En producción SECRET_KEY debe definirse y tener al menos 32 caracteres.")
        if self.app_env == "production" and not self.data_encryption_key:
            raise ValueError("En producción DATA_ENCRYPTION_KEY es obligatoria.")
        if self.app_env == "production" and any(o.startswith("http://") and "localhost" not in o and "127.0.0.1" not in o for o in self.cors_origins):
            raise ValueError("En producción CORS_ORIGINS solo admite https:// (o localhost).")
        return self

    def umbral_rag(self) -> float:
        """Similitud mínima para el modelo de embeddings en uso (cada modelo tiene su escala)."""
        return self.rag_umbral_por_modelo.get(self.embedding_model.split(":")[0], self.rag_umbral_similitud)


@lru_cache
def get_settings() -> Settings:
    return Settings()

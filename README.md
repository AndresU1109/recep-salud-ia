# RECEP-SALUD IA

Recepción médica con agentes conversacionales (texto y voz): citas, pacientes y consulta documental (RAG) con PostgreSQL + pgvector. Todo con datos ficticios.


## Inicio rápido (Windows)

Doble clic en **`iniciar.cmd`**: enciende Docker y la base de datos, revisa Ollama, aplica las migraciones, arranca la aplicación en segundo plano y abre el panel en http://127.0.0.1:8000/panel/. Para apagar todo: **`detener.cmd`** (los datos no se borran; `detener.cmd -MantenerBD` deja la base de datos encendida).

- Requisito previo, una sola vez: `.\scripts\setup.ps1` (crea el entorno de Python y el `.env`) y crear el primer administrador (el script de inicio te dice cómo si todavía no hay ninguno).
- Opciones: `iniciar.cmd -SinNavegador`, `iniciar.cmd -Puerto 8010`.
- Los registros quedan en `data/logs/api.log` y `api.err.log`; el número del proceso, en `.recep.pid`.
- Si algo falla, la ventana se queda abierta con el motivo.

## Atender por voz

En el panel, **Atención → Atención por voz**: pulse «Iniciar conversación», permita el micrófono y hable. El asistente responde en voz alta, pide el documento y la fecha de nacimiento (lee el número de vuelta para que lo confirme), ofrece horarios con profesional y sede, nombra especialidades y sedes, y agenda la cita; todo hablado. Con el permiso de atender pacientes (administrador, coordinador, recepcionista) se puede marcar «conversación real»; sin marcar es de prueba. No hay que elegir nada en pantalla: el asistente pregunta **con cuál institución** desea comunicarse (hay 5 ficticias tras `python -m scripts.seed_demo_completo`: una clínica, una IPS, un centro odontológico, un hospital y un centro pediátrico) o la deduce de la especialidad que el paciente pide, y desde entonces solo responde con las especialidades, sedes, políticas y documentos de esa institución. Cuando el paciente dice que no necesita más ayuda («no, eso es todo»), el asistente se despide y la llamada se cierra sola. Para probar con los datos demo: documento `9183746502` (CC) y nacimiento 21 de agosto de 1993 (Valentina Cruz Demo).

## Datos de demostración (ficticios)

```powershell
.\.venv\Scripts\python.exe -m scripts.seed_demo_completo
```

Carga sedes, consultorios, 7 especialidades, 10 profesionales con horarios, 18 pacientes, **citas de la semana en curso** (pasadas con su desenlace y futuras agendadas por el servicio), lista de espera, solicitudes, políticas, preguntas frecuentes, 4 documentos indexados para el asistente y un usuario por rol. Todo lleva «demo» o «ficticio/a», los documentos son `9000000001…` y los correos `@example.com`. Es idempotente (no duplica) y se niega a correr en producción. Requiere un administrador creado y Ollama encendido (para indexar los documentos).

## Arrancar (desarrollo)

```powershell
.\scripts\setup.ps1          # venv + dependencias + .env con claves aleatorias
docker compose up -d db      # PostgreSQL con pgvector
.\.venv\Scripts\alembic.exe upgrade head    # crea las tablas
.\.venv\Scripts\python.exe -m scripts.seed_demo   # (opcional) datos demo ficticios
.\.venv\Scripts\python.exe -m scripts.crear_admin --email tu@correo.co --nombre "Tu Nombre"   # primer administrador
.\scripts\dev.ps1            # API en http://localhost:8000  (docs en /docs)
```

Requisitos: Python 3.12, Docker, Ollama con `gemma4:e4b-it-qat` y `embeddinggemma:latest`.

## Comprobar

```powershell
.\.venv\Scripts\python.exe -m pytest        # pruebas
curl http://localhost:8000/api/v1/health/dependencias
```

Todo en Docker: `docker compose up -d --build` (la API habla con el Ollama del equipo). Con HTTPS: `docker compose --profile produccion up -d --build`.

**Despliegue, HTTPS, copias de seguridad, retención de datos y lista de comprobación antes de abrir al público: [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md).**

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not ollama"          # ~850 pruebas, sin modelos (~3 min)
.\.venv\Scripts\python.exe -m pytest -m ollama -s             # Pruebas con el modelo real (Ollama): embeddings, LLM y el agente (~5 min)
```

Pruebas con voz real (necesitan los modelos de Whisper y Piper; se omiten si faltan): `-m stt`, `-m tts` y `-m "stt and tts"` (voz → asistente → voz).

El asistente se prueba desde el panel (**Conocimiento → Probar asistente**) con usuario ADMINISTRADOR o COORDINADOR. Datos de ejemplo: `python -m scripts.seed_demo`.

## IA en la nube (opcional)
Por defecto todo es local (Ollama). En el panel, **Administración → Configuración de IA**, se puede usar una IA en la nube: elegir el proveedor, pegar la clave de API y pulsar «Probar conexión» (la clave se guarda cifrada y solo si la prueba funciona). Esto envía el texto de las conversaciones al proveedor: confirme que tiene la autorización legal para hacerlo. En la misma pantalla se elige el modelo Gemma de embeddings de la búsqueda en documentos (se reindexa solo).

## Voz: activación, encuesta y voces
- **Activar por voz:** en «Atención por voz → Activar escucha por voz» el panel espera «hola asistente» (o la frase elegida en Configuración de IA) y empieza la llamada solo; al terminar vuelve a esperar. El audio de la espera no se guarda ni se devuelve.
- **Encuesta:** al final de una llamada de voz el asistente pide una calificación de 1 a 5 (opcional). Resultados en **Reportes → Satisfacción**. Se apaga en Configuración de IA.
- **Voz del asistente:** Configuración de IA → «Voz del asistente»: Piper, Kokoro (Dora, Alex, Santa; modelo en `data/models/tts/kokoro/`) o la voz de Windows, con muestra y velocidad.


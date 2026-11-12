# Despliegue y operación de RECEP-SALUD IA

> Lo que está **verificado** y lo que **no** se indica en cada sección. Estado general del proyecto: [CONTEXTO.md](../CONTEXTO.md).

## 1. Qué se despliega

| Pieza | Cómo corre | Notas |
|---|---|---|
| PostgreSQL + pgvector | contenedor `db` (volumen `pgdata`) | solo escucha en `127.0.0.1` |
| API + panel | contenedor `api` (usuario sin privilegios, `HEALTHCHECK`) | solo en `127.0.0.1:8000`; aplica migraciones al arrancar |
| HTTPS | contenedor `proxy` (Caddy), perfil `produccion` | lo único que escucha en la red (80/443) |
| Ollama (LLM y embeddings) | **en el equipo**, fuera de Docker | la API lo alcanza por `host.docker.internal:11434` |
| Modelos de voz | carpeta `data/models` montada de solo lectura | Whisper `small` + voz Piper; **no van en la imagen** |

Requisitos: Docker, Ollama con `gemma4:e4b-it-qat` y `embeddinggemma:latest`, y los modelos de voz en `data/models/whisper` y `data/models/tts/piper/es_MX-claude-high.onnx(.json)`.

## 2. Primera puesta en marcha

```powershell
copy .env.example .env     # o .\scripts\setup.ps1, que además genera claves aleatorias
# Edita .env: POSTGRES_PASSWORD, SECRET_KEY (32+ caracteres), DATA_ENCRYPTION_KEY (Fernet), APP_ENV=production,
# CORS_ORIGINS=["https://tu-dominio"], DOMINIO=tu-dominio, y SMTP_* si quieres enviar correos.
docker compose --profile produccion up -d --build
docker compose exec api python -m scripts.crear_admin --email tu@correo.co --nombre "Tu Nombre"
```

Clave Fernet: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

**Guarda `SECRET_KEY` y `DATA_ENCRYPTION_KEY` fuera del servidor (gestor de secretos).** Sin `DATA_ENCRYPTION_KEY` los documentos de identidad cifrados no se pueden recuperar, ni siquiera con un respaldo.

En `APP_ENV=production` la aplicación **se niega a arrancar** si faltan esas claves o si `CORS_ORIGINS` tiene un `http://` que no sea localhost, y no publica `/docs`, `/redoc` ni `/openapi.json`.

## 3. HTTPS

El micrófono del navegador **solo funciona en HTTPS o en localhost**; el proxy Caddy lo resuelve:

- `DOMINIO=localhost`: certificado del CA local de Caddy (el navegador pedirá confiar en él). Sirve para probar.
- `DOMINIO=recepcion.tu-institucion.co`: Caddy obtiene y renueva solo el certificado de Let's Encrypt. Requiere que el DNS apunte al servidor y que lleguen los puertos 80 y 443. **No verificado aquí** (no hay dominio público): solo se comprobó el modo `localhost`.

El proxy añade `Strict-Transport-Security`, comprime, limita las subidas a 20 MB y deja pasar el WebSocket de voz.

## 4. Copias de seguridad y restauración (verificadas)

```powershell
.\scripts\respaldo.ps1                  # respaldos\recep-AAAAMMDD-HHMMSS.dump (+ documentos.zip, + .sha256); conserva los últimos 14
.\scripts\restaurar.ps1 -Archivo respaldos\recep-....dump       # restaura en una base NUEVA 'recep_restaurada' y cuenta filas por tabla
```

Verificado: el respaldo de la base de desarrollo se restauró en una base nueva con **las mismas filas en las 29 tablas** y las extensiones `vector` y `btree_gist`; un archivo alterado se rechaza por su SHA256; no se permite restaurar encima de la base de trabajo sin `-Sobrescribir` y confirmación escrita.

- Programa `respaldo.ps1` en el Programador de tareas de Windows (diario). **No está programado**: es una decisión de la institución.
- El respaldo contiene datos personales: guárdalo en un disco **cifrado** y copia el último a otro equipo/ubicación. El script no cifra.
- Prueba la restauración periódicamente: un respaldo que nunca se restauró no es un respaldo.
- Si la API corre en Docker, los documentos subidos están en el volumen `uploads` (el script solo empaqueta `data/uploads` local): `docker compose cp api:/srv/data/uploads respaldos\uploads`.

Volver a un respaldo en producción: detener la API (`docker compose stop api`), `restaurar.ps1 -BaseDestino <base de trabajo> -Sobrescribir`, y `docker compose start api`.

## 5. Actualizar y volver atrás

```powershell
.\scripts\respaldo.ps1
docker compose --profile produccion up -d --build     # reconstruye y aplica las migraciones al arrancar
```

Las migraciones son hacia adelante; `alembic downgrade` existe pero no se probó contra datos reales. Para volver atrás con seguridad: restaura el respaldo previo a la actualización.

## 6. Retención de datos

El servidor aplica cada día (`retencion_service`): borra el **texto** de avisos con más de `RETENCION_AVISOS_DIAS` (90), el **contenido** de mensajes de conversaciones reales con más de `RETENCION_CONVERSACIONES_DIAS` (365) y las conversaciones **de prueba** con más de `RETENCION_PRUEBAS_DIAS` (30). `0` = no borrar nunca. No toca la bitácora de auditoría ni las citas.

**Esos plazos son valores iniciales, no un criterio legal.** La institución debe fijarlos con su asesor jurídico (Ley 1581 de 2012 y normas de historia clínica si aplican). Los respaldos conservan lo que ya se había borrado de la base: define también cuánto tiempo se guardan.

## 7. Operación

- Salud: `GET /api/v1/health` (rápido) y `/api/v1/health/dependencias` (BD, Ollama, modelos; requiere sesión).
- Logs: `docker compose logs -f api`. No contienen correos, teléfonos ni documentos (se redactan) ni el contenido de lo dicho.
- Avisos por correo: sin `SMTP_HOST` quedan registrados como omitidos. Vista *Notificaciones* del panel.
- Voz: la primera síntesis de Piper tarda ~5 s (se precalienta al arrancar). La transcripción corre en CPU (~0,35× tiempo real); no hay GPU para Whisper en esta máquina.

## 8. Lista de comprobación antes de abrir al público

- [ ] `APP_ENV=production`, claves reales, respaldo de las claves fuera del servidor.
- [ ] DNS y HTTPS con certificado real; comprobar con el navegador que el micrófono funciona.
- [ ] Primer administrador creado y la contraseña temporal cambiada.
- [ ] Respaldo diario programado **y** una restauración de prueba hecha.
- [ ] Plazos de retención definidos con el asesor jurídico; política de tratamiento de datos publicada y consentimiento revisado.
- [ ] SMTP configurado y un correo de prueba recibido (no se probó con un proveedor real).
- [ ] Probar el asistente con audio y acento reales de la región (**no se ha hecho**: toda la voz probada fue sintética).
- [ ] Decidir el canal de pacientes: hoy el asistente y la llamada de voz son solo para personal autorizado (modo prueba).
- [ ] Revisión de seguridad independiente antes de exponer datos reales (esta aplicación no ha tenido una).

## 9. Límites conocidos de esta versión

- Sin canal público para pacientes ni telefonía (no se simulan llamadas reales: no hay integración SIP).
- Sin limitación de peticiones por IP (solo bloqueo de cuenta tras 5 fallos de inicio de sesión).
- Sin cifrado de los respaldos ni rotación de `SECRET_KEY`/`DATA_ENCRYPTION_KEY`.
- Sin alta disponibilidad: una sola instancia de cada servicio.
- Un solo trabajador de la API: el ciclo de avisos y retención corre dentro del proceso.

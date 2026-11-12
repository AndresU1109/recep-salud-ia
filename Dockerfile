FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# libgomp1: lo necesitan faster-whisper (CTranslate2) y onnxruntime (Piper) para usar varios hilos.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY migrations ./migrations
COPY alembic.ini .

# Los modelos (Whisper, Piper) NO van en la imagen: se montan en /srv/data/models (ver docker-compose.yml).
RUN useradd --create-home recep && mkdir -p /srv/data/uploads && chown -R recep /srv
USER recep

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"
# Aplica las migraciones pendientes y arranca la API (detras del proxy: confia en sus cabeceras X-Forwarded-*).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]

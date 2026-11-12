# Arranca la API en modo desarrollo (recarga automática) en http://localhost:8000
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

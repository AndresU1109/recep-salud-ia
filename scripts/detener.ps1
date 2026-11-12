# Detiene RECEP-SALUD IA: la API en segundo plano y los contenedores (base de datos, y API/proxy si estaban en Docker). (Solo ASCII.)
# Uso:  .\scripts\detener.ps1 [-MantenerBD] [-Puerto 8000]      (o doble clic en detener.cmd)
# Los datos NO se borran: los contenedores se detienen, no se eliminan; el volumen de la base de datos sigue intacto.
param([switch]$MantenerBD, [int]$Puerto = 8000)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$archivoPid = Join-Path (Get-Location).Path ".recep.pid"
$url = "http://127.0.0.1:$Puerto"

function Paso($t) { Write-Host ""; Write-Host ">> $t" -ForegroundColor Cyan }
function Bien($t) { Write-Host "   [ok] $t" -ForegroundColor Green }
function Ojo($t) { Write-Host "   [!]  $t" -ForegroundColor Yellow }
function Responde($direccion) { try { return ((Invoke-WebRequest $direccion -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) } catch { return $false } }
# taskkill /T mata tambien a los procesos hijos (en Windows, el python del entorno virtual lanza otro python).
function Matar-Arbol($id) { taskkill /PID $id /T /F *> $null }

Write-Host ""
Write-Host "  RECEP-SALUD IA - detener" -ForegroundColor White

# ---------- 1. la API que arranco iniciar.ps1 ----------
Paso "Deteniendo la aplicacion"
$detenidos = 0
if (Test-Path $archivoPid) {
    $id = [int](Get-Content $archivoPid -TotalCount 1)
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { Matar-Arbol $id; $detenidos++ }
    Remove-Item $archivoPid -Force
}
# Por si quedo alguna suelta (p. ej. arrancada con dev.ps1): solo procesos que ejecutan ESTA aplicacion en ESTE puerto.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app\.main:app" -and $_.CommandLine -match "--port $Puerto(\s|$)"
} | ForEach-Object { Matar-Arbol $_.ProcessId; $detenidos++ }
Start-Sleep -Seconds 1
if (Responde "$url/api/v1/health") { Ojo "Algo sigue respondiendo en $url (quiza la API corre dentro de Docker; se detiene a continuacion)." }
elseif ($detenidos -gt 0) { Bien "Aplicacion detenida" }
else { Bien "La aplicacion no estaba en marcha" }

# ---------- 2. contenedores ----------
Paso "Deteniendo los contenedores"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Ojo "Docker no esta instalado: no hay contenedores que detener." }
else {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Ojo "Docker no esta encendido: no hay contenedores en marcha." }
    elseif ($MantenerBD) {
        docker compose --profile produccion stop api proxy *> $null
        Bien "API y proxy en Docker detenidos (la base de datos sigue encendida: -MantenerBD)"
    } else {
        docker compose --profile produccion stop *> $null
        Bien "Base de datos (y API/proxy si estaban en Docker) detenidas. Los datos siguen guardados."
    }
}

Write-Host ""
if ($MantenerBD) { Write-Host "  Aplicacion apagada; la base de datos sigue encendida.  Para volver a encender la aplicacion:  iniciar.cmd" -ForegroundColor Green }
else { Write-Host "  Listo, todo apagado.  Para volver a encender:  iniciar.cmd" -ForegroundColor Green }

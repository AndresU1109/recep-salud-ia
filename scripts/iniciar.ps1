# Inicio rapido de RECEP-SALUD IA: deja todo encendido con un solo comando y abre el panel. (Solo ASCII: PowerShell 5.1 lee mal los acentos sin BOM.)
# Uso:  .\scripts\iniciar.ps1 [-SinNavegador] [-Puerto 8000]      (o doble clic en iniciar.cmd)
# Hace, en orden: revisa requisitos -> enciende Docker y la base de datos -> revisa Ollama -> aplica migraciones -> arranca la API -> abre el panel.
# Detener: detener.cmd. La API queda en segundo plano (sin ventana); sus registros estan en data\logs\.
param([switch]$SinNavegador, [int]$Puerto = 8000)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$raiz = (Get-Location).Path
$url = "http://127.0.0.1:$Puerto"
$python = Join-Path $raiz ".venv\Scripts\python.exe"
$archivoPid = Join-Path $raiz ".recep.pid"
$registros = Join-Path $raiz "data\logs"

function Paso($t) { Write-Host ""; Write-Host ">> $t" -ForegroundColor Cyan }
function Bien($t) { Write-Host "   [ok] $t" -ForegroundColor Green }
function Ojo($t) { Write-Host "   [!]  $t" -ForegroundColor Yellow }
function Fallo($t) { Write-Host "   [X]  $t" -ForegroundColor Red; exit 1 }
function Responde($direccion) { try { return ((Invoke-WebRequest $direccion -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200) } catch { return $false } }
function Leer-Env($clave, $defecto) {
    if (Test-Path .env) {
        $linea = Get-Content .env | Where-Object { $_ -match "^\s*$clave\s*=" } | Select-Object -First 1
        if ($linea) { return ($linea -replace "^\s*$clave\s*=\s*", "").Trim().Trim('"') }
    }
    return $defecto
}
function Docker-Listo { docker info *> $null; return ($LASTEXITCODE -eq 0) }

Write-Host ""
Write-Host "  RECEP-SALUD IA - inicio rapido" -ForegroundColor White

# ---------- 1. requisitos ----------
Paso "Revisando requisitos"
if (-not (Test-Path $python)) { Fallo "No existe el entorno de Python (.venv). Ejecuta primero:  .\scripts\setup.ps1" }
if (-not (Test-Path .env)) { Fallo "No existe el archivo .env. Ejecuta primero:  .\scripts\setup.ps1   (lo crea con claves aleatorias)" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fallo "Docker no esta instalado: la base de datos corre en un contenedor." }
Bien "Python, .env y Docker encontrados"

# ---------- 2. Docker y la base de datos ----------
Paso "Encendiendo la base de datos"
if (-not (Docker-Listo)) {
    $escritorio = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $escritorio)) { Fallo "Docker no esta encendido y no encuentro Docker Desktop. Abrelo y vuelve a intentar." }
    Ojo "Docker Desktop estaba apagado: lo enciendo (puede tardar un minuto)..."
    Start-Process $escritorio
    $limite = (Get-Date).AddSeconds(150)
    while (-not (Docker-Listo)) {
        if ((Get-Date) -gt $limite) { Fallo "Docker no termino de arrancar en 2,5 minutos. Abre Docker Desktop, espera a que diga 'running' y reintenta." }
        Start-Sleep -Seconds 4
    }
}
docker compose up -d db | Out-Null
if ($LASTEXITCODE -ne 0) { Fallo "No pude encender la base de datos (docker compose up -d db). Revisa POSTGRES_PASSWORD en .env." }
$contenedor = (docker compose ps -q db | Select-Object -First 1)
$limite = (Get-Date).AddSeconds(90)
while ($true) {
    $salud = (docker inspect -f "{{.State.Health.Status}}" $contenedor 2>$null)
    if ($salud -eq "healthy") { break }
    if ((Get-Date) -gt $limite) { Fallo "La base de datos no quedo lista en 90 s (estado: $salud). Mira: docker compose logs db" }
    Start-Sleep -Seconds 2
}
Bien "PostgreSQL listo"

# ---------- 3. Ollama (el modelo de lenguaje) ----------
Paso "Revisando Ollama (el asistente)"
$ollama = (Leer-Env "OLLAMA_BASE_URL" "http://127.0.0.1:11434").TrimEnd("/")
if (-not (Responde "$ollama/api/tags")) {
    $exe = Get-Command ollama -ErrorAction SilentlyContinue
    if ($exe) {
        Ojo "Ollama no responde: lo enciendo..."
        Start-Process -FilePath $exe.Source -ArgumentList "serve" -WindowStyle Hidden
        $limite = (Get-Date).AddSeconds(25)
        while (-not (Responde "$ollama/api/tags") -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 2 }
    }
}
if (Responde "$ollama/api/tags") { Bien "Ollama responde" } else { Ojo "Ollama no responde. El panel funciona, pero el asistente no hasta que abras la aplicacion Ollama." }

# ---------- 4. migraciones ----------
Paso "Actualizando la base de datos"
& $python -m alembic upgrade head 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fallo "Fallaron las migraciones. Ejecuta para ver el error:  .\.venv\Scripts\python.exe -m alembic upgrade head" }
Bien "Tablas al dia"

# ---------- 5. la API ----------
Paso "Arrancando la aplicacion"
if (Responde "$url/api/v1/health") {
    Bien "Ya estaba en marcha en $url"
} else {
    if (Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue) { Fallo "El puerto $Puerto esta ocupado por otro programa. Cierralo o usa otro:  .\scripts\iniciar.ps1 -Puerto 8010" }
    New-Item -ItemType Directory -Force $registros | Out-Null
    # Se lanza por WMI (Win32_Process.Create): el proceso queda totalmente desligado de esta consola (sin ventana y sin heredar sus tuberias),
    # asi esta ventana puede cerrarse y quien llame al script no se queda esperando a la API.
    $log = Join-Path $registros "api.log"
    $err = Join-Path $registros "api.err.log"
    $orden = "cmd.exe /c `"`"$python`" -m uvicorn app.main:app --host 127.0.0.1 --port $Puerto > `"$log`" 2> `"$err`"`""
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $orden; CurrentDirectory = $raiz }
    if ($r.ReturnValue -ne 0) { Fallo "Windows no pudo lanzar la aplicacion (codigo $($r.ReturnValue))." }
    $r.ProcessId | Set-Content -Encoding ASCII $archivoPid
    $proceso = Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue
    $limite = (Get-Date).AddSeconds(90)
    while (-not (Responde "$url/api/v1/health")) {
        if (-not (Get-Process -Id $r.ProcessId -ErrorAction SilentlyContinue)) {
            Write-Host "--- ultimas lineas del registro de errores ---" -ForegroundColor Red
            Get-Content (Join-Path $registros "api.err.log") -Tail 15 -ErrorAction SilentlyContinue
            Fallo "La aplicacion se cerro al arrancar. Revisa data\logs\api.err.log"
        }
        if ((Get-Date) -gt $limite) { Fallo "La aplicacion no respondio en 90 s. Revisa data\logs\api.err.log" }
        Start-Sleep -Seconds 2
    }
    Bien "Aplicacion lista en $url"
}

# ---------- 6. primer uso ----------
$usuario = Leer-Env "POSTGRES_USER" "recep"
$base = Leer-Env "POSTGRES_DB" "recep_salud"
$cuantos = (docker compose exec -T db psql -U $usuario -d $base -At -c "select count(*) from usuarios" 2>$null | Select-Object -First 1)
if ($cuantos -eq "0") {
    Ojo "Todavia no hay ningun usuario. Crea el primer administrador con:"
    Write-Host "        .\.venv\Scripts\python.exe -m scripts.crear_admin --email tu@correo.co --nombre `"Tu Nombre`"" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Todo listo.  Panel:  $url/panel/" -ForegroundColor Green
Write-Host "  Para apagar todo:  detener.cmd   (o  .\scripts\detener.ps1)" -ForegroundColor Gray
if (-not $SinNavegador) { Start-Process "$url/panel/" }

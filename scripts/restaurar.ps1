# Restaura un respaldo en una base NUEVA (por defecto 'recep_restaurada'), nunca encima de la de trabajo salvo que se pida. Solo ASCII.
# Uso:  .\scripts\restaurar.ps1 -Archivo respaldos\recep-AAAAMMDD-HHMMSS.dump [-BaseDestino recep_restaurada] [-Sobrescribir]
# Para volver a poner en marcha el sistema con ese respaldo: detener la API, restaurar con -BaseDestino <la base de trabajo> -Sobrescribir, y arrancar.
param([Parameter(Mandatory = $true)][string]$Archivo, [string]$BaseDestino = "recep_restaurada", [switch]$Sobrescribir)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Leer-Env($clave, $defecto) {
    if (Test-Path .env) {
        $linea = Get-Content .env | Where-Object { $_ -match "^\s*$clave\s*=" } | Select-Object -First 1
        if ($linea) { return ($linea -replace "^\s*$clave\s*=\s*", "").Trim().Trim('"') }
    }
    return $defecto
}
$usuario = Leer-Env "POSTGRES_USER" "recep"
$trabajo = Leer-Env "POSTGRES_DB" "recep_salud"
if (-not (Test-Path $Archivo)) { throw "No existe $Archivo" }
if ($BaseDestino -notmatch "^[A-Za-z0-9_]+$") { throw "BaseDestino solo admite letras, numeros y guion bajo" }

# 1) Integridad: coincide con su suma SHA256 (si existe).
if (Test-Path "$Archivo.sha256") {
    $esperado = ((Get-Content "$Archivo.sha256" -TotalCount 1) -split "\s+")[0]
    if ((Get-FileHash $Archivo -Algorithm SHA256).Hash -ne $esperado) { throw "El respaldo esta alterado o danado: la suma SHA256 no coincide" }
    Write-Host "SHA256 correcto."
} else { Write-Warning "No hay archivo .sha256: no se pudo verificar la integridad." }

# 2) Nunca encima de la base de trabajo sin pedirlo expresamente.
if ($BaseDestino -eq $trabajo -and -not $Sobrescribir) { throw "Eso reemplazaria la base de trabajo '$trabajo'. Si es lo que quieres, agrega -Sobrescribir (y deten la API antes)." }
if ($BaseDestino -eq $trabajo) {
    $r = Read-Host "Se BORRARA la base '$trabajo' y se reemplazara por el respaldo. Escribe el nombre de la base para confirmar"
    if ($r -ne $trabajo) { throw "Cancelado." }
}

docker compose cp $Archivo db:/tmp/restaurar.dump | Out-Null
docker compose exec -T db psql -U $usuario -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $BaseDestino WITH (FORCE)" -c "CREATE DATABASE $BaseDestino"
if ($LASTEXITCODE -ne 0) { throw "No se pudo crear la base $BaseDestino" }
# Las extensiones (vector, btree_gist) viajan dentro del respaldo; --no-owner evita depender del usuario original.
docker compose exec -T db pg_restore -U $usuario -d $BaseDestino --no-owner --exit-on-error /tmp/restaurar.dump
if ($LASTEXITCODE -ne 0) { throw "pg_restore fallo; la base $BaseDestino quedo incompleta, no la uses" }
docker compose exec -T db rm -f /tmp/restaurar.dump | Out-Null

# 3) Comprobacion: filas por tabla.
$sql = "SELECT table_name || ': ' || (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
docker compose exec -T db psql -U $usuario -d $BaseDestino -At -c $sql
Write-Host "Restauracion terminada en la base '$BaseDestino'."

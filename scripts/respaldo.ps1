# Copia de seguridad: base de datos (pg_dump formato custom) + documentos subidos. (Solo ASCII: PowerShell 5.1 lee mal los acentos sin BOM.)
# Uso:  .\scripts\respaldo.ps1 [-Carpeta respaldos] [-Conservar 14]
# IMPORTANTE: este respaldo NO incluye .env. Guarda SECRET_KEY y DATA_ENCRYPTION_KEY en otro lugar seguro: sin DATA_ENCRYPTION_KEY los
# documentos de los pacientes del respaldo NO se pueden descifrar. El respaldo contiene datos personales: guardalo en un disco cifrado.
param([string]$Carpeta = "respaldos", [int]$Conservar = 14)
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
$base = Leer-Env "POSTGRES_DB" "recep_salud"
$sello = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force $Carpeta | Out-Null
$dump = Join-Path $Carpeta "recep-$sello.dump"

# El volcado se hace dentro del contenedor y se copia: redirigir binarios en PowerShell 5.1 los corrompe (convierte a UTF-16).
docker compose exec -T db pg_dump -U $usuario -d $base -Fc -f /tmp/respaldo.dump
if ($LASTEXITCODE -ne 0) { throw "pg_dump fallo (la base esta encendida? docker compose up -d db)" }
docker compose exec -T db pg_restore -l /tmp/respaldo.dump | Out-Null
if ($LASTEXITCODE -ne 0) { throw "El respaldo generado no se puede leer: no se guarda" }
docker compose cp db:/tmp/respaldo.dump $dump | Out-Null
docker compose exec -T db rm -f /tmp/respaldo.dump | Out-Null
if (-not (Test-Path $dump) -or (Get-Item $dump).Length -lt 1000) { throw "El respaldo quedo vacio o incompleto" }

# Documentos subidos (si existen en la carpeta local; en Docker estan en el volumen 'uploads': usar docker compose cp api:/srv/data/uploads).
$subidas = "data/uploads"
$zip = $null
if ((Test-Path $subidas) -and (Get-ChildItem $subidas -Recurse -File | Select-Object -First 1)) {
    $zip = Join-Path $Carpeta "recep-$sello-documentos.zip"
    Compress-Archive -Path "$subidas/*" -DestinationPath $zip
}

$archivos = @($dump) + $(if ($zip) { @($zip) } else { @() })
foreach ($a in $archivos) {
    $h = (Get-FileHash $a -Algorithm SHA256).Hash
    "$h  $(Split-Path $a -Leaf)" | Set-Content -Encoding ASCII "$a.sha256"
}

# Rotacion: se conservan los ultimos N respaldos de base de datos.
Get-ChildItem $Carpeta -Filter "recep-*.dump" | Sort-Object Name -Descending | Select-Object -Skip $Conservar | ForEach-Object {
    $raiz = $_.FullName -replace "\.dump$", ""
    Remove-Item "$raiz*" -Force
}

Write-Host "Respaldo listo: $dump ($([math]::Round((Get-Item $dump).Length / 1KB)) KB)"
if ($zip) { Write-Host "Documentos:     $zip" }
Write-Host "RECUERDA: guarda aparte SECRET_KEY y DATA_ENCRYPTION_KEY (no estan en el respaldo) y cifra el disco donde guardas esto."

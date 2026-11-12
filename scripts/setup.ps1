# Prepara el entorno local: venv, dependencias y .env con claves aleatorias.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.\.venv\Scripts\python.exe -m pip install --quiet -r requirements-dev.txt

if (-not (Test-Path .env)) {
    function Nueva-Clave($n) { -join ((48..57 + 65..90 + 97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ }) }
    $pg = Nueva-Clave 24
    $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    $fernet = [Convert]::ToBase64String($b).Replace('+','-').Replace('/','_')
    (Get-Content .env.example -Encoding UTF8) |
        ForEach-Object { $_ -replace '^SECRET_KEY=.*', ("SECRET_KEY=" + (Nueva-Clave 48)) `
                           -replace '^DATA_ENCRYPTION_KEY=.*', "DATA_ENCRYPTION_KEY=$fernet" `
                           -replace '^POSTGRES_PASSWORD=.*', "POSTGRES_PASSWORD=$pg" `
                           -replace '^DATABASE_URL=.*', "DATABASE_URL=postgresql+psycopg://recep:$pg@127.0.0.1:5432/recep_salud" } |
        Set-Content .env -Encoding UTF8
    Write-Host ".env creado con claves aleatorias."
}
Write-Host "Listo. Siguiente: docker compose up -d db ; .\scripts\dev.ps1"

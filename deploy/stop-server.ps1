# TPV Moderno — parada del servidor.
#   powershell -NoProfile -ExecutionPolicy Bypass -File stop-server.ps1
#
# Cortar el proceso es seguro: las transacciones viven en PostgreSQL y cualquier
# operación a medias hace rollback al reconectar; nunca queda nada a medias.

param([string]$InstallRoot = 'C:\TPV')

. "$PSScriptRoot\common.ps1"

$pidFile = Join-Path $InstallRoot 'run\server.pid'
if (-not (Test-Path $pidFile)) {
    Write-Ok "No hay registro de servidor en marcha (no había nada que parar)."
    exit 0
}

$serverPid = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $serverPid -or -not (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Ok "El servidor no estaba en marcha."
    exit 0
}

Write-Step "Parando el servidor (PID $serverPid)…"
Stop-Process -Id $serverPid -Force
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
Write-Ok "Servidor parado."

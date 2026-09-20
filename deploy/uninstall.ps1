# TPV Moderno — desinstalación del servidor.
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
#
# Quita: servidor, tareas automáticas, regla de firewall y el servicio de
# PostgreSQL propio. POR DEFECTO NO borra datos ni copias de seguridad: los
# ficheros quedan en la carpeta de instalación para que decidas con calma.
# Con -RemoveData pregunta antes de borrar TODO (irrecuperable).

param(
    [string]$InstallRoot = 'C:\TPV',
    [switch]$RemoveData
)

. "$PSScriptRoot\common.ps1"

function Prompt-SN([string]$question) {
    while ($true) {
        $a = Read-Host "$question  [s/n]"
        if ($a -match '^[sS]') { return $true }
        if ($a -eq '' -or $a -match '^[nN]') { return $false }
    }
}

Write-Step 'Parando el servidor…'
$stopPs = Join-Path $PSScriptRoot 'stop-server.ps1'
if (Test-Path $stopPs) { & $stopPs -InstallRoot $InstallRoot }

Write-Step 'Quitando tareas automáticas…'
foreach ($task in @('TPV-Servidor', 'TPV-Backup')) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Ok 'Tareas quitadas'

Write-Step 'Quitando la regla del firewall…'
Remove-NetFirewallRule -DisplayName 'TPV Servidor (HTTP)' -ErrorAction SilentlyContinue
Write-Ok 'Firewall cerrado'

Write-Step 'Parando y quitando el servicio de PostgreSQL propio…'
$serviceName = 'TPV-PostgreSQL'
$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq 'Running') { Stop-Service -Name $serviceName -Force }
    $pgCtl = Join-Path $InstallRoot 'pgsql\bin\pg_ctl.exe'
    if (Test-Path $pgCtl) {
        & $pgCtl -D (Join-Path $InstallRoot 'pgdata') unregister -N $serviceName
    }
    Write-Ok "Servicio $serviceName desregistrado"
} else {
    Write-Ok 'No había servicio propio de PostgreSQL (nada que quitar)'
}

if ($RemoveData) {
    Write-Host ''
    Write-Warn2 'Esto BORRARÁ la base de datos y TODAS las copias de seguridad. No hay vuelta atrás.'
    if (Prompt-SN '¿Borrar todo el contenido de la carpeta de instalación?') {
        Remove-Item $InstallRoot -Recurse -Force
        Write-Ok 'Instalación borrada por completo.'
    } else {
        Write-Ok "Nada borrado. Los ficheros siguen en $InstallRoot"
    }
} else {
    Write-Ok "Los ficheros (incluidas las copias en backups\) siguen en $InstallRoot."
    Write-Host '  Cuando estés seguro de que no los necesitas, borra la carpeta desde el Explorador.'
}

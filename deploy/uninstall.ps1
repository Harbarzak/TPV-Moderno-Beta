# TPV Moderno — desinstalación del servidor (Windows).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
#       Para el servicio, quita tareas programadas y regla de firewall.
#       NO borra datos ni nada instalado en el sistema.
#
#   ... -File uninstall.ps1 -RemoveData
#       Además pregunta si borrar la base de datos y la carpeta de instalación
#       completa (venv, frontends compilados, logs, copias de seguridad).
#
#   ... -File uninstall.ps1 -Purge
#       Desinstalación COMPLETA: todo lo de -RemoveData, más (con
#       confirmación en cada paso) el rol y la base de datos de PostgreSQL
#       AUNQUE uses un PostgreSQL que ya tuvieras instalado (no solo el
#       propio TPV-PostgreSQL), el servicio TPV-PostgreSQL si es el que
#       instaló install.ps1, y opcionalmente el código fuente clonado del
#       repositorio (RepoRoot). Pensado para no dejar NADA huérfano si el
#       servidor se retira por completo.
#
# Nada de esto toca un PostgreSQL de terceros salvo para borrar el rol/BD
# propios del TPV (tpv_app / tpv), y solo si confirmas la pregunta.

param(
    [string]$InstallRoot = 'C:\TPV',
    [switch]$RemoveData,
    [switch]$Purge
)

. "$PSScriptRoot\common.ps1"

if ($Purge) { $RemoveData = $true }

function Prompt-SN([string]$question) {
    while ($true) {
        $a = Read-Host "$question  [s/n]"
        if ($a -match '^[sS]') { return $true }
        if ($a -eq '' -or $a -match '^[nN]') { return $false }
    }
}

Write-Host ''
Write-Host '==============================================================' -ForegroundColor Cyan
Write-Host '   DESINSTALADOR DEL SERVIDOR TPV MODERNO (Windows)' -ForegroundColor Cyan
Write-Host '==============================================================' -ForegroundColor Cyan
Write-Host ''

# ------------------------------------------------------------ 1. Servidor
Write-Step 'Parando el servidor…'
$stopPs = Join-Path $PSScriptRoot 'stop-server.ps1'
if (Test-Path $stopPs) { & $stopPs -InstallRoot $InstallRoot }

# --------------------------------------------------- 2. Tareas automáticas
Write-Step 'Quitando tareas automáticas…'
foreach ($task in @('TPV-Servidor', 'TPV-Backup')) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Ok 'Tareas quitadas'

# ------------------------------------------------------------- 3. Firewall
Write-Step 'Quitando la regla del firewall…'
Remove-NetFirewallRule -DisplayName 'TPV Servidor (HTTP)' -ErrorAction SilentlyContinue
Write-Ok 'Firewall cerrado'

# ------------------------------------------------------- 4. Config y datos
$confDir = Join-Path $InstallRoot 'conf'
$envPath = Join-Path $confDir '.env'
$pathsFile = Join-Path $confDir 'paths.txt'
$cfg = @{}
if (Test-Path $envPath) { $cfg = Import-TpvEnv $envPath }
$repoRoot = $null
if (Test-Path $pathsFile) {
    foreach ($line in Get-Content $pathsFile -Encoding UTF8) {
        if ($line -match '^repo=(.+)$') { $repoRoot = $Matches[1].Trim() }
    }
}

if ($RemoveData) {
    Write-Host ''
    Write-Warn2 'Esto BORRARÁ la base de datos y TODAS las copias de seguridad. No hay vuelta atrás.'

    # El rol y la BD del TPV pueden vivir en un PostgreSQL propio
    # (TPV-PostgreSQL) o en uno que ya tuvieras instalado y reutilizaste: en
    # ambos casos hay que soltarlos explícitamente para no dejarlos huérfanos
    # en el servidor de BD, aunque luego no se borre $InstallRoot.
    $serviceName = 'TPV-PostgreSQL'
    $svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $pgBin = $null
    if (Test-Path (Join-Path $InstallRoot 'pgsql\bin\psql.exe')) {
        $pgBin = Join-Path $InstallRoot 'pgsql\bin'
    } else {
        $cmd = Get-Command psql -ErrorAction SilentlyContinue
        if ($cmd) { $pgBin = Split-Path $cmd.Source -Parent }
        elseif (Test-Path 'C:\Program Files\PostgreSQL') {
            foreach ($v in (Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory | Sort-Object Name -Descending)) {
                if (Test-Path (Join-Path $v.FullName 'bin\psql.exe')) { $pgBin = Join-Path $v.FullName 'bin'; break }
            }
        }
    }

    if ($pgBin -and (Prompt-SN '¿Borrar también el rol y la base de datos de PostgreSQL (tpv_app / tpv)?')) {
        $psql = Join-Path $pgBin 'psql.exe'
        $dbPort = if ($cfg.Contains('TPV_DATABASE_URL') -and $cfg['TPV_DATABASE_URL'] -match ':(\d+)/') { $Matches[1] } else { '5432' }
        $superPass = $cfg['TPV_PG_SUPERUSER_PASSWORD']
        if (-not $superPass) {
            Write-Host '  Necesito la contraseña del administrador "postgres" de PostgreSQL para borrar el rol y la BD.'
            $sec = Read-Host '  Contraseña de "postgres" (Enter para omitir este paso)' -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try { $superPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        }
        if ($superPass) {
            $env:PGPASSWORD = $superPass
            try {
                $out1 = & $psql -h localhost -p $dbPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS tpv;' 2>&1
                $out2 = & $psql -h localhost -p $dbPort -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'DROP ROLE IF EXISTS tpv_app;' 2>&1
                if ($LASTEXITCODE -ne 0) {
                    Write-Warn2 "psql devolvió error al borrar: $out1 $out2"
                } else {
                    Write-Ok 'Base de datos y rol de PostgreSQL eliminados'
                }
            } catch {
                Write-Warn2 "No pude borrar la base de datos/rol: $($_.Exception.Message). Bórralos a mano si hace falta."
            } finally {
                Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
            }
        } else {
            Write-Warn2 'Sin contraseña de "postgres": no se borró la base de datos ni el rol. Bórralos a mano si hace falta.'
        }
    }

    # Servicio TPV-PostgreSQL propio (solo si lo creó install.ps1 — un
    # PostgreSQL ya existente y reutilizado nunca tiene este servicio, así
    # que aquí nunca se toca un PostgreSQL que no instalamos nosotros).
    Write-Step 'Parando y quitando el servicio de PostgreSQL propio (si existe)…'
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

    if (Prompt-SN "¿Borrar todo el contenido de $InstallRoot (venv, frontends compilados, logs, backups, PostgreSQL propio si lo hay)?") {
        Remove-Item $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "Instalación borrada por completo: $InstallRoot"
    } else {
        Write-Ok "Nada borrado en $InstallRoot."
    }
} else {
    Write-Ok "Los ficheros (incluidas las copias en backups\) siguen en $InstallRoot."
    Write-Host '  Para borrarlos también, usa -RemoveData. Para una desinstalación completa, -Purge.'
}

# --------------------------------------------------------- 5. Purga total
if ($Purge) {
    Write-Host ''
    Write-Step 'Desinstalación completa: código fuente clonado…'
    if ($repoRoot -and (Test-Path $repoRoot)) {
        Write-Host "  El código fuente del proyecto sigue en: $repoRoot"
        if (Prompt-SN '¿Borrar también esa carpeta con el código fuente?') {
            Remove-Item $repoRoot -Recurse -Force -ErrorAction SilentlyContinue
            Write-Ok "Código fuente borrado: $repoRoot"
        } else {
            Write-Ok "Código fuente conservado en $repoRoot"
        }
    } else {
        Write-Ok 'No encuentro la ruta del código fuente (conf\paths.txt); nada que borrar ahí.'
    }
    Write-Host ''
    Write-Host 'Desinstalación completa terminada. No debería quedar nada del TPV en este equipo' -ForegroundColor Green
    Write-Host '(salvo lo que hayas decidido conservar en las preguntas anteriores).'
}

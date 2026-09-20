# TPV Moderno — arranque del servidor (API + frontends servidos, §1.4).
#
# Uso normal (doble clic o tarea programada al arrancar Windows):
#   powershell -NoProfile -ExecutionPolicy Bypass -File start-server.ps1
# Diagnóstico (el servidor corre en ESTA consola; Ctrl+C para parar):
#   powershell -NoProfile -ExecutionPolicy Bypass -File start-server.ps1 -Foreground
#
# La configuración se lee de conf\.env (generado por install.ps1). Los logs van
# a logs\tpv-FECHA.log, un fichero por día, purgados a los 30 días (§11).

param(
    [string]$InstallRoot = 'C:\TPV',
    [switch]$Foreground
)

. "$PSScriptRoot\common.ps1"

$confDir   = Join-Path $InstallRoot 'conf'
$envFile   = Join-Path $confDir '.env'
$pathsFile = Join-Path $confDir 'paths.txt'
$runDir    = Join-Path $InstallRoot 'run'
$logDir    = Join-Path $InstallRoot 'logs'
$pidFile   = Join-Path $runDir 'server.pid'

# Dónde está el código (lo anota install.ps1 en conf\paths.txt).
$repoRoot = $null
if (Test-Path $pathsFile) {
    foreach ($line in Get-Content $pathsFile -Encoding UTF8) {
        if ($line -match '^repo=(.+)$') { $repoRoot = $Matches[1].Trim() }
    }
}
if (-not $repoRoot -or -not (Test-Path (Join-Path $repoRoot 'backend'))) {
    Exit-WithError "No encuentro el código del servidor (falta '$pathsFile' con la línea repo=...). Vuelve a ejecutar install.ps1."
}
$backendDir = Join-Path $repoRoot 'backend'

if (-not (Test-Path $envFile)) { Exit-WithError "Falta '$envFile'. Ejecuta primero install.ps1." }
$cfg = Import-TpvEnv $envFile
$bind = if ($cfg.ContainsKey('TPV_BIND')) { $cfg['TPV_BIND'] } else { '0.0.0.0' }
$port = if ($cfg.ContainsKey('TPV_PORT')) { $cfg['TPV_PORT'] } else { '8000' }

# Variables TPV_* del .env al entorno del proceso: la app las prefiere al .env y
# así arranca igual desde cualquier carpeta de trabajo (incluida la tarea SYSTEM).
foreach ($key in $cfg.Keys) {
    if ($key -like 'TPV_*') { Set-Item -Path ("Env:\" + $key) -Value $cfg[$key] }
}
# Se arranca con `python -m app.serve`, no con la CLI de uvicorn: psycopg async
# exige un SelectorEventLoop y uvicorn elegiría el Proactor por defecto en
# Windows (toda petición a la BD fallaría). app.serve lee host/puerto del entorno.
Set-Item -Path 'Env:\TPV_BIND' -Value $bind
Set-Item -Path 'Env:\TPV_PORT' -Value $port

$venvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Exit-WithError "Falta el Python del servidor: $venvPython. Ejecuta primero install.ps1."
}

if ($Foreground) {
    Write-Step "Servidor en primer plano (Ctrl+C para parar). Abre http://localhost:$port/app/tpv/"
    Push-Location $backendDir
    try { & $venvPython -m app.serve }
    finally { Pop-Location }
    return
}

# ¿Ya está en marcha?
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Ok "El servidor ya está en marcha (PID $old). Para pararlo usa stop-server.ps1."
        exit 0
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $runDir, $logDir | Out-Null

# Un log por día (§11); se purgan los de más de 30 días.
$stamp  = Get-Date -Format 'yyyyMMdd'
$outLog = Join-Path $logDir "tpv-$stamp.log"
$errLog = Join-Path $logDir "tpv-$stamp.err.log"
Get-ChildItem $logDir -Filter 'tpv-*.log' -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force

Write-Step "Arrancando el servidor TPV (puerto $port)…"
$proc = Start-Process -FilePath $venvPython `
    -ArgumentList @('-m', 'app.serve') `
    -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Set-Content -Path $pidFile -Value $proc.Id

# Salud: espera hasta 45 s a que /healthz responda.
$ready = $false
foreach ($i in 1..45) {
    Start-Sleep -Seconds 1
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/v1/healthz" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    if ($proc.HasExited) {
        Write-Host "  ERROR  El proceso terminó al arrancar (código $($proc.ExitCode)). Últimas líneas:" -ForegroundColor Red
        Get-Content $errLog -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
        Write-Host "  Pista habitual: credenciales o carpetas mal en $envFile" -ForegroundColor Yellow
    } else {
        Write-Warn2 "El servidor no responde tras 45 s. Revisa $errLog"
    }
    exit 1
}

$ip = Get-LanIPv4
Write-Ok "Servidor en marcha (PID $($proc.Id))."
if ($ip) {
    Write-Host ""
    Write-Host "  Mostrador : http://$ip`:$port/app/tpv/" -ForegroundColor White
    Write-Host "  Móvil     : http://$ip`:$port/app/movil/  (en el móvil: 'Añadir a pantalla de inicio')" -ForegroundColor White
    Write-Host ""
}
"OK $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') PID=$($proc.Id) puerto=$port" |
    Out-File -FilePath (Join-Path $confDir 'last-start.txt') -Encoding utf8

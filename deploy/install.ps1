# TPV Moderno — instalador del servidor para Windows.
#
# EJECUTAR COMO ADMINISTRADOR: clic derecho sobre instalar.bat → "Ejecutar como
# administrador", o desde PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
#
# Qué hace (preguntando en español, sin tocar nada fuera de su carpeta):
#   1. Comprueba requisitos (Windows, administrador, Python).
#   2. PostgreSQL: usa el que haya o instala el suyo propio (servicio "TPV-PostgreSQL").
#   3. Genera conf\.env con secretos NUEVOS y rotados (nunca prefijados, ADR-008).
#   4. Crea la base de datos, aplica migraciones y carga los datos iniciales.
#   5. Pide los datos del primer usuario administrador.
#   6. Compila los frontends (mostrador y móvil) y los deja servidos por la API.
#   7. Registra el arranque automático con Windows y las copias de seguridad.
#   8. Abre el firewall en el puerto del servidor y lo arranca.
#
# Si ya estaba instalado se puede volver a ejecutar: pregunta si conservar la
# configuración anterior (por defecto sí) y sólo actualiza código y frontends.

param(
    [string]$InstallRoot = 'C:\TPV',
    [string]$RepoRoot = '',
    # Ruta local a un .zip de binarios PostgreSQL (para instalar SIN Internet).
    [string]$PgZip = '',
    [int]$DbPort = 5432
)

. "$PSScriptRoot\common.ps1"

# Dependencias del backend (las mismas que pyproject.toml): se fijan aquí para
# instalarlas con pip sin empaquetar el proyecto como paquete Python.
$PythonDeps = @(
    'fastapi>=0.115', 'uvicorn[standard]>=0.30', 'pydantic-settings>=2.0',
    'structlog>=24.1', 'sqlalchemy>=2.0', 'alembic>=1.13',
    'psycopg[binary]>=3.1', 'argon2-cffi>=23.1', 'pyjwt>=2.8'
)

function Prompt-SN([string]$question) {
    while ($true) {
        $a = Read-Host "$question  [S/n]"
        if ($a -eq '' -or $a -match '^[sS]') { return $true }
        if ($a -match '^[nN]') { return $false }
        Write-Host '  Responde S o n.' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '==============================================================' -ForegroundColor Cyan
Write-Host '   INSTALADOR DEL SERVIDOR TPV MODERNO' -ForegroundColor Cyan
Write-Host '==============================================================' -ForegroundColor Cyan
Write-Host ''

# ---------------------------------------------------------------- 1. Requisitos
Write-Step 'Comprobando requisitos…'

if ($env:OS -ne 'Windows_NT') {
    Exit-WithError 'Este instalador es para Windows.'
}
if (-not (Test-IsAdmin)) {
    Exit-WithError 'Hacen falta permisos de administrador (servicio de arranque, firewall). Cierra esta ventana, haz clic derecho en instalar.bat y elige "Ejecutar como administrador".'
}

# Dónde está el código: parámetro → conf\paths.txt → carpeta padre de deploy\.
$confDir = Join-Path $InstallRoot 'conf'
$pathsFile = Join-Path $confDir 'paths.txt'
if (-not $RepoRoot -and (Test-Path $pathsFile)) {
    foreach ($line in Get-Content $pathsFile -Encoding UTF8) {
        if ($line -match '^repo=(.+)$') { $RepoRoot = $Matches[1].Trim() }
    }
}
if (-not $RepoRoot) {
    $candidate = Split-Path $PSScriptRoot -Parent
    if (Test-Path (Join-Path $candidate 'backend\app')) { $RepoRoot = $candidate }
}
if (-not $RepoRoot -or -not (Test-Path (Join-Path $RepoRoot 'backend\app'))) {
    Exit-WithError "No encuentro el código del TPV (carpeta con backend\ dentro). Vuelve a lanzar el instalador con -RepoRoot `"C:\ruta\estructura_data`"."
}

$py = Find-Python
if (-not $py) {
    Exit-WithError 'No encuentro Python 3.12 o más nuevo. Instálalo desde https://www.python.org/downloads/ marcando la casilla "Add python.exe to PATH" y vuelve a ejecutar este instalador.'
}
Write-Ok "Windows y administrador: correctos. Python: $($py.Exe) $($py.Args -join ' ')"

# Estructura de carpetas (se crean todas, también las vacías: los montajes de
# frontends apuntan aquí y la app no debe fallar al arrancar por falta de ruta).
$layout = @(
    $confDir,
    (Join-Path $InstallRoot 'logs'),
    (Join-Path $InstallRoot 'run'),
    (Join-Path $InstallRoot 'data'),
    (Join-Path $InstallRoot 'bin'),
    (Join-Path $InstallRoot 'frontend\tpv'),
    (Join-Path $InstallRoot 'frontend\movil'),
    (Join-Path $InstallRoot 'backups\daily'),
    (Join-Path $InstallRoot 'backups\weekly'),
    (Join-Path $InstallRoot 'backups\monthly'),
    (Join-Path $InstallRoot 'pgsql'),
    (Join-Path $InstallRoot 'pgdata')
)
foreach ($dir in $layout) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Write-Ok "Carpetas listas en $InstallRoot"

$backendDir = Join-Path $RepoRoot 'backend'

# ---------------------------------------------------------------- 2. PostgreSQL
Write-Step 'Preparando PostgreSQL…'

function Find-PgHome {
    $own = Join-Path $InstallRoot 'pgsql\bin\pg_ctl.exe'
    if (Test-Path $own) { return (Split-Path $own -Parent | Split-Path -Parent) }
    if (Test-Path 'C:\Program Files\PostgreSQL') {
        foreach ($v in (Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory | Sort-Object Name -Descending)) {
            if (Test-Path (Join-Path $v.FullName 'bin\pg_ctl.exe')) { return $v.FullName }
        }
    }
    $cmd = Get-Command pg_ctl -ErrorAction SilentlyContinue
    if ($cmd) { return (Split-Path $cmd.Source -Parent | Split-Path -Parent) }
    return $null
}

$pgHome = Find-PgHome
$pgData = Join-Path $InstallRoot 'pgdata'
$serviceName = 'TPV-PostgreSQL'

if (-not $pgHome) {
    Write-Step 'Consiguiendo PostgreSQL (binarios oficiales)…'
    $zipPath = $PgZip
    if (-not $zipPath) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $zipUrl = 'https://get.enterprisedb.com/postgresql/postgresql-16.9-1-windows-x64-binaries.zip'
        $zipPath = Join-Path $env:TEMP 'tpv-postgresql.zip'
        try {
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
        } catch {
            Exit-WithError "No pude descargar PostgreSQL ($($_.Exception.Message)). Si este equipo no tiene Internet, descarga en otro PC el zip «postgresql-…-windows-x64-binaries.zip» y vuelve a lanzar el instalador con -PgZip `"C:\ruta\fichero.zip`"."
        }
    } elseif (-not (Test-Path $zipPath)) {
        Exit-WithError "No existe el fichero -PgZip: $zipPath"
    }
    Write-Step 'Descomprimiendo PostgreSQL…'
    $tmpZip = Join-Path $env:TEMP 'tpv-pg-unzip'
    if (Test-Path $tmpZip) { Remove-Item $tmpZip -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $tmpZip -Force
    $inner = Get-ChildItem $tmpZip -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'bin\pg_ctl.exe') } |
        Select-Object -First 1
    if (-not $inner) {
        Exit-WithError 'El zip no parece de PostgreSQL (se esperaba una carpeta con bin\pg_ctl.exe).'
    }
    Copy-Item (Join-Path $inner.FullName '*') (Join-Path $InstallRoot 'pgsql') -Recurse -Force
    Remove-Item $tmpZip -Recurse -Force -ErrorAction SilentlyContinue
    $pgHome = Join-Path $InstallRoot 'pgsql'
}
Write-Ok "PostgreSQL: $pgHome"

$pgBin  = Join-Path $pgHome 'bin'
$pgCtl  = Join-Path $pgBin 'pg_ctl.exe'
$psql   = Join-Path $pgBin 'psql.exe'
$initDb = Join-Path $pgBin 'initdb.exe'
$readyExe = Join-Path $pgBin 'pg_isready.exe'

$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
$pgSuperPass = $null

if (-not $svc -and -not (Test-Path (Join-Path $pgData 'PG_VERSION'))) {
    # Almacén de datos nuevo: contraseña del superusuario GENERADA (ADR-008).
    $pgSuperPass = New-TpvSecret 24
    Write-Step 'Inicializando el almacén de datos (solo la primera vez)…'
    $pwFile = Join-Path $env:TEMP 'tpv-pg-pwfile.txt'
    Set-Content -Path $pwFile -Value $pgSuperPass -NoNewline -Encoding ascii
    try {
        & $initDb -D $pgData -U postgres --pwfile="$pwFile" -E UTF8 --locale=C --auth=scram-sha-256
        if ($LASTEXITCODE -ne 0) { Exit-WithError 'La inicialización de PostgreSQL falló (revisa el mensaje anterior).' }
    } finally {
        Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
    }
    Write-Step "Registrando el servicio de Windows '$serviceName'…"
    & $pgCtl -D $pgData register -N $serviceName -o "-p $DbPort"
    if ($LASTEXITCODE -ne 0) {
        Exit-WithError "No pude registrar el servicio $serviceName (¿estás en una ventana de administrador?)."
    }
    Start-Service -Name $serviceName
}
elseif ($svc) {
    Write-Ok "Servicio $serviceName ya registrado (se reutiliza)."
    if ($svc.Status -ne 'Running') { Start-Service -Name $serviceName }
}

# ¿Responde ya en el puerto?
$dbUp = $false
foreach ($i in 1..30) {
    & $readyExe -h localhost -p $DbPort -q
    if ($LASTEXITCODE -eq 0) { $dbUp = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $dbUp) {
    Exit-WithError "PostgreSQL no responde en localhost:$DbPort tras 30 s. Prueba a reiniciar el equipo; si sigue igual, llama al técnico."
}
Write-Ok "PostgreSQL a la escucha en el puerto $DbPort"

# ---------------------------------------------------------------- 3. Configuración
Write-Step 'Preparando la configuración (conf\.env)…'

$envPath = Join-Path $confDir '.env'
$cfg = [ordered]@{}
$keepEnv = $false

if (Test-Path $envPath) {
    $keepEnv = Prompt-SN 'Ya existe una configuración anterior. ¿Conservarla (mismas contraseñas y secretos)?'
    if ($keepEnv) {
        $old = Import-TpvEnv $envPath
        foreach ($k in $old.Keys) { $cfg[$k] = $old[$k] }
    }
}

# Contraseña de la aplicación: nueva, o leída de la URL conservada.
$dbAppPass = $null
if ($keepEnv -and $cfg['TPV_DATABASE_URL'] -match '://[^:]+:([^@]+)@') {
    $dbAppPass = $Matches[1]
}

if (-not $keepEnv) {
    # Todo NUEVO y rotado (ADR-008): nada se hereda de plantillas ni de otras
    # instalaciones. La contraseña del superusuario solo queda en conf\.env.
    if (-not $pgSuperPass) {
        if ($cfg.Contains('TPV_PG_SUPERUSER_PASSWORD')) {
            $pgSuperPass = $cfg['TPV_PG_SUPERUSER_PASSWORD']
        } else {
            Write-Host '  (Tu PostgreSQL ya estaba instalado: necesito su contraseña de administrador'
            Write-Host '   "postgres" solo para crear el usuario de la aplicación. No se guarda en el código.)'
            $sec = Read-Host '  Contraseña de "postgres" (Enter si no la sabes)' -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try { $pgSuperPass = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        }
    }
    $dbAppPass = New-TpvSecret 24
    $cfg['TPV_ENV'] = 'prod'
    $cfg['TPV_LOG_LEVEL'] = 'INFO'
    $cfg['TPV_BIND'] = '0.0.0.0'
    $cfg['TPV_PORT'] = '8000'
    $cfg['TPV_DATABASE_URL'] = "postgresql+psycopg://tpv_app:$dbAppPass@localhost:$DbPort/tpv"
    $cfg['TPV_JWT_SECRET'] = New-TpvSecret 64
    $cfg['TPV_ACCESS_TOKEN_MINUTES'] = '15'
    $cfg['TPV_REFRESH_TOKEN_HOURS'] = '12'
    $cfg['TPV_PG_SUPERUSER_PASSWORD'] = $pgSuperPass
}
elseif (-not $dbAppPass) {
    Exit-WithError "El .env conservado no trae TPV_DATABASE_URL válida. Bórralo ($envPath) y ejecuta el instalador de nuevo."
}

# Rutas siempre al día, aunque se conserve el resto de la configuración.
$cfg['TPV_DATA_DIR'] = Join-Path $InstallRoot 'data'
$cfg['TPV_SERVE_FRONTEND_DIR'] = Join-Path $InstallRoot 'frontend\tpv'
$cfg['TPV_SERVE_MOBILE_DIR'] = Join-Path $InstallRoot 'frontend\movil'
if (-not $cfg['TPV_PORT']) { $cfg['TPV_PORT'] = '8000' }

# A fichero, con cabecera de aviso: este fichero es EL secreto del servidor.
$header = @(
    '# TPV Moderno — configuración del servidor (GENERADO por install.ps1).',
    '# CONTIENE CONTRASEÑAS: no copies este fichero, no lo envíes y no lo subas',
    '# a ningún repositorio (ADR-008). Para cambiar algo, vuelve a install.ps1.'
)
($header + ($cfg.Keys | ForEach-Object { "$_=$($cfg[$_])" })) |
    Out-File -FilePath $envPath -Encoding utf8
Write-Ok "Configuración escrita en $envPath"

# Variables al entorno para los pasos siguientes (alembic, seed, arranque).
foreach ($k in $cfg.Keys) {
    if ($k -like 'TPV_*') { Set-Item -Path ("Env:\" + $k) -Value $cfg[$k] }
}

# ---------------------------------------------------------------- 4. Base de datos
Write-Step 'Creando el usuario y la base de datos de la aplicación…'

if (-not $cfg['TPV_PG_SUPERUSER_PASSWORD']) {
    Exit-WithError 'Falta la contraseña del administrador de PostgreSQL; no puedo crear el usuario de la aplicación.'
}
function Invoke-PsqlAs([string]$User, [string]$UserPass, [string]$DbName, [string]$Sql) {
    $env:PGPASSWORD = $UserPass
    try {
        $out = & $psql -h localhost -p $DbPort -U $User -d $DbName -tAc $Sql 2>&1
        if ($LASTEXITCODE -ne 0) { Exit-WithError "PostgreSQL dijo: $out" }
        return $out
    } finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
}

$hasRole = Invoke-PsqlAs 'postgres' $cfg['TPV_PG_SUPERUSER_PASSWORD'] 'postgres' "SELECT 1 FROM pg_roles WHERE rolname='tpv_app'"
if ($hasRole) {
    # Existe: sincronizamos la contraseña con la del .env (idempotente).
    Invoke-PsqlAs 'postgres' $cfg['TPV_PG_SUPERUSER_PASSWORD'] 'postgres' "ALTER ROLE tpv_app WITH LOGIN PASSWORD '$dbAppPass'" | Out-Null
} else {
    # base64url: sin comillas ni caracteres especiales → seguro dentro del SQL.
    Invoke-PsqlAs 'postgres' $cfg['TPV_PG_SUPERUSER_PASSWORD'] 'postgres' "CREATE ROLE tpv_app LOGIN PASSWORD '$dbAppPass'" | Out-Null
}
$hasDb = Invoke-PsqlAs 'postgres' $cfg['TPV_PG_SUPERUSER_PASSWORD'] 'postgres' "SELECT 1 FROM pg_database WHERE datname='tpv'"
if (-not $hasDb) {
    Invoke-PsqlAs 'postgres' $cfg['TPV_PG_SUPERUSER_PASSWORD'] 'postgres' 'CREATE DATABASE tpv OWNER tpv_app' | Out-Null
}
Write-Ok 'Usuario tpv_app y base de datos tpv listos'

Write-Step 'Aplicando el esquema de la base de datos (migraciones)…'
$venvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Step 'Creando el entorno Python del servidor…'
    & $py.Exe @($py.Args) -m venv (Join-Path $InstallRoot 'venv')
    if ($LASTEXITCODE -ne 0) { Exit-WithError 'No pude crear el entorno Python (venv).' }
}
Write-Step 'Instalando dependencias del servidor (unos minutos)…'
& $venvPython -m pip install --disable-pip-version-check -q --upgrade pip
if ($LASTEXITCODE -ne 0) { Exit-WithError 'No pude actualizar pip (¿tiene este equipo Internet? Para instalar sin red, pide al técnico el paquete offline).' }
& $venvPython -m pip install --disable-pip-version-check -q @PythonDeps
if ($LASTEXITCODE -ne 0) { Exit-WithError 'No pude instalar las dependencias del servidor. Comprueba la conexión a Internet y vuelve a ejecutar el instalador.' }

Push-Location $backendDir
try {
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { Exit-WithError 'Las migraciones de base de datos fallaron (revisa el mensaje anterior).' }
} finally {
    Pop-Location
}
Write-Ok 'Esquema de base de datos aplicado'

# Datos iniciales (roles, IVA, formas de pago, parámetros): solo si están vacíos.
$rolesCount = Invoke-PsqlAs 'tpv_app' $dbAppPass 'tpv' 'SELECT count(*) FROM roles'
if ("$rolesCount".Trim() -eq '0') {
    Write-Step 'Cargando datos iniciales (roles, IVA, formas de pago)…'
    $seedFile = Join-Path $RepoRoot 'docs\database\seed.sql'
    $env:PGPASSWORD = $dbAppPass
    try {
        & $psql -h localhost -p $DbPort -U tpv_app -d tpv -v ON_ERROR_STOP=1 -f $seedFile
        if ($LASTEXITCODE -ne 0) { Exit-WithError 'El seed inicial falló (revisa el mensaje anterior).' }
    } finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
    Write-Ok 'Datos iniciales cargados'
} else {
    Write-Ok 'Datos iniciales ya presentes (no se tocan)'
}

# ---------------------------------------------------------------- 5. Usuario admin
Write-Host ''
Write-Step 'Usuario administrador del TPV'
if (Prompt-SN '¿Dar de alta el primer usuario administrador ahora?') {
    & (Join-Path $PSScriptRoot 'create-admin.ps1') -InstallRoot $InstallRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 'No se creó el usuario. Podrás repetirlo con Crear-usuario.bat'
    }
} else {
    Write-Warn2 'Recuerda crear el usuario antes de usar el TPV: doble clic en Crear-usuario.bat'
}

# ---------------------------------------------------------------- 6. Frontends
Write-Step 'Preparando las aplicaciones (mostrador y móvil)…'
$frontendOk = $false
$npm = Get-Command npm -ErrorAction SilentlyContinue

function Build-Frontend([string]$SrcDir, [string]$Base, [string]$DestDir) {
    Write-Step "Compilando «$(Split-Path $SrcDir -Leaf)»…"
    Push-Location $SrcDir
    try {
        if (Test-Path (Join-Path $SrcDir 'package-lock.json')) { & npm ci }
        else { & npm install }
        if ($LASTEXITCODE -ne 0) { throw 'npm no pudo instalar las dependencias' }
        # Base por CLI: los assets quedan con rutas /app/tpv o /app/movil, que es
        # donde la API los sirve (§1.4). Se llama a vite directamente; el
        # chequeo de tipos (tsc -b) ya lo garantizan desarrollo y tests.
        & npx vite build "--base=$Base"
        if ($LASTEXITCODE -ne 0) { throw 'la compilación (vite build) falló' }
        Copy-Item (Join-Path $SrcDir 'dist\*') $DestDir -Recurse -Force
    } finally {
        Pop-Location
    }
}

if (-not $npm) {
    Write-Warn2 'Node.js no está instalado: NO se compilaron las aplicaciones. El servidor funcionará (API OK) pero sin pantallas; instala Node 20+ desde https://nodejs.org y vuelve a ejecutar este instalador.'
}
else {
    try {
        Build-Frontend (Join-Path $RepoRoot 'frontend') '/app/tpv/' (Join-Path $InstallRoot 'frontend\tpv')
        Build-Frontend (Join-Path $RepoRoot 'frontend\mobile') '/app/movil/' (Join-Path $InstallRoot 'frontend\movil')
        $frontendOk = $true
        Write-Ok 'Aplicaciones compiladas y servidas por el servidor'
    } catch {
        Write-Warn2 "No pude compilar las aplicaciones: $($_.Exception.Message). La API funcionará; reintenta ejecutando este instalador otra vez."
    }
}

# ---------------------------------------------------------------- 7. Operación
Write-Step 'Instalando scripts de operación y tareas automáticas…'

$binDir = Join-Path $InstallRoot 'bin'
Copy-Item "$PSScriptRoot\*.ps1" $binDir -Force
Copy-Item "$PSScriptRoot\*.py" $binDir -Force
"repo=$RepoRoot" | Out-File -FilePath (Join-Path $confDir 'paths.txt') -Encoding utf8

# Lanzadores de doble clic en la raíz de la instalación.
$launchers = [ordered]@{
    'Arrancar-TPV.bat'            = 'start-server.ps1'
    'Parar-TPV.bat'               = 'stop-server.ps1'
    'Copia-seguridad-ahora.bat'   = 'backup-database.ps1 -Kind daily'
    'Crear-usuario.bat'           = 'create-admin.ps1'
    'Resumen-instalacion.bat'     = $null
}
foreach ($name in $launchers.Keys) {
    $target = $launchers[$name]
    if ($name -like 'Resumen*') {
        $body = "@echo off`r`nnotepad `"$confDir\install-summary.txt`"`r`n"
    } else {
        $body = "@echo off`r`ntitle TPV - $name`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$binDir\$target`"`r`npause`r`n"
    }
    $body | Out-File -FilePath (Join-Path $InstallRoot $name) -Encoding ascii
}
Copy-Item (Join-Path $PSScriptRoot '*.bat') $binDir -Force -ErrorAction SilentlyContinue

# Tarea de arranque con Windows (SYSTEM, sin sesión abierta) y una única tarea
# de copia diaria que decide la clase por fecha (1 del mes → mensual, domingo →
# semanal, resto → diaria): misma retención 7/4/12, menos piezas que mantener.
if ($InstallRoot -match '\s') {
    Write-Warn2 "La carpeta $InstallRoot tiene espacios: el Programador de tareas puede no arrancar el servidor. Usa C:\TPV."
}
$psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$tasksOk = $true
try {
    $action = New-ScheduledTaskAction -Execute $psExe `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $binDir 'start-server.ps1')`""
    Register-ScheduledTask -TaskName 'TPV-Servidor' -Action $action `
        -Trigger (New-ScheduledTaskTrigger -AtStartup) -User 'SYSTEM' -RunLevel Highest -Force | Out-Null

    $action = New-ScheduledTaskAction -Execute $psExe `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $binDir 'backup-database.ps1')`" -Kind auto"
    Register-ScheduledTask -TaskName 'TPV-Backup' -Action $action `
        -Trigger (New-ScheduledTaskTrigger -Daily -At '03:07') -User 'SYSTEM' -Force | Out-Null

    Write-Ok 'Arranque automático con Windows (TPV-Servidor) y copia diaria programada (TPV-Backup)'
} catch {
    Write-Warn2 "No pude registrar las tareas automáticas: $($_.Exception.Message)"
    $tasksOk = $false
}

# ---------------------------------------------------------------- 8. Firewall
$port = $cfg['TPV_PORT']
$fwName = 'TPV Servidor (HTTP)'
if (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $fwName -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Any | Out-Null
    Write-Ok "Firewall abierto: otros equipos pueden entrar por el puerto $port"
} else {
    Write-Warn2 "No pude abrir el firewall: pide al técnico que permita el puerto $port entrante."
}

# ---------------------------------------------------------------- 9. Arrancar
Write-Step 'Arrancando el servidor…'
& (Join-Path $binDir 'start-server.ps1') -InstallRoot $InstallRoot

# ---------------------------------------------------------------- 10. Resumen
$ip = Get-LanIPv4
$summary = @(
    'INSTALACIÓN DEL TPV MODERNO',
    "Fecha: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    '',
    "Mostrador : http://$ip`:$port/app/tpv/",
    "Móvil     : http://$ip`:$port/app/movil/   (en el móvil: abrir y «Añadir a pantalla de inicio»)",
    '',
    "Instalación           : $InstallRoot",
    "Código del programa   : $RepoRoot",
    'Config y CONTRASEÑAS  : ' + (Join-Path $confDir '.env') + '   (NO compartir este fichero)',
    'Logs (30 días)        : ' + (Join-Path $InstallRoot 'logs'),
    'Copias de seguridad   : ' + (Join-Path $InstallRoot 'backups') + '\daily|weekly|monthly',
    'Última copia          : ' + (Join-Path $confDir 'last-backup.txt'),
    '',
    'Automático: el servidor arranca al encender el equipo (tarea TPV-Servidor).',
    'Copias: una al día a las 03:07 — diarias ×7, semanales ×4, mensuales ×12.',
    '',
    'Para restaurar una copia (técnico):',
    "  pg_restore -h localhost -p $DbPort -U tpv_app -d tpv --clean --if-exists <fichero.dump>",
    '',
    'Otros usuarios: doble clic en Crear-usuario.bat'
)
if (-not $frontendOk) {
    $summary += ''
    $summary += 'PENDIENTE: instalar Node.js 20+ y volver a ejecutar install.ps1 para tener las pantallas.'
}
$summary | Out-File -FilePath (Join-Path $confDir 'install-summary.txt') -Encoding utf8

Write-Host ''
Write-Host '==============================================================' -ForegroundColor Green
Write-Host '   INSTALACIÓN TERMINADA' -ForegroundColor Green
Write-Host '==============================================================' -ForegroundColor Green
if ($ip) {
    Write-Host "  Mostrador : http://$ip`:$port/app/tpv/"
    Write-Host "  Móvil     : http://$ip`:$port/app/movil/"
}
Write-Host "  Resumen guardado en: $(Join-Path $confDir 'install-summary.txt')"
Write-Host "  Contraseñas y configuración (NO compartir): $envPath"
Write-Host ''

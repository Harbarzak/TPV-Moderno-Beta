# TPV Moderno — copia de seguridad de la base de datos (ARCHITECTURE.md §10).
#
# Dump lógico comprimido con pg_dump + retención 7 diarios / 4 semanales / 12
# mensuales. Las credenciales NUNCA van en este fichero: se leen del .env de la
# instalación (ADR-008). Lo llama la tarea programada TPV-Backup (con -Kind auto,
# que elige daily/weekly/monthly según la fecha) y también el usuario a mano:
#   powershell -NoProfile -ExecutionPolicy Bypass -File backup-database.ps1 -Kind daily
#
# Al acabar deja el resultado en conf\last-backup.txt (estado visible sin consola).

param(
    [string]$InstallRoot = 'C:\TPV',
    # daily | weekly | monthly (cada clase con su retención). «auto» lo decide
    # la fecha — 1 del mes → monthly, domingo → weekly, resto → daily — para la
    # única tarea programada de copias (TPV-Backup, diaria).
    [ValidateSet('auto', 'daily', 'weekly', 'monthly')][string]$Kind = 'auto'
)

. "$PSScriptRoot\common.ps1"

if ($Kind -eq 'auto') {
    $Kind = if ((Get-Date).Day -eq 1) { 'monthly' }
            elseif ((Get-Date).DayOfWeek -eq [DayOfWeek]::Sunday) { 'weekly' }
            else { 'daily' }
}

$retentionDays = @{ daily = 7; weekly = 28; monthly = 365 }[$Kind]

$confDir  = Join-Path $InstallRoot 'conf'
$envFile  = Join-Path $confDir '.env'
$backupDir = Join-Path $InstallRoot ("backups\" + $Kind)
$statusFile = Join-Path $confDir 'last-backup.txt'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$cfg = Import-TpvEnv $envFile
if (-not $cfg.ContainsKey('TPV_DATABASE_URL')) {
    Exit-WithError "El .env no define TPV_DATABASE_URL"
}

# postgresql+psycopg://usuario:contrasena@host:puerto/bd  ->  partes para pg_dump
$url = $cfg['TPV_DATABASE_URL'] -replace '^postgresql\+psycopg://', ''
if ($url -notmatch '^([^:]+):([^@]+)@([^:/]+):(\d+)/(.+)$') {
    Exit-WithError "TPV_DATABASE_URL no tiene el formato esperado (usuario:contrasena@host:puerto/bd)"
}
$dbUser = $Matches[1]; $dbPass = $Matches[2]; $dbHost = $Matches[3]; $dbPort = $Matches[4]; $dbName = $Matches[5]

# pg_dump del propio PostgreSQL instalado (o del que haya en PATH).
$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) {
    foreach ($cand in @((Join-Path $InstallRoot 'pgsql\bin\pg_dump.exe')) +
             (Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory -ErrorAction SilentlyContinue |
              ForEach-Object { Join-Path $_.FullName 'bin\pg_dump.exe' })) {
        if (Test-Path $cand) { $pgDump = $cand; break }
    }
}
if (-not $pgDump) { Exit-WithError "No se encuentra pg_dump (¿PostgreSQL instalado?)" }

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$target = Join-Path $backupDir ("tpv-$stamp.dump")

$env:PGPASSWORD = $dbPass
try {
    & $pgDump --host $dbHost --port $dbPort --username $dbUser --format custom --file $target $dbName
    if ($LASTEXITCODE -ne 0) { Exit-WithError "pg_dump terminó con error (código $LASTEXITCODE)" }
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

# Retención por antigüedad real del fichero (7 diarios · 4 semanales · 12 mensuales, §10).
$limit = (Get-Date).AddDays(-$retentionDays)
Get-ChildItem $backupDir -Filter 'tpv-*.dump' |
    Where-Object { $_.LastWriteTime -lt $limit } |
    Remove-Item -Force

"OK $Kind $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -> $target" | Out-File -FilePath $statusFile -Encoding utf8
Write-Ok "Copia creada: $target"

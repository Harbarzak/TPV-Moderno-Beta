# TPV Moderno — helpers compartidos por los scripts de deploy/.
# No se ejecuta solo: los demás scripts hacen:  . "$PSScriptRoot\common.ps1"

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  AVISO  $msg" -ForegroundColor Yellow }
function Exit-WithError([string]$msg) {
    Write-Host "  ERROR  $msg" -ForegroundColor Red
    exit 1
}

# Raíz por defecto de la instalación en el servidor (creada por install.ps1).
$script:DefaultInstallRoot = 'C:\TPV'

# Lee un fichero .env (KEY=VALUE, '#' como comentario) a una hashtable.
function Import-TpvEnv([string]$Path) {
    if (-not (Test-Path $Path)) { Exit-WithError "No existe el fichero de configuración: $Path" }
    $map = @{}
    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $trim = $line.Trim()
        if ($trim -eq '' -or $trim.StartsWith('#')) { continue }
        $idx = $trim.IndexOf('=')
        if ($idx -lt 1) { continue }
        $map[$trim.Substring(0, $idx).Trim()] = $trim.Substring($idx + 1).Trim()
    }
    return $map
}

# Secreto nuevo y rotado (ADR-008): bytes del generador criptográfico del SO,
# codificados base64url. Nunca hay valores prefijados ni heredados.
function New-TpvSecret([int]$Bytes = 48) {
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($buf).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

# Primera IPv4 de la LAN (para decirle al usuario qué URL abrir en los terminales).
function Get-LanIPv4 {
    try {
        $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.PrefixOrigin -ne 'WellKnown' } |
            Sort-Object InterfaceMetric -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($ip) { return $ip.IPAddress }
    } catch { }
    return $null
}

# ¿La sesión actual es de administrador? (necesario para servicios, tareas y firewall)
function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Python >= 3.12 del servidor: devuelve el ejecutable o $null.
function Find-Python {
    foreach ($candidate in @('py', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            if ($candidate -eq 'py') {
                & $cmd -3.12 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { return @{ Exe = $cmd.Source; Args = @('-3.12') } }
            } else {
                & $cmd -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { return @{ Exe = $cmd.Source; Args = @() } }
            }
        } catch { }
    }
    return $null
}

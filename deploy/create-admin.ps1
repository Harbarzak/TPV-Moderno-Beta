# TPV Moderno — alta/restablecimiento del usuario administrador.
# Doble clic en crear-admin.bat o:
#   powershell -NoProfile -ExecutionPolicy Bypass -File create-admin.ps1
#
# Pregunta usuario, nombre y contraseña por consola; la contraseña no se ve al
# escribirla y viaja solo por memoria (stdin), nunca a ficheros ni logs.

param(
    [string]$InstallRoot = 'C:\TPV',
    [string]$Username = '',
    [string]$FullName = ''
)

. "$PSScriptRoot\common.ps1"

$envFile   = Join-Path $InstallRoot 'conf\.env'
$pathsFile = Join-Path $InstallRoot 'conf\paths.txt'
$venvPython = Join-Path $InstallRoot 'venv\Scripts\python.exe'

if (-not (Test-Path $envFile))   { Exit-WithError "Falta '$envFile'. Ejecuta primero install.ps1." }
if (-not (Test-Path $venvPython)) { Exit-WithError "Falta '$venvPython'. Ejecuta primero install.ps1." }

$repoRoot = $null
if (Test-Path $pathsFile) {
    foreach ($line in Get-Content $pathsFile -Encoding UTF8) {
        if ($line -match '^repo=(.+)$') { $repoRoot = $Matches[1].Trim() }
    }
}
if (-not $repoRoot) { Exit-WithError "Falta '$pathsFile' (línea repo=...). Vuelve a ejecutar install.ps1." }

if (-not $Username) { $Username = Read-Host "Nombre de usuario (p. ej. admin)" }
if (-not $FullName) { $FullName = Read-Host "Nombre y apellidos de esa persona" }
if (-not $Username -or -not $FullName) { Exit-WithError "Usuario y nombre son obligatorios." }

$secure = Read-Host "Contraseña (mín. 8 caracteres, no se ve al escribir)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

# La app importa desde backend\ (PYTHONPATH) y lee el .env de conf\ vía entorno.
$cfg = Import-TpvEnv $envFile
foreach ($key in $cfg.Keys) {
    if ($key -like 'TPV_*') { Set-Item -Path ("Env:\" + $key) -Value $cfg[$key] }
}
$env:PYTHONPATH = Join-Path $repoRoot 'backend'

Write-Step "Dando de alta al usuario '$Username'…"
$password | & $venvPython (Join-Path $PSScriptRoot 'create_admin.py') $Username $FullName
if ($LASTEXITCODE -ne 0) {
    Exit-WithError "No se pudo crear el usuario (revisa el mensaje anterior)."
}
Write-Ok "Listo. Ese usuario ya puede entrar en el TPV con su contraseña."

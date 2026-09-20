#!/usr/bin/env bash
# TPV Moderno — funciones compartidas por los scripts de deploy/linux/.
# No se ejecuta solo: los demás scripts hacen `source "$(dirname "$0")/common.sh"`.

set -euo pipefail

# Colores (se desactivan solos si la salida no es una terminal)
if [[ -t 1 ]]; then
    C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
else
    C_CYAN=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_RESET=''
fi

step()  { echo -e "${C_CYAN}==>${C_RESET} $1"; }
ok()    { echo -e "  ${C_GREEN}OK${C_RESET}    $1"; }
warn()  { echo -e "  ${C_YELLOW}AVISO${C_RESET} $1"; }
die()   { echo -e "  ${C_RED}ERROR${C_RESET} $1" >&2; exit 1; }

# Raíz por defecto de la instalación en el servidor.
TPV_DEFAULT_INSTALL_ROOT='/opt/tpv'

# Lee un fichero .env (CLAVE=VALOR, '#' como comentario) exportando cada
# variable TPV_* al entorno del proceso actual.
import_tpv_env() {
    local file="$1"
    [[ -f "$file" ]] || die "No existe el fichero de configuración: $file"
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%$'\r'}"
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" != *=* ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        key="$(echo -n "$key" | xargs)"
        [[ "$key" == TPV_* ]] || continue
        export "$key=$val"
    done < "$file"
}

# Secreto nuevo y rotado (ADR-008): bytes del generador criptográfico del
# kernel, en base64url. Nunca hay valores prefijados ni heredados.
new_tpv_secret() {
    local bytes="${1:-48}"
    openssl rand -base64 "$bytes" | tr '+/' '-_' | tr -d '=\n'
}

# Primera IPv4 no-loopback de la máquina (para decirle al usuario qué URL
# abrir en los terminales). Vacío si no se puede determinar.
lan_ipv4() {
    local ip=""
    if command -v ip >/dev/null 2>&1; then
        ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"
    fi
    if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    echo "$ip"
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        die "Hacen falta permisos de root (paquetes, servicio systemd, firewall). Ejecuta con: sudo $0"
    fi
}

# Python >= 3.12 disponible en PATH: imprime la ruta del ejecutable o nada.
find_python312() {
    local cand
    for cand in python3.13 python3.12 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
                command -v "$cand"
                return 0
            fi
        fi
    done
    return 1
}

# ¿Systemd disponible como PID 1? (lo exige el resto del instalador).
require_systemd() {
    if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
        die "Este instalador necesita systemd (servicio de arranque, temporizador de copias). No lo encuentro en este sistema."
    fi
}

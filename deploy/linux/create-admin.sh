#!/usr/bin/env bash
# TPV Moderno — alta/restablecimiento del usuario administrador.
#   sudo bash create-admin.sh [--install-root /opt/tpv] [--username admin] [--full-name "Nombre Apellidos"]
#
# Pregunta usuario, nombre y contraseña por consola; la contraseña no se
# muestra al escribirla y viaja solo por stdin, nunca a ficheros ni logs.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

INSTALL_ROOT="${TPV_INSTALL_ROOT:-$TPV_DEFAULT_INSTALL_ROOT}"
USERNAME=""
FULL_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --username) USERNAME="$2"; shift 2 ;;
        --full-name) FULL_NAME="$2"; shift 2 ;;
        *) die "Opción no reconocida: $1" ;;
    esac
done

ENV_FILE="$INSTALL_ROOT/conf/.env"
PATHS_FILE="$INSTALL_ROOT/conf/paths.txt"
VENV_PY="$INSTALL_ROOT/venv/bin/python"

[[ -f "$ENV_FILE" ]] || die "Falta '$ENV_FILE'. Ejecuta primero install.sh."
[[ -x "$VENV_PY" ]] || die "Falta '$VENV_PY'. Ejecuta primero install.sh."

REPO_ROOT=""
[[ -f "$PATHS_FILE" ]] && REPO_ROOT="$(grep -m1 '^repo=' "$PATHS_FILE" | cut -d= -f2-)"
[[ -n "$REPO_ROOT" ]] || die "Falta '$PATHS_FILE' (línea repo=...). Vuelve a ejecutar install.sh."

[[ -n "$USERNAME" ]] || read -r -p "Nombre de usuario (p. ej. admin): " USERNAME
[[ -n "$FULL_NAME" ]] || read -r -p "Nombre y apellidos de esa persona: " FULL_NAME
[[ -n "$USERNAME" && -n "$FULL_NAME" ]] || die "Usuario y nombre son obligatorios."

read -r -s -p "Contraseña (mín. 8 caracteres, no se ve al escribir): " PASSWORD
echo ""
[[ -n "$PASSWORD" ]] || die "La contraseña no puede estar vacía."

import_tpv_env "$ENV_FILE"
export PYTHONPATH="$REPO_ROOT/backend"

step "Dando de alta al usuario '$USERNAME'…"
printf '%s\n' "$PASSWORD" | "$VENV_PY" "$SCRIPT_DIR/../create_admin.py" "$USERNAME" "$FULL_NAME"
ok "Listo. Ese usuario ya puede entrar en el TPV con su contraseña."

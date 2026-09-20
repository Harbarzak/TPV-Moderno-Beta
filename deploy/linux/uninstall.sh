#!/usr/bin/env bash
# TPV Moderno — desinstalación del servidor nativo Linux.
#
#   sudo bash uninstall.sh                 Quita el servicio; NO borra datos
#                                           ni nada que ya estuviera en la
#                                           máquina antes de instalar el TPV.
#   sudo bash uninstall.sh --remove-data    Además pregunta si borrar la BD,
#                                           las copias de seguridad y la
#                                           carpeta de instalación.
#   sudo bash uninstall.sh --purge          Desinstalación COMPLETA: todo lo
#                                           de --remove-data, más (con
#                                           confirmación en cada paso) lo que
#                                           install.sh instaló en el sistema
#                                           fuera de la carpeta de instalación
#                                           — PostgreSQL si lo instaló él,
#                                           el usuario de sistema "tpv", y
#                                           opcionalmente el código fuente
#                                           clonado del repositorio. Pensado
#                                           para no dejar NADA huérfano si el
#                                           servidor se retira por completo.
#
# Qué es seguro revertir lo decide conf/install-manifest.txt, que install.sh
# escribe con lo que ÉL instaló (no toca un PostgreSQL, Python o usuario que
# ya existiera en la máquina por otro motivo). Sin ese fichero, --purge se
# limita a lo mismo que --remove-data y avisa de qué no puede verificar.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
require_root

INSTALL_ROOT="${TPV_INSTALL_ROOT:-$TPV_DEFAULT_INSTALL_ROOT}"
REMOVE_DATA=0
PURGE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --remove-data) REMOVE_DATA=1; shift ;;
        --purge) REMOVE_DATA=1; PURGE=1; shift ;;
        *) die "Opción no reconocida: $1" ;;
    esac
done

ask_sn() {
    local question="$1" ans
    read -r -p "$question  [s/N] " ans || ans=""
    [[ "$ans" =~ ^[sS] ]]
}

echo ""
echo -e "${C_CYAN}==============================================================${C_RESET}"
echo -e "${C_CYAN}   DESINSTALADOR DEL SERVIDOR TPV MODERNO (Linux)${C_RESET}"
echo -e "${C_CYAN}==============================================================${C_RESET}"
echo ""

# -------------------------------------------------------- 1. Servicio y timer
step 'Parando y deshabilitando el servicio…'
systemctl stop tpv.service 2>/dev/null || true
systemctl disable tpv.service 2>/dev/null || true
systemctl stop tpv-backup.timer 2>/dev/null || true
systemctl disable tpv-backup.timer 2>/dev/null || true
rm -f /etc/systemd/system/tpv.service /etc/systemd/system/tpv-backup.service /etc/systemd/system/tpv-backup.timer
rm -f /etc/logrotate.d/tpv
systemctl daemon-reload
ok 'Servicio y temporizador quitados'

# -------------------------------------------------------------- 2. Firewall
step 'Quitando la regla del firewall (si existía)…'
if command -v ufw >/dev/null 2>&1; then
    PORT="8000"
    [[ -f "$INSTALL_ROOT/conf/.env" ]] && PORT="$(grep -m1 '^TPV_PORT=' "$INSTALL_ROOT/conf/.env" | cut -d= -f2)"
    ufw delete allow "${PORT:-8000}/tcp" 2>/dev/null || true
fi
ok 'Firewall limpiado'

# --------------------------------------------------------------- Manifest
MANIFEST="$INSTALL_ROOT/conf/install-manifest.txt"
INSTALLED_PG_PKGS=0
INSTALLED_PYTHON_PKGS=0
CREATED_SYSTEM_USER=0
SYSTEM_USER="tpv"
REPO_ROOT=""
if [[ -f "$MANIFEST" ]]; then
    INSTALLED_PG_PKGS="$(grep -m1 '^installed_postgresql_pkgs=' "$MANIFEST" | cut -d= -f2 || echo 0)"
    INSTALLED_PYTHON_PKGS="$(grep -m1 '^installed_python_pkgs=' "$MANIFEST" | cut -d= -f2 || echo 0)"
    CREATED_SYSTEM_USER="$(grep -m1 '^created_system_user=' "$MANIFEST" | cut -d= -f2 || echo 0)"
    SYSTEM_USER="$(grep -m1 '^system_user=' "$MANIFEST" | cut -d= -f2 || echo tpv)"
    REPO_ROOT="$(grep -m1 '^repo_root=' "$MANIFEST" | cut -d= -f2 || echo "")"
elif [[ "$PURGE" -eq 1 ]]; then
    warn "No encuentro $MANIFEST: no sé con certeza qué instaló install.sh. --purge se limitará a los datos propios del TPV y preguntará antes de tocar nada del sistema."
fi

# --------------------------------------------------- 3. Base de datos / datos
if [[ "$REMOVE_DATA" -eq 1 ]]; then
    echo ""
    warn 'Esto BORRARÁ la base de datos y TODAS las copias de seguridad. No hay vuelta atrás.'
    if ask_sn '¿Borrar también el rol y la base de datos de PostgreSQL (tpv_app / tpv)?'; then
        sudo -u postgres psql -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS tpv;' -c 'DROP ROLE IF EXISTS tpv_app;' >/dev/null
        ok 'Base de datos y rol eliminados'
    fi
    if ask_sn "¿Borrar todo el contenido de $INSTALL_ROOT (venv, frontends compilados, logs, backups)?"; then
        rm -rf "${INSTALL_ROOT:?}"
        ok "Instalación borrada por completo: $INSTALL_ROOT"
    else
        ok "Nada borrado en $INSTALL_ROOT."
    fi
else
    ok "Los ficheros (incluidas las copias en backups/) siguen en $INSTALL_ROOT."
    echo "  Para borrarlos también, usa --remove-data. Para una desinstalación completa, --purge."
fi

# ------------------------------------------------------ 4. Purga del sistema
if [[ "$PURGE" -eq 1 ]]; then
    echo ""
    step 'Desinstalación completa: revirtiendo lo que install.sh puso en el sistema…'

    if [[ "$CREATED_SYSTEM_USER" == "1" ]] && id -u "$SYSTEM_USER" >/dev/null 2>&1; then
        if ask_sn "¿Borrar el usuario de sistema '$SYSTEM_USER' (creado por install.sh, sin login ni privilegios)?"; then
            userdel "$SYSTEM_USER" 2>/dev/null || true
            ok "Usuario '$SYSTEM_USER' eliminado"
        fi
    fi

    if [[ "$INSTALLED_PG_PKGS" == "1" ]]; then
        warn 'install.sh instaló PostgreSQL en esta máquina (no existía antes).'
        if ask_sn '¿Desinstalar PostgreSQL por completo (apt purge)? Solo di que sí si NINGÚN otro programa de este servidor lo usa'; then
            apt-get purge -y -qq postgresql postgresql-contrib 'postgresql-*' >/dev/null 2>&1 || true
            apt-get autoremove -y -qq >/dev/null 2>&1 || true
            if ask_sn '¿Borrar también los datos de PostgreSQL en disco (/var/lib/postgresql)?'; then
                rm -rf /var/lib/postgresql /etc/postgresql
                ok 'PostgreSQL desinstalado y datos borrados'
            else
                ok 'PostgreSQL desinstalado; datos conservados en /var/lib/postgresql'
            fi
        else
            ok 'PostgreSQL conservado'
        fi
    else
        ok 'PostgreSQL ya estaba en la máquina antes de instalar el TPV: no se toca.'
    fi

    if [[ "$INSTALLED_PYTHON_PKGS" == "1" ]]; then
        if ask_sn '¿Desinstalar también python3-venv/python3-pip que instaló install.sh? (deja python3 del sistema intacto)'; then
            apt-get purge -y -qq python3-venv python3-pip >/dev/null 2>&1 || true
            ok 'Paquetes de Python quitados'
        fi
    fi

    if [[ -n "$REPO_ROOT" && -d "$REPO_ROOT" ]]; then
        echo ""
        echo "  El código fuente clonado del proyecto sigue en: $REPO_ROOT"
        if ask_sn "¿Borrar también esa carpeta con el código fuente?"; then
            rm -rf "${REPO_ROOT:?}"
            ok "Código fuente borrado: $REPO_ROOT"
        else
            ok "Código fuente conservado en $REPO_ROOT"
        fi
    fi

    rm -f "$MANIFEST" 2>/dev/null || true
    echo ""
    echo -e "${C_GREEN}Desinstalación completa terminada. No debería quedar nada del TPV en este servidor${C_RESET}"
    echo "(salvo lo que hayas decidido conservar en las preguntas anteriores)."
fi

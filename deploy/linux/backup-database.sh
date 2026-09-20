#!/usr/bin/env bash
# TPV Moderno — copia de seguridad de la base de datos (ARCHITECTURE.md §10).
#
# Dump lógico comprimido con pg_dump + retención 7 diarios / 4 semanales / 12
# mensuales. Las credenciales NUNCA van en este fichero: se leen del .env de
# la instalación (ADR-008). Lo llama tpv-backup.timer (con --kind auto, que
# elige daily/weekly/monthly según la fecha) y también el usuario a mano:
#   sudo bash backup-database.sh --kind daily
#
# Al acabar deja el resultado en conf/last-backup.txt (estado visible sin systemd).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

INSTALL_ROOT="${TPV_INSTALL_ROOT:-$TPV_DEFAULT_INSTALL_ROOT}"
KIND="auto"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --kind) KIND="$2"; shift 2 ;;
        *) die "Opción no reconocida: $1" ;;
    esac
done

if [[ "$KIND" == "auto" ]]; then
    DOM="$(date +%d)"; DOW="$(date +%u)" # DOW: 1=lunes .. 7=domingo
    if [[ "$DOM" == "01" ]]; then KIND="monthly"
    elif [[ "$DOW" == "7" ]]; then KIND="weekly"
    else KIND="daily"; fi
fi
case "$KIND" in
    daily) RETENTION_DAYS=7 ;;
    weekly) RETENTION_DAYS=28 ;;
    monthly) RETENTION_DAYS=365 ;;
    *) die "--kind debe ser auto|daily|weekly|monthly" ;;
esac

CONF_DIR="$INSTALL_ROOT/conf"
ENV_FILE="$CONF_DIR/.env"
BACKUP_DIR="$INSTALL_ROOT/backups/$KIND"
STATUS_FILE="$CONF_DIR/last-backup.txt"
mkdir -p "$BACKUP_DIR"

import_tpv_env "$ENV_FILE"
[[ -n "${TPV_DATABASE_URL:-}" ]] || die "El .env no define TPV_DATABASE_URL"

# postgresql+psycopg://usuario:contrasena@host:puerto/bd  ->  partes para pg_dump
URL="${TPV_DATABASE_URL#postgresql+psycopg://}"
DB_USER="${URL%%:*}"
REST="${URL#*:}"
DB_PASS="${REST%%@*}"
REST="${REST#*@}"
DB_HOST="${REST%%:*}"
REST="${REST#*:}"
DB_PORT="${REST%%/*}"
DB_NAME="${REST#*/}"
[[ -n "$DB_USER" && -n "$DB_PASS" && -n "$DB_HOST" && -n "$DB_PORT" && -n "$DB_NAME" ]] \
    || die "TPV_DATABASE_URL no tiene el formato esperado (usuario:contrasena@host:puerto/bd)"

command -v pg_dump >/dev/null 2>&1 || die "No se encuentra pg_dump (¿PostgreSQL instalado?)"

STAMP="$(date '+%Y%m%d_%H%M%S')"
TARGET="$BACKUP_DIR/tpv-$STAMP.dump"

PGPASSWORD="$DB_PASS" pg_dump --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
    --format custom --file "$TARGET" "$DB_NAME"

# Retención por antigüedad real del fichero (7 diarios · 4 semanales · 12 mensuales, §10).
find "$BACKUP_DIR" -maxdepth 1 -name 'tpv-*.dump' -mtime "+$RETENTION_DAYS" -delete

echo "OK $KIND $(date '+%Y-%m-%d %H:%M:%S') -> $TARGET" > "$STATUS_FILE"
ok "Copia creada: $TARGET"

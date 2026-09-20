#!/bin/sh
# =============================================================================
# TPV Moderno — restauración de una copia (Docker), fase Despliegue.
#
#   ./restore.sh backups/tpv_FECHA_HORA.dump
#
# SUSTITUYE todos los datos actuales por el contenido del volcado. Pide
# escribir RESTAURAR para continuar y para la API durante la operación para
# que nadie escriba mientras se restaura. Al arrancar de nuevo, el contenedor
# aplica las migraciones pendientes (si la copia es antigua).
# =============================================================================
set -eu
cd "$(dirname "$0")"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Uso: $0 backups/tpv_FECHA_HORA.dump   (fichero generado por backup.sh)"
  exit 2
fi
DUMP="$1"

printf 'AVISO: se SUSTITUIRÁN todos los datos actuales por «%s».\nEscribe RESTAURAR para continuar: ' "${DUMP}"
read -r answer
[ "${answer}" = "RESTAURAR" ] || { echo "Cancelado."; exit 1; }

echo "Parando la API…"
docker compose stop api

echo "Cerrando conexiones activas…"
docker compose exec -T db psql -U tpv -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'tpv' AND pid <> pg_backend_pid();" \
  >/dev/null

echo "Restaurando ${DUMP}…"
docker compose exec -T db pg_restore -U tpv -d tpv --clean --if-exists < "${DUMP}"

echo "Arrancando la API (aplicará migraciones si la copia era antigua)…"
docker compose start api
echo "Listo. Verifica: curl -fsS http://localhost:${TPV_HTTP_PORT:-80}/api/v1/readyz"

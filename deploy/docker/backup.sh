#!/bin/sh
# =============================================================================
# TPV Moderno — copia de seguridad (Docker), fase Despliegue.
#
#   ./backup.sh
#
# Genera backups/tpv_FECHA_HORA.dump (formato custom de pg_dump, comprimido)
# en el ANFITRIÓN y aplica la retención: borra lo más viejo que
# BACKUP_KEEP_DAYS días, conservando SIEMPRE las BACKUP_KEEP_MIN más recientes.
#
# Automatización (crontab en el anfitrión):
#   15 3 * * * cd /ruta/deploy/docker && ./backup.sh >> backups/backup.log 2>&1
#
# La contraseña NUNCA va en argv: pg_dump corre dentro del contenedor de la BD
# y usa su socket local (el volcado viaja por stdout hasta el anfitrión).
# Cópialos fuera del servidor (NAS/USB): una copia en el mismo disco no es
# copia de seguridad.
# =============================================================================
set -eu
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
KEEP_MIN="${BACKUP_KEEP_MIN:-10}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="backups/tpv_${STAMP}.dump"
mkdir -p backups

echo "[$(date '+%F %T')] Volcando la base de datos a ${TARGET}…"
docker compose exec -T db pg_dump -U tpv -d tpv -Fc > "${TARGET}"
echo "[$(date '+%F %T')] Copia creada: ${TARGET} ($(du -h "${TARGET}" | cut -f1))."

# Retención: candidatas = todas menos las KEEP_MIN más recientes; se borran
# solo si además superan KEEP_DAYS de antigüedad.
ls -1t backups/tpv_*.dump 2>/dev/null | tail -n +"$((KEEP_MIN + 1))" | while IFS= read -r old; do
  if [ -n "$(find "$old" -mtime +"${KEEP_DAYS}" -print 2>/dev/null)" ]; then
    rm -f -- "$old"
    echo "[$(date '+%F %T')] Retención: eliminada ${old}"
  fi
done
echo "[$(date '+%F %T')] Retención aplicada: se conservan al menos ${KEEP_MIN} copias."

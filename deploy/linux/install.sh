#!/usr/bin/env bash
# TPV Moderno — instalador NATIVO del servidor para Linux (sin Docker).
#
# EJECUTAR COMO ROOT (systemd, paquetes, firewall):
#   sudo bash deploy/linux/install.sh
#
# Pensado para Debian/Ubuntu (usa apt). En otra familia de distro, instala tú
# mismo PostgreSQL 16+, Python 3.12+ y Node 20+ antes de lanzarlo: el resto
# del instalador (BD, venv, migraciones, frontends, systemd, backups) es igual.
#
# Qué hace (todo en español, sin tocar nada fuera de su carpeta de instalación):
#   1. Comprueba requisitos (root, systemd, Python 3.12+).
#   2. PostgreSQL: usa el que haya en el sistema o lo instala por apt.
#   3. Genera conf/.env con secretos NUEVOS y rotados (nunca prefijados, ADR-008).
#   4. Crea el rol y la base de datos de la aplicación, aplica migraciones y
#      carga los datos iniciales.
#   5. Pide los datos del primer usuario administrador.
#   6. Compila los tres frontends (mostrador, móvil, administración) y los
#      deja servidos por la propia API (§1.4 de ARCHITECTURE.md).
#   7. Registra el servicio systemd (arranque automático + reinicio si falla)
#      y el temporizador systemd de copias de seguridad diarias.
#   8. Abre el puerto en el firewall (ufw, si está activo) y arranca el servicio.
#
# Si ya estaba instalado, se puede volver a ejecutar: pregunta si conservar la
# configuración anterior (por defecto sí) y solo actualiza código y frontends.
#
# Relación con deploy/docker/: ese kit despliega el TPV en contenedores
# (PostgreSQL + API + Caddy). Este instalador hace lo mismo pero SIN Docker,
# instalando los servicios directamente en el sistema operativo — para
# servidores donde no se quiere o no se puede usar contenedores.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

INSTALL_ROOT="${TPV_INSTALL_ROOT:-$TPV_DEFAULT_INSTALL_ROOT}"
REPO_ROOT="${TPV_REPO_ROOT:-}"
DB_PORT="${TPV_DB_PORT:-5432}"
SERVICE_USER="tpv"
PG_SERVICE="postgresql"

usage() {
    cat <<EOF
Uso: sudo bash install.sh [opciones]

  --install-root RUTA   Dónde instalar (por defecto: $TPV_DEFAULT_INSTALL_ROOT)
  --repo-root RUTA       Dónde está el código fuente (por defecto: se detecta
                          a partir de la carpeta que contiene este script)
  --db-port PUERTO       Puerto de PostgreSQL (por defecto: 5432)
  -h, --help             Esta ayuda
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-root) INSTALL_ROOT="$2"; shift 2 ;;
        --repo-root) REPO_ROOT="$2"; shift 2 ;;
        --db-port) DB_PORT="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Opción no reconocida: $1 (usa --help)" ;;
    esac
done

ask_sn() {
    local question="$1" ans
    while true; do
        read -r -p "$question  [S/n] " ans || ans=""
        case "$ans" in
            ""|[sS]*) return 0 ;;
            [nN]*) return 1 ;;
            *) echo "  Responde S o n." ;;
        esac
    done
}

echo ""
echo -e "${C_CYAN}==============================================================${C_RESET}"
echo -e "${C_CYAN}   INSTALADOR NATIVO DEL SERVIDOR TPV MODERNO (Linux)${C_RESET}"
echo -e "${C_CYAN}==============================================================${C_RESET}"
echo ""

# ---------------------------------------------------------------- 1. Requisitos
step 'Comprobando requisitos…'
require_root
require_systemd

CONF_DIR="$INSTALL_ROOT/conf"
PATHS_FILE="$CONF_DIR/paths.txt"
if [[ -z "$REPO_ROOT" && -f "$PATHS_FILE" ]]; then
    REPO_ROOT="$(grep -m1 '^repo=' "$PATHS_FILE" 2>/dev/null | cut -d= -f2- || true)"
fi
if [[ -z "$REPO_ROOT" ]]; then
    CANDIDATE="$(cd "$SCRIPT_DIR/../.." && pwd)"
    [[ -d "$CANDIDATE/backend/app" ]] && REPO_ROOT="$CANDIDATE"
fi
[[ -n "$REPO_ROOT" && -d "$REPO_ROOT/backend/app" ]] || die "No encuentro el código del TPV (carpeta con backend/ dentro). Relanza con --repo-root /ruta/al/proyecto"

if ! command -v openssl >/dev/null 2>&1; then
    die "Falta 'openssl' (se usa para generar secretos). Instálalo: apt-get install -y openssl"
fi

HAS_APT=0
command -v apt-get >/dev/null 2>&1 && HAS_APT=1

# Manifest de lo que ESTE instalador pone en el sistema (fuera de INSTALL_ROOT):
# lo usa uninstall.sh para saber qué es seguro revertir sin tocar nada que ya
# estuviera en la máquina antes por otro motivo.
INSTALLED_PG_PKGS=0
INSTALLED_PYTHON_PKGS=0
CREATED_SYSTEM_USER=0

PYTHON_BIN="$(find_python312 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    if [[ "$HAS_APT" -eq 1 ]]; then
        step 'Python 3.12+ no encontrado: instalando python3, venv y pip por apt…'
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
        INSTALLED_PYTHON_PKGS=1
        PYTHON_BIN="$(find_python312 || true)"
    fi
fi
[[ -n "$PYTHON_BIN" ]] || die "No encuentro Python 3.12 o más nuevo. Instálalo y vuelve a ejecutar este instalador."
ok "Requisitos: root, systemd y Python OK ($PYTHON_BIN)"

# Estructura de carpetas.
BACKEND_DIR="$REPO_ROOT/backend"
mkdir -p "$CONF_DIR" "$INSTALL_ROOT/logs" "$INSTALL_ROOT/run" "$INSTALL_ROOT/data" \
         "$INSTALL_ROOT/frontend/tpv" "$INSTALL_ROOT/frontend/movil" "$INSTALL_ROOT/frontend/admin" \
         "$INSTALL_ROOT/backups/daily" "$INSTALL_ROOT/backups/weekly" "$INSTALL_ROOT/backups/monthly"
ok "Carpetas listas en $INSTALL_ROOT"

# ---------------------------------------------------------------- 2. PostgreSQL
step 'Preparando PostgreSQL…'

if ! command -v psql >/dev/null 2>&1; then
    if [[ "$HAS_APT" -eq 1 ]]; then
        step 'PostgreSQL no encontrado: instalando por apt (postgresql)…'
        apt-get update -qq
        apt-get install -y -qq postgresql postgresql-contrib >/dev/null
        INSTALLED_PG_PKGS=1
    else
        die "No encuentro PostgreSQL y este sistema no usa apt. Instala PostgreSQL 16+ manualmente y vuelve a ejecutar."
    fi
fi
systemctl enable --now "$PG_SERVICE" >/dev/null 2>&1 || systemctl enable --now postgresql.service >/dev/null 2>&1 || true

READY=0
for _ in $(seq 1 30); do
    if sudo -u postgres pg_isready -p "$DB_PORT" -q 2>/dev/null; then READY=1; break; fi
    sleep 1
done
[[ "$READY" -eq 1 ]] || die "PostgreSQL no responde en el puerto $DB_PORT tras 30 s. Revisa 'systemctl status postgresql'."
ok "PostgreSQL a la escucha en el puerto $DB_PORT"

# ---------------------------------------------------------------- 3. Configuración
step 'Preparando la configuración (conf/.env)…'

ENV_PATH="$CONF_DIR/.env"
KEEP_ENV=0
if [[ -f "$ENV_PATH" ]]; then
    if ask_sn 'Ya existe una configuración anterior. ¿Conservarla (mismas contraseñas y secretos)?'; then
        KEEP_ENV=1
        import_tpv_env "$ENV_PATH"
    fi
fi

DB_APP_PASS=""
if [[ "$KEEP_ENV" -eq 1 && -n "${TPV_DATABASE_URL:-}" ]]; then
    DB_APP_PASS="$(echo "$TPV_DATABASE_URL" | sed -n 's#.*://[^:]*:\([^@]*\)@.*#\1#p')"
fi

if [[ "$KEEP_ENV" -eq 0 ]]; then
    DB_APP_PASS="$(new_tpv_secret 24)"
    TPV_ENV='prod'
    TPV_LOG_LEVEL='INFO'
    TPV_BIND='0.0.0.0'
    TPV_PORT='8000'
    TPV_DATABASE_URL="postgresql+psycopg://tpv_app:${DB_APP_PASS}@localhost:${DB_PORT}/tpv"
    TPV_JWT_SECRET="$(new_tpv_secret 64)"
    TPV_ACCESS_TOKEN_MINUTES='15'
    TPV_REFRESH_TOKEN_HOURS='12'
elif [[ -z "$DB_APP_PASS" ]]; then
    die "El .env conservado no trae TPV_DATABASE_URL válida. Bórralo ($ENV_PATH) y ejecuta el instalador de nuevo."
fi

# Rutas siempre al día, aunque se conserve el resto de la configuración.
TPV_DATA_DIR="$INSTALL_ROOT/data"
TPV_SERVE_FRONTEND_DIR="$INSTALL_ROOT/frontend/tpv"
TPV_SERVE_MOBILE_DIR="$INSTALL_ROOT/frontend/movil"
TPV_SERVE_ADMIN_DIR="$INSTALL_ROOT/frontend/admin"
TPV_PORT="${TPV_PORT:-8000}"
TPV_BIND="${TPV_BIND:-0.0.0.0}"

{
    echo "# TPV Moderno — configuración del servidor (GENERADO por install.sh)."
    echo "# CONTIENE CONTRASEÑAS: no copies este fichero, no lo envíes y no lo subas"
    echo "# a ningún repositorio (ADR-008). Para cambiar algo, vuelve a install.sh."
    echo "TPV_ENV=$TPV_ENV"
    echo "TPV_LOG_LEVEL=$TPV_LOG_LEVEL"
    echo "TPV_BIND=$TPV_BIND"
    echo "TPV_PORT=$TPV_PORT"
    echo "TPV_DATABASE_URL=$TPV_DATABASE_URL"
    echo "TPV_JWT_SECRET=$TPV_JWT_SECRET"
    echo "TPV_ACCESS_TOKEN_MINUTES=$TPV_ACCESS_TOKEN_MINUTES"
    echo "TPV_REFRESH_TOKEN_HOURS=$TPV_REFRESH_TOKEN_HOURS"
    echo "TPV_DATA_DIR=$TPV_DATA_DIR"
    echo "TPV_SERVE_FRONTEND_DIR=$TPV_SERVE_FRONTEND_DIR"
    echo "TPV_SERVE_MOBILE_DIR=$TPV_SERVE_MOBILE_DIR"
    echo "TPV_SERVE_ADMIN_DIR=$TPV_SERVE_ADMIN_DIR"
} > "$ENV_PATH"
chmod 600 "$ENV_PATH"
ok "Configuración escrita en $ENV_PATH"

import_tpv_env "$ENV_PATH"

# ---------------------------------------------------------------- 4. Base de datos
step 'Creando el usuario y la base de datos de la aplicación…'

psql_super() {
    sudo -u postgres psql -p "$DB_PORT" -v ON_ERROR_STOP=1 -tAc "$1"
}

HAS_ROLE="$(psql_super "SELECT 1 FROM pg_roles WHERE rolname='tpv_app'" || true)"
if [[ "$HAS_ROLE" == "1" ]]; then
    psql_super "ALTER ROLE tpv_app WITH LOGIN PASSWORD '${DB_APP_PASS}'" >/dev/null
else
    psql_super "CREATE ROLE tpv_app LOGIN PASSWORD '${DB_APP_PASS}'" >/dev/null
fi
HAS_DB="$(psql_super "SELECT 1 FROM pg_database WHERE datname='tpv'" || true)"
if [[ "$HAS_DB" != "1" ]]; then
    psql_super "CREATE DATABASE tpv OWNER tpv_app" >/dev/null
fi
ok "Usuario tpv_app y base de datos tpv listos"

step 'Creando el entorno Python del servidor e instalando dependencias…'
VENV_DIR="$INSTALL_ROOT/venv"
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
"$VENV_PY" -m pip install --disable-pip-version-check -q --upgrade pip
"$VENV_PY" -m pip install --disable-pip-version-check -q "$BACKEND_DIR"
ok "Dependencias instaladas en $VENV_DIR"

step 'Aplicando el esquema de la base de datos (migraciones)…'
( cd "$BACKEND_DIR" && "$VENV_PY" -m alembic upgrade head )
ok "Esquema de base de datos aplicado"

ROLES_COUNT="$(PGPASSWORD="$DB_APP_PASS" psql -h localhost -p "$DB_PORT" -U tpv_app -d tpv -tAc 'SELECT count(*) FROM roles' 2>/dev/null || echo 0)"
if [[ "$(echo "$ROLES_COUNT" | xargs)" == "0" ]]; then
    step 'Cargando datos iniciales (roles, IVA, formas de pago)…'
    PGPASSWORD="$DB_APP_PASS" psql -h localhost -p "$DB_PORT" -U tpv_app -d tpv -v ON_ERROR_STOP=1 -f "$REPO_ROOT/docs/database/seed.sql" >/dev/null
    ok "Datos iniciales cargados"
else
    ok "Datos iniciales ya presentes (no se tocan)"
fi

# ---------------------------------------------------------------- 5. Usuario admin
echo ""
step 'Usuario administrador del TPV'
echo "$REPO_ROOT" > /dev/null # (placeholder para mantener el paso legible)
echo "repo=$REPO_ROOT" > "$PATHS_FILE"
if ask_sn '¿Dar de alta el primer usuario administrador ahora?'; then
    TPV_INSTALL_ROOT="$INSTALL_ROOT" bash "$SCRIPT_DIR/create-admin.sh" || warn "No se creó el usuario. Podrás repetirlo con: sudo bash $SCRIPT_DIR/create-admin.sh"
else
    warn "Recuerda crear el usuario antes de usar el TPV: sudo bash $SCRIPT_DIR/create-admin.sh"
fi

# ---------------------------------------------------------------- 6. Frontends
step 'Preparando las aplicaciones (mostrador, móvil, administración)…'
FRONTEND_OK=0

build_frontend() {
    local src_dir="$1" base="$2" dest_dir="$3"
    step "Compilando «$(basename "$src_dir")»…"
    ( cd "$src_dir" && \
      if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi && \
      npx vite build "--base=$base" )
    rm -rf "${dest_dir:?}"/*
    cp -r "$src_dir/dist/." "$dest_dir/"
}

if ! command -v npm >/dev/null 2>&1; then
    warn "Node.js no está instalado: NO se compilaron las aplicaciones. El servidor funcionará (API OK) pero sin pantallas; instala Node 20+ y vuelve a ejecutar este instalador."
else
    if build_frontend "$REPO_ROOT/frontend" "/app/tpv/" "$INSTALL_ROOT/frontend/tpv" \
        && build_frontend "$REPO_ROOT/frontend/mobile" "/app/movil/" "$INSTALL_ROOT/frontend/movil" \
        && build_frontend "$REPO_ROOT/frontend/admin" "/app/admin/" "$INSTALL_ROOT/frontend/admin"; then
        FRONTEND_OK=1
        ok "Aplicaciones compiladas y servidas por el servidor"
    else
        warn "No pude compilar alguna aplicación. La API funcionará; reintenta ejecutando este instalador otra vez."
    fi
fi

# ---------------------------------------------------------------- 7. Servicio systemd
step 'Registrando el usuario y el servicio del sistema…'

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    CREATED_SYSTEM_USER=1
fi
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_ROOT"

cat > /etc/systemd/system/tpv.service <<EOF
[Unit]
Description=TPV Moderno — API + frontends servidos (§1.4)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$BACKEND_DIR
EnvironmentFile=$ENV_PATH
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host \${TPV_BIND} --port \${TPV_PORT} --workers 1
Restart=always
RestartSec=3
StandardOutput=append:$INSTALL_ROOT/logs/tpv.log
StandardError=append:$INSTALL_ROOT/logs/tpv.err.log
# El hub WebSocket asume un único proceso (§8): nunca subas --workers.
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$INSTALL_ROOT
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/tpv-backup.service <<EOF
[Unit]
Description=TPV Moderno — copia de seguridad diaria de PostgreSQL
After=postgresql.service

[Service]
Type=oneshot
User=$SERVICE_USER
Group=$SERVICE_USER
ExecStart=/usr/bin/env bash $SCRIPT_DIR/backup-database.sh --install-root $INSTALL_ROOT --kind auto
EOF

cat > /etc/systemd/system/tpv-backup.timer <<'EOF'
[Unit]
Description=TPV Moderno — dispara la copia de seguridad diaria a las 03:07

[Timer]
OnCalendar=*-*-* 03:07:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# logrotate: un fichero por día implícito vía journald quedaría fuera de
# INSTALL_ROOT; en su lugar se rota el log de systemd con logrotate si existe.
if [[ -d /etc/logrotate.d ]]; then
    cat > /etc/logrotate.d/tpv <<EOF
$INSTALL_ROOT/logs/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    copytruncate
}
EOF
fi

systemctl daemon-reload
systemctl enable --now tpv-backup.timer >/dev/null
ok "Servicio 'tpv' y temporizador de copias diarias (tpv-backup.timer, 03:07) registrados"

# Manifest para uninstall.sh: qué tocó ESTE instalador fuera de INSTALL_ROOT,
# para poder revertirlo sin tocar nada que ya existiera por otro motivo
# (ni un PostgreSQL compartido con otro uso de la máquina, por ejemplo).
# No se sobrescribe si ya existía de una instalación anterior en modo
# "conservar configuración": lo que se instaló la primera vez sigue siendo
# lo que hay que revertir, aunque esta ejecución no haya instalado nada nuevo.
MANIFEST="$CONF_DIR/install-manifest.txt"
if [[ -f "$MANIFEST" ]]; then
    PREV_PG="$(grep -m1 '^installed_postgresql_pkgs=' "$MANIFEST" 2>/dev/null | cut -d= -f2 || echo 0)"
    PREV_PY="$(grep -m1 '^installed_python_pkgs=' "$MANIFEST" 2>/dev/null | cut -d= -f2 || echo 0)"
    PREV_USER="$(grep -m1 '^created_system_user=' "$MANIFEST" 2>/dev/null | cut -d= -f2 || echo 0)"
    [[ "$PREV_PG" == "1" ]] && INSTALLED_PG_PKGS=1
    [[ "$PREV_PY" == "1" ]] && INSTALLED_PYTHON_PKGS=1
    [[ "$PREV_USER" == "1" ]] && CREATED_SYSTEM_USER=1
fi
{
    echo "# TPV Moderno — qué instaló install.sh fuera de $INSTALL_ROOT (para uninstall.sh)."
    echo "install_root=$INSTALL_ROOT"
    echo "repo_root=$REPO_ROOT"
    echo "db_port=$DB_PORT"
    echo "system_user=$SERVICE_USER"
    echo "installed_postgresql_pkgs=$INSTALLED_PG_PKGS"
    echo "installed_python_pkgs=$INSTALLED_PYTHON_PKGS"
    echo "created_system_user=$CREATED_SYSTEM_USER"
} > "$MANIFEST"

# ---------------------------------------------------------------- 8. Firewall
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
    ufw allow "${TPV_PORT}/tcp" comment 'TPV Servidor (HTTP)' >/dev/null
    ok "Firewall (ufw) abierto: otros equipos pueden entrar por el puerto $TPV_PORT"
else
    warn "ufw no está activo: no hay nada que abrir (o abre tú el puerto $TPV_PORT en tu firewall si usas otro)."
fi

# ---------------------------------------------------------------- 9. Arrancar
step 'Arrancando el servidor…'
systemctl enable tpv.service >/dev/null
systemctl restart tpv.service

READY=0
for _ in $(seq 1 45); do
    sleep 1
    if curl -fsS "http://127.0.0.1:${TPV_PORT}/api/v1/healthz" >/dev/null 2>&1; then READY=1; break; fi
    systemctl is-active --quiet tpv.service || break
done
if [[ "$READY" -ne 1 ]]; then
    warn "El servidor no respondió a tiempo. Diagnóstico: systemctl status tpv.service && journalctl -u tpv -n 50"
else
    ok "Servidor en marcha (systemctl status tpv.service)"
fi

# ---------------------------------------------------------------- 10. Resumen
IP="$(lan_ipv4)"
SUMMARY="$CONF_DIR/install-summary.txt"
{
    echo "INSTALACIÓN DEL TPV MODERNO (nativo Linux)"
    echo "Fecha: $(date '+%Y-%m-%d %H:%M')"
    echo ""
    echo "Mostrador : http://${IP:-<IP-del-servidor>}:${TPV_PORT}/app/tpv/"
    echo "Móvil     : http://${IP:-<IP-del-servidor>}:${TPV_PORT}/app/movil/   (en el móvil: abrir y «Añadir a pantalla de inicio»)"
    echo "Admin     : http://${IP:-<IP-del-servidor>}:${TPV_PORT}/app/admin/"
    echo ""
    echo "Instalación           : $INSTALL_ROOT"
    echo "Código del programa   : $REPO_ROOT"
    echo "Config y CONTRASEÑAS  : $ENV_PATH   (NO compartir este fichero)"
    echo "Logs                  : $INSTALL_ROOT/logs (rotados 30 días si hay logrotate)"
    echo "Copias de seguridad   : $INSTALL_ROOT/backups/{daily,weekly,monthly}"
    echo "Última copia          : $CONF_DIR/last-backup.txt"
    echo ""
    echo "Automático: el servidor arranca solo al iniciar la máquina (systemctl enable tpv)"
    echo "            y se reinicia solo si el proceso muere (Restart=always)."
    echo "Copias: una al día a las 03:07 (tpv-backup.timer) — diarias x7, semanales x4, mensuales x12."
    echo ""
    echo "Comandos útiles:"
    echo "  Estado       : systemctl status tpv"
    echo "  Reiniciar    : sudo systemctl restart tpv"
    echo "  Parar        : sudo systemctl stop tpv"
    echo "  Logs en vivo : journalctl -u tpv -f"
    echo "  Otro usuario : sudo bash $SCRIPT_DIR/create-admin.sh"
    echo "  Backup manual: sudo bash $SCRIPT_DIR/backup-database.sh --kind daily"
    echo "  Restaurar    : pg_restore -h localhost -p $DB_PORT -U tpv_app -d tpv --clean --if-exists <fichero.dump>"
    echo "  Desinstalar  : sudo bash $SCRIPT_DIR/uninstall.sh --purge   (quita TODO lo que puso este instalador)"
} > "$SUMMARY"

if [[ "$FRONTEND_OK" -ne 1 ]]; then
    echo "" >> "$SUMMARY"
    echo "PENDIENTE: instalar Node.js 20+ y volver a ejecutar install.sh para tener las pantallas." >> "$SUMMARY"
fi

echo ""
echo -e "${C_GREEN}==============================================================${C_RESET}"
echo -e "${C_GREEN}   INSTALACIÓN TERMINADA${C_RESET}"
echo -e "${C_GREEN}==============================================================${C_RESET}"
if [[ -n "$IP" ]]; then
    echo "  Mostrador : http://$IP:${TPV_PORT}/app/tpv/"
    echo "  Móvil     : http://$IP:${TPV_PORT}/app/movil/"
    echo "  Admin     : http://$IP:${TPV_PORT}/app/admin/"
fi
echo "  Resumen guardado en: $SUMMARY"
echo "  Contraseñas y configuración (NO compartir): $ENV_PATH"
echo ""

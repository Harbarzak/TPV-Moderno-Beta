# TPV Moderno — Despliegue en producción con Docker

Kit multiplataforma de la fase **Despliegue**. Complementa (no sustituye) al
instalador Windows nativo (`../README-INSTALACION.md`): mismo producto, misma
arquitectura (§1 de ARCHITECTURE.md), otro empaquetado —pensado para un
servidor Linux permanente o cualquier anfitrión con Docker.

```
Terminales (navegador)          Anfitrión Docker
┌────────────┐  http(s) :80/:443 ┌─────────────────────────────────────┐
│ TPV (most.)│ ────────────────► │ proxy  Caddy (gzip, TLS MODO B)     │
│ Móvil      │                   │   └─► api  FastAPI + frontends      │
│ Admin      │                   │          ├─ /app/tpv /app/movil /app│
└────────────┘                   │          │  /admin  (§1.4)          │
                                 │          └─► db  PostgreSQL 16      │
                                 │              (solo red interna)     │
                                 │ volúmenes: pgdata · tpv_data ·      │
                                 │ backups · caddy_data                │
                                 └─────────────────────────────────────┘
```

**Contenido del kit** (`deploy/docker/`):

| Fichero | Papel |
|---|---|
| `Dockerfile` | Imagen única: API + los 3 frontends + cliente PostgreSQL (`pg_dump`/`pg_restore`, para el endpoint de backups). Migraciones y seed condicional en cada arranque; 1 worker (el hub WS exige proceso único). |
| `compose.yml` | 3 servicios (`db`, `api`, `proxy`) con `restart: unless-stopped`, healthchecks y logs rotados (10 MB × 5). |
| `Caddyfile` | Reverse proxy. MODO A: HTTP plano (`:80`). MODO B: HTTPS con la CA interna de Caddy. |
| `.env.example` | Plantilla de variables. Sin secretos reales (ADR-008): se generan nuevos al copiar a `.env`. |
| `backup.sh` / `restore.sh` | Copia y restauración con retención y confirmación. |
| `seed_once.py` | Seed idempotente SOLO si la BD está vacía (sin usuarios). |

**Salud y estado**: `GET /api/v1/healthz` (proceso vivo, no toca la BD) y
`GET /api/v1/readyz` (BD y migraciones al día). Esos dos endpoints son el
«todo va bien» de esta documentación.

**Requisitos**: Docker 24+ con Compose v2 (`docker compose version`), 2 GB de
RAM libres, y una IP/nombre fijo en la LAN para el servidor.

---

## 0. Preparación de secretos (una vez)

Los secretos **nunca** se copian de otro sitio: se generan nuevos y rotados
(ADR-008). En `deploy/docker/`:

```sh
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(24))"   # → POSTGRES_PASSWORD
python -c "import secrets; print(secrets.token_urlsafe(64))"   # → TPV_JWT_SECRET
```

Edita `.env` y pega los dos valores. Si activarás el MODO B, descomenta y
ajusta `TPV_DOMAIN`. `.env` está fuera del repositorio (ignorado por git y
por Docker); **no** lo copies entre servidores: genera uno nuevo y cambia la
contraseña de BD en el viejo si dejas de usarlo.

---

## 1. Instalación (primera vez)

```sh
cd deploy/docker
docker compose up -d --build          # primera vez: 5–15 min (compila frontends)
docker compose ps                     # los 3 servicios «running/healthy»
curl -fsS http://localhost/api/v1/healthz     # {"status":"ok"}
curl -fsS http://localhost/api/v1/readyz      # BD accesible y migrada
```

El contenedor `api` aplica automáticamente las migraciones y el seed (roles,
permisos, IVA, formas de pago, parámetros) **solo si la BD está vacía**.

Alta del primer administrador (la contraseña va SOLO por stdin; mínimo 8
caracteres):

```sh
printf 'CONTRASEÑA-DEL-ADMIN\n' | docker compose exec -T api python create_admin.py admin "Nombre y Apellidos"
```

Verificación final desde otro equipo de la LAN (sustituye `SERVIDOR`):

- Administración: `http://SERVIDOR/app/admin/` — entra con el admin creado.
- Mostrador: `http://SERVIDOR/app/tpv/` — login del usuario con PIN.
- Móvil: `http://SERVIDOR/app/movil/`.

Con esto el servidor está en producción. **Configura ya el backup
automático** (punto 3) antes de dar de alta datos reales.

## 2. Actualización (nueva versión del código)

```sh
cd deploy/docker
./backup.sh                                        # 1. copia de seguridad SIEMPRE antes
git -C ../.. pull                                  # 2. trae el código nuevo
docker compose up -d --build                       # 3. recompila y recrea SOLO lo cambiado
curl -fsS http://localhost/api/v1/readyz           # 4. verificación
```

Las migraciones de esquema se aplican solas al arrancar `api` (alembic es
incremental). Rollback: restaura la copia del paso 1 (punto 4) y vuelve al
commit anterior (`git -C ../.. checkout <tag> && docker compose up -d --build`).

## 3. Backup

Manual: `./backup.sh` → `backups/tpv_YYYYMMDD_HHMMSS.dump` (formato
`pg_dump -Fc`, comprimido). Retención configurable en `.env`
(`BACKUP_KEEP_DAYS=30`, `BACKUP_KEEP_MIN=10`): se borra lo más viejo que N
días conservando siempre las N más recientes.

Automático — crontab del anfitrión (`crontab -e`):

```cron
15 3 * * * cd /ruta/deploy/docker && ./backup.sh >> backups/backup.log 2>&1
```

En Windows/Docker Desktop usa el Programador de tareas apuntando a
`sh.exe backup.sh` (el kit Windows nativo ya trae `backup-database.ps1`).

**Regla de oro**: copia los `.dump` fuera del servidor (NAS, USB, nube) con
la frecuencia que defina tu pérdida de datos tolerable. Una copia en el
mismo disco no protege de nada. La administración web también puede crear
copias (`/app/admin` → Copias de seguridad); viven en el volumen `backups`
del anfitrión.

## 4. Restore

```sh
./restore.sh backups/tpv_20260913_031500.dump
```

El script pide escribir `RESTAURAR`, **para la API** (nadie escribe mientras
se restaura), cierra conexiones vivas, restaura con
`pg_restore --clean --if-exists` y arranca la API de nuevo — que aplicará
las migraciones pendientes si la copia era antigua. Termina siempre
verificando `readyz`. Es una operación destructiva sobre los datos actuales:
si dudas, haz un `./backup.sh` ANTES de restaurar.

## 5. Cambio de servidor

1. En el viejo: `./backup.sh` y apunta el `.dump` resultante.
2. En el nuevo: instala Docker, copia el repositorio y `deploy/docker/`,
   genera un `.env` **nuevo** (punto 0) y `docker compose up -d --build`.
3. Copia el `.dump` al nuevo y `./restore.sh ruta/al/dump`.
4. Datos fuera de la BD: si usabas el MODO B y quieres conservar los
   certificados de la CA interna (para no volver a confiarla en cada
   terminal), migra también el volumen `caddy_data`:
   `docker run --rm -v tpv_caddy_data:/from -v "$(pwd)"/caddy_data:/to alpine tar -C /from -cf - . | tar -C /to -xf -`
5. Apunta el DNS/directivas a la IP del nuevo y **verifica**:
   `http://NUEVO/api/v1/readyz` + un login en `/app/tpv/`.
6. Apaga el viejo (`docker compose down`) y regenera sus secretos si no se
   va a reutilizar.

## 6. Incorporación de un TPV (mostrador)

1. Equipo con navegador moderno (Chrome/Edge/Firefox actualizados) en la
   misma LAN que el servidor. Nada que instalar.
2. Fija la IP del servidor (reserva DHCP o DNS interno).
3. Crea/activa en `/app/admin` → Usuarios un camarero con PIN y permisos de
   venta (`sales.sell`).
4. Abre `http://SERVIDOR/app/tpv/`, inicia sesión, y crea un acceso directo
   en el escritorio/kiosco del navegador (modo pantalla completa recomendado).
5. Impresora: `connection: network` con su IP (colas gestionadas por la API)
   o `agent` cuando haya tpv-agent instalado en ese puesto.
6. **Si el servidor está en MODO B**: primero instala el certificado raíz de
   la CA de Caddy (ver cabecera del Caddyfile) y usa
   `https://TPV_DOMAIN/app/tpv/`; si no, el login fallará por certificado.

## 7. Incorporación de un móvil

1. Móvil en la misma WiFi de la LAN (no datos móviles).
2. Abre `http://SERVIDOR/app/movil/`, entra con el usuario/contraseña que le
   haya creado el administrador.
3. «Añadir a pantalla de inicio» desde el menú del navegador para que abra
   como app a pantalla completa.
4. **Recomendado MODO B**: las funciones de navegador que exigen contexto
   seguro (service worker/PWA instalable, cámara) requieren `https://`; en
   HTTP plano el navegador puede limitarlas.
5. Baja de un móvil = desactivar su usuario en `/app/admin` (efecto
   inmediato en el siguiente `401`).

---

## HTTPS en detalle

- **MODO A** (por defecto): `:80` HTTP plano. Válido en LAN de confianza.
- **MODO B**: `tls internal` — Caddy genera CA raíz y certificados del
  dominio, renovándolos él solo (por eso `caddy_data` es volumen). Los
  WebSocket pasan a `wss://` sin cambiar nada en la app. Cada terminal debe
  confiar la CA raíz una vez (comando de exportación en la cabecera del
  Caddyfile).
- **Dominio público**: `tpv.tudominio.com { tls correo@dominio.com; … }` →
  certificado de Let's Encrypt automáticamente, sin confiar CA alguna.
- El proxy solo expone 80/443; la BD no publica puertos y la API no es
  alcanzable más que a través del proxy (misma política same-origin que en
  desarrollo: CORS denegado por defecto).

## Logs y diagnóstico

```sh
docker compose ps                 # ¿todo healthy? (healthz para api, pg_isready para db)
docker compose logs -f api        # seguir la API (structlog, nivel de TPV_LOG_LEVEL)
docker compose logs db proxy      # resto de servicios
docker compose restart api        # primer remedio benigno
docker compose exec api python seed_once.py   # diagnóstico: ¿el seed está aplicado?
```

Rotación ya configurada (`json-file`, 10 MB × 5 ficheros por servicio): los
logs no crecen sin límite. Para conservarlos fuera, añade al backup del
punto 3: `docker compose logs api > backups/api_$(date +%F).log`.

## Recuperación ante fallo

| Síntoma | Diagnóstico | Acción |
|---|---|---|
| Un servicio «restarting» o `unhealthy` | `docker compose logs <svc>` | `restart: unless-stopped` ya lo reintenta; si el bucle persiste, corrige la causa (ver filas siguientes) |
| `readyz` responde error / `db` unhealthy | BD caída o volumen dañado | `docker compose restart db`; si no vuelve: restaurar (punto 4) |
| Datos borrados/corruptos por error humano | — | `./backup.sh` (para no perder lo actual) y luego `./restore.sh` con la copia buena más cercana al incidente |
| El anfitrión entero se pierde | — | Montar nuevo servidor (punto 5): es idéntico a un cambio de servidor; de ahí la regla de copias FUERA del anfitrión |
| Disco lleno (`db` escribe errores de E/S) | `docker system df` | `docker image prune -f`; revisar retención de backups; ampliar disco |
| Terminales no conectan pero `readyz` OK en local | red/proxy | `docker compose ps proxy`, `docker compose logs proxy`; comprobar IP/DNS y que 80/443 no estén ocupados (`TPV_HTTP_PORT` en `.env`) |
| Picos de latencia / API lenta | — | Escalar verticalmente el anfitrión; la API usa un solo worker por el hub WS — no multiplicar réplicas de `api` |

Tras CUALQUIER recuperación: verificar `healthz` + `readyz` + un login real
en mostrador antes de dar el servidor por operativo.

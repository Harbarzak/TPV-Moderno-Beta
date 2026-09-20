# TPV Moderno — instalador nativo para Linux (sin Docker)

Este instalador monta el servidor TPV **directamente sobre el sistema
operativo** de un servidor Linux (Debian/Ubuntu), sin contenedores: instala
PostgreSQL, crea un entorno Python, aplica las migraciones, compila las tres
pantallas (mostrador, móvil, administración) y registra el servidor como
**servicio systemd** con arranque automático, reinicio ante fallo y copias de
seguridad diarias.

Es una alternativa al kit Docker que ya existe en `deploy/docker/` (ese usa
contenedores con PostgreSQL + API + Caddy). Usa este instalador cuando el
servidor no debe o no puede usar Docker; usa el kit Docker cuando prefieras
contenedores. Ambos despliegan el mismo backend y las mismas pantallas — la
diferencia es solo *cómo* se instalan en la máquina.

## Requisitos

- Un servidor Linux con **systemd** (Debian 12+, Ubuntu 22.04+ o similar).
- Acceso `root` (o `sudo`).
- Conexión a Internet la primera vez (para instalar paquetes con `apt` y
  dependencias de Python/Node). Si el servidor no tiene Internet, instala tú
  mismo PostgreSQL 16+, Python 3.12+ y Node.js 20+ antes de lanzarlo.
- El código del proyecto (este repositorio) copiado al servidor, por ejemplo
  en `/opt/tpv-src` (clonado desde el repositorio privado de GitHub — ver la
  guía de publicación en la raíz del proyecto).

En distribuciones que no usan `apt` (Fedora, Arch, etc.) instala PostgreSQL,
Python 3.12+ y Node.js 20+ con el gestor de paquetes de esa distro antes de
ejecutar el script: el resto del proceso (base de datos, entorno Python,
migraciones, frontends, servicio systemd, copias) es idéntico.

## Instalación

```bash
cd /opt/tpv-src              # la carpeta con backend/, frontend/, deploy/...
sudo bash deploy/linux/install.sh
```

Por defecto instala en `/opt/tpv`. Para cambiar la ruta o el puerto de
PostgreSQL:

```bash
sudo bash deploy/linux/install.sh --install-root /opt/tpv --db-port 5432
```

El instalador es interactivo y en español. Al terminar deja un resumen en
`/opt/tpv/conf/install-summary.txt` con las URLs de cada pantalla y los
comandos de administración.

### Qué hace, paso a paso

1. Comprueba requisitos (root, systemd, Python 3.12+; instala Python por
   `apt` si falta).
2. Instala PostgreSQL por `apt` si no hay uno ya en el sistema, y lo arranca
   como servicio del sistema operativo.
3. Genera `conf/.env` con contraseñas y el secreto JWT **nuevos y aleatorios**
   (nunca valores de ejemplo ni heredados — igual que exige el proyecto para
   Windows, ADR-008). El fichero se crea con permisos `600` (solo root puede
   leerlo).
4. Crea el rol `tpv_app` y la base de datos `tpv`, aplica las migraciones
   Alembic y carga los datos iniciales (roles, IVA, formas de pago) si la
   base está vacía.
5. Pregunta si quieres dar de alta el primer usuario administrador ahí mismo
   (usuario, nombre y contraseña; la contraseña no se muestra en pantalla ni
   se guarda en ningún fichero).
6. Compila con Node.js los tres frontends (`frontend/`, `frontend/mobile/`,
   `frontend/admin/`) y los deja en `frontend/{tpv,movil,admin}/` dentro de
   la instalación, servidos por la propia API en `/app/tpv`, `/app/movil` y
   `/app/admin`.
7. Crea un usuario de sistema sin privilegios (`tpv`) dueño de la
   instalación, y registra:
   - `tpv.service` — el servidor (`uvicorn`, un único proceso: el hub de
     WebSocket lo exige), con `Restart=always` y arranque automático.
   - `tpv-backup.timer` / `tpv-backup.service` — copia de seguridad diaria a
     las 03:07 con retención 7 diarias / 4 semanales / 12 mensuales.
8. Abre el puerto del servidor en `ufw` si está activo.
9. Arranca el servicio y espera a que `/api/v1/healthz` responda.

Si se vuelve a ejecutar sobre una instalación existente, pregunta si quieres
conservar la configuración anterior (contraseñas incluidas) y solo actualiza
código y frontends — igual que el instalador de Windows.

## Administración del día a día

```bash
sudo systemctl status tpv          # ¿está en marcha?
sudo systemctl restart tpv         # reiniciar
sudo systemctl stop tpv            # parar
journalctl -u tpv -f               # logs en vivo (también en /opt/tpv/logs/)

sudo bash deploy/linux/create-admin.sh          # dar de alta / resetear un usuario
sudo bash deploy/linux/backup-database.sh --kind daily   # copia manual
```

Restaurar una copia:

```bash
pg_restore -h localhost -p 5432 -U tpv_app -d tpv --clean --if-exists <fichero.dump>
```

## Desinstalar

```bash
sudo bash deploy/linux/uninstall.sh                  # quita el servicio; NO borra datos
sudo bash deploy/linux/uninstall.sh --remove-data     # además pregunta si borrar BD y ficheros
sudo bash deploy/linux/uninstall.sh --purge           # desinstalación COMPLETA (ver abajo)
```

`install.sh` anota en `conf/install-manifest.txt` qué instaló él mismo en el
sistema (fuera de la carpeta de instalación): si tuvo que instalar
PostgreSQL, los paquetes de Python, y si creó el usuario de sistema `tpv`.
`uninstall.sh` lee ese manifest para saber qué es seguro revertir:

- **Sin opciones**: solo quita el servicio, el temporizador de copias y la
  regla de firewall. No toca datos ni nada instalado en el sistema.
- **`--remove-data`**: además pregunta (con confirmación) si borrar la base
  de datos `tpv`/rol `tpv_app` y toda la carpeta de instalación (venv,
  frontends compilados, logs, copias de seguridad).
- **`--purge`**: todo lo anterior, y además pregunta uno por uno si:
  - borrar el usuario de sistema `tpv` (creado por el instalador, sin login),
  - desinstalar PostgreSQL por completo (`apt purge`) — **solo si fue este
    instalador quien lo puso**; si ya estaba en la máquina antes (por
    ejemplo compartido con otro servicio), lo detecta y no lo toca,
  - borrar también los datos de PostgreSQL en disco,
  - borrar el código fuente clonado del repositorio.

Con `--purge` no debería quedar ni un fichero, servicio, usuario o paquete
huérfano relacionado con el TPV en el servidor — cada paso pide confirmación
por separado, así que puedes decir que no a los que quieras conservar (por
ejemplo, si ese PostgreSQL ya lo usa otra aplicación tuya, contesta "n" y se
queda intacto).

Si borraste o perdiste `conf/install-manifest.txt`, `--purge` se comporta
como `--remove-data` y avisa de que no puede verificar qué instaló el
sistema, así que pregunta igualmente por PostgreSQL antes de tocarlo.

## Notas de seguridad

- `conf/.env` contiene contraseñas y el secreto JWT: no lo copies, no lo
  subas a ningún repositorio y no lo envíes por ningún canal. El propio
  `.gitignore` del proyecto ya lo excluye.
- El servidor corre como usuario de sistema `tpv` (sin shell, sin privilegios)
  y el `ExecStart` de `tpv.service` usa `ProtectSystem=strict` +
  `ReadWritePaths` limitado a la instalación.
- PostgreSQL no queda expuesto fuera del propio servidor (por defecto solo
  escucha en `localhost`, como en el resto de la arquitectura del proyecto).

## Pendiente conocido

- Probado sobre Debian/Ubuntu; en otras familias de distro, el paso 1-2
  (instalación de paquetes) requiere adaptar los `apt-get` a `dnf`/`pacman`/
  etc. — el resto del instalador no cambia.
- Igual que el instalador de Windows, no compila ni sirve el KDS (`frontend/
  kds/`): el backend actual no tiene una ruta `/app/kds` de servido estático
  (solo `tpv`, `movil` y `admin`). El KDS se sigue ejecutando como SPA aparte
  en desarrollo.

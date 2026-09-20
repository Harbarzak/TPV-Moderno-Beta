# Instalación del servidor TPV en Windows — guía paso a paso

Guía para instalar el servidor del TPV en un ordenador Windows **sin saber
programar**. Tiempo estimado: **menos de 1 hora**. Si en algún paso te atascas,
la sección [Problemas frecuentes](#9-problemas-frecuentes) del final resuelve
los casos habituales.

**Qué se instala en el ordenador servidor:**

| Pieza | Para qué sirve | Cómo se instala |
|---|---|---|
| PostgreSQL | La base de datos (todo lo que se vende, cobra y archiva) | El instalador lo baja y lo deja funcionando como servicio |
| Servidor TPV (API) | El cerebro: los mostradores y móviles hablan con él | Como tarea automática: arranca solo al encender el equipo |
| Pantallas (mostrador y móvil) | Los programas que usan camareros y caja | Compiladas y **servidas por el propio servidor** |
| Copias de seguridad | Una al día, con retención 7 diarias · 4 semanales · 12 mensuales | Tarea programada a las 03:07 |
| Logs | Registro diario de lo que pasa, se guardan 30 días | Automático |

Los terminales (caja, móviles) **no instalan nada**: solo abren una dirección
web en el navegador.

---

## 1. Qué necesitas

- Un PC Windows 10 u 11 (64 bits) que hará de **servidor**, encendido durante
  el horario del local, conectado a la **misma red** (WiFi o cable) que los
  terminales y móviles.
- Permisos de **administrador** en ese PC (para el servicio de arranque y el
  firewall).
- **Python 3.12 o más nuevo**, instalado con la casilla
  *"Add python.exe to PATH"* marcada → <https://www.python.org/downloads/>
- **Node.js 20 o más nuevo** (solo para generar las pantallas) → <https://nodejs.org>
  (versión LTS). Si este equipo no tiene Internet, mira el punto 9.4.
- La **carpeta del programa** (se llama `estructura_data`), copiada al servidor,
  por ejemplo en `C:\tpv-programa`.

> Consejo: dale al servidor una **IP fija** en el router (o una reserva DHCP).
> Así la dirección que marcan los terminales nunca cambia.

## 2. Instalar

1. Abre la carpeta `estructura_data\deploy`.
2. Haz **clic derecho** sobre `instalar.bat` → **Ejecutar como administrador**.
3. Responde a las preguntas (la respuesta por defecto es siempre S = sí).
4. Espera. La primera vez descarga PostgreSQL y las dependencias: puede tardar
   10–20 minutos según la conexión.

Al acabar verás un recuadro verde con las direcciones del TPV y quedará
guardado un resumen en `C:\TPV\conf\install-summary.txt` (doble clic en
`Resumen-instalacion.bat` para releerlo cuando quieras).

## 3. Qué te va a preguntar

| Pregunta | Qué responder |
|---|---|
| «¿Conservar la configuración anterior?» | **S** si ya estaba instalado y solo actualizas. La primera vez no aparece. |
| «Contraseña de "postgres"» | Solo si usas un PostgreSQL instalado antes por tu cuenta y sabes su contraseña. Si el instalador puso PostgreSQL él mismo, no la pide. |
| Usuario administrador | El nombre con el que entrará el responsable (p. ej. `admin`) y su contraseña (mínimo 8 caracteres; no se ve al escribirla). |

Las contraseñas internas (base de datos, firma de sesiones) **no te las
pregunta**: las genera el instalador, nuevas y al azar, y las guarda solo en
`C:\TPV\conf\.env`.

## 4. Entrar desde los terminales

El servidor anuncia las direcciones al terminar la instalación. Son:

- **Caja / mostrador**: `http://IP-DEL-SERVIDOR:8000/app/tpv/`
- **Móvil (camarero)**: `http://IP-DEL-SERVIDOR:8000/app/movil/`

`IP-DEL-SERVIDOR` es la dirección del PC servidor dentro de tu red
(algo como `192.168.1.50`). Para saberla: en el servidor, abre
`Resumen-instalacion.bat` — la dirección está escrita ahí.

**En la caja/terminal** (Windows o cualquier PC de la red):
1. Abre Chrome o Edge.
2. Escribe la dirección del mostrador.
3. (Opcional) Menú ⋮ → *Instalar aplicación* / *Crear acceso directo* para
   tenerla como un programa más.

**En el móvil** (Android/iOS, conectado al WiFi del local):
1. Abre Chrome/Safari y escribe la dirección del móvil.
2. Menú → **Añadir a pantalla de inicio**. Desde ese icono funciona como una
   app (a pantalla completa, sin barra del navegador).

Eso es **toda** la configuración de un terminal: la dirección web. Las
pantallas encuentran la API solas porque viven en el mismo servidor.

> Comprueba tras instalar que desde un móvil puedes abrir la dirección del
> mostrador. Si el PC del servidor se apaga o pierde la red, los terminales
> avisan de que no hay conexión — enciéndelo y siguen funcionando.

## 5. Contraseñas: dónde viven y cómo cambiarlas

- **El responsable del TPV** entra con el usuario que diste de alta en la
  instalación. Para crear otro usuario (o restablecer una contraseña
  olvidada): doble clic en `Crear-usuario.bat` en `C:\TPV` y responde.
- **Contraseñas internas**: están en `C:\TPV\conf\.env`. Ese fichero es el
  secreto del servidor: **no lo copies, no lo envíes, no lo enseñes**. Si
  sospechas que se ha visto, borra el fichero y vuelve a ejecutar
  `instalar.bat` respondiendo **n** a «¿conservar?» — genera todas nuevas.

## 6. Copias de seguridad

- **Cuándo**: todos los días a las **03:07** (el equipo debe estar encendido,
  aunque sea a la espera).
- **Dónde**: `C:\TPV\backups\daily` (7), `weekly` (4), `monthly` (12).
  Fichero `tpv-FECHA.dump`. La copia de más reciente queda apuntada en
  `C:\TPV\conf\last-backup.txt`.
- **Hacer una extra ahora** (antes de tocar algo importante): doble clic en
  `Copia-seguridad-ahora.bat`.
- **Restaurar** (lo hace un técnico): con el programa incluido en la carpeta
  `pgsql\bin` del servidor:
  ```
  pg_restore -h localhost -p 5432 -U tpv_app -d tpv --clean --if-exists fichero.dump
  ```
  (pedirá la contraseña de la aplicación, que está en `conf\.env`, clave
  `TPV_DATABASE_URL`).

> Las copias están **en el mismo PC**. Para protección real contra un disco
> roto, copia periódicamente la carpeta `C:\TPV\backups` a un USB o a la nube.

## 7. El día a día

| Situación | Qué pasa / qué hacer |
|---|---|
| Encienden el servidor | El TPV arranca solo (tarea `TPV-Servidor`). No hace falta iniciar sesión. |
| Algo va raro y quieres reiniciarlo | Doble clic en `Parar-TPV.bat` y luego en `Arrancar-TPV.bat`. |
| Quieres ver el servidor en vivo (técnico) | `powershell -File C:\TPV\bin\start-server.ps1 -Foreground` |
| **Actualizar el programa** | Copia la carpeta nueva del programa al servidor y vuelve a ejecutar `instalar.bat` como administrador, respondiendo **S** a «¿conservar?». Actualiza código y pantallas; los datos y contraseñas no se tocan. |
| Quitarlo todo | `powershell -File C:\TPV\bin\uninstall.ps1` (añade `-RemoveData` si también quieres borrar datos y copias — pregunta antes de hacerlo). |
| Ver qué ha pasado | Logs en `C:\TPV\logs` (un fichero por día, 30 días). |

## 8. Requisitos de tamaño

Cualquier PC de los últimos años vale para uno o varios locales pequeños:
4 GB de RAM y 20 GB libres de disco sobran para arrancar. Lo crítico no es la
potencia sino que el equipo **esté encendido y en red** durante el servicio.

## 9. Problemas frecuentes

**9.1. «No encuentro Python 3.12»** — Instala Python desde
<https://www.python.org/downloads/> marcando *"Add python.exe to PATH"*,
cierra y vuelve a lanzar `instalar.bat`.

**9.2. Desde el móvil no abre la dirección.**
- ¿El móvil está en el **mismo WiFi** que el servidor?
- ¿El servidor está encendido y has pasado el instalador (abre el firewall)?
- Prueba en el servidor mismo: abre `http://localhost:8000/app/tpv/`. Si ahí
  funciona pero desde el móvil no, es red/firewall: llama al técnico.
- Si la IP del servidor cambió (no es fija), fija la IP o reserva en el router
  y usa la nueva.

**9.3. «El servidor no responde tras 45 s» al arrancar.** Mira el final de
`C:\TPV\logs\tpv-FECHA.err.log`. Causa habitual: rutas o contraseñas mal en
`conf\.env` (por ejemplo se movió la carpeta del programa). Reejecuta
`instalar.bat`.

**9.4. El servidor NO tiene Internet.** El instalador necesita Internet la
primera vez para bajar PostgreSQL y las dependencias de Python. Alternativa
sin red: en un PC con Internet descarga (1) el zip
`postgresql-…-windows-x64-binaries.zip`, (2) el instalador de Python y (3)
pide al proveedor del programa el paquete de dependencias offline; luego lanza
`instalar.bat` con el zip a mano:
`powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1 -PgZip "C:\ruta\postgresql-...zip"`

**9.5. «Hacen falta permisos de administrador».** No vale doble clic: clic
derecho en `instalar.bat` → *Ejecutar como administrador*.

**9.6. Las pantallas no aparecen (el servidor responde pero no hay diseño).**
Node.js no estaba instalado cuando se instaló el TPV. Instala Node 20+ y
vuelve a ejecutar `instalar.bat` (conserva la configuración).

**9.7. El puerto 8000 lo usa otro programa.** Lanza el instalador con otro
puerto: `install.ps1 -DbPort 8001` cambia base de datos; para el web, edita
`TPV_PORT` en `conf\.env`, ejecuta `Parar-TPV.bat` y `Arrancar-TPV.bat`, y
abre el firewall para el nuevo puerto (o reejecuta `instalar.bat`).

## 10. Qué NO incluye esta instalación

- **HTTPS / certificados**: las terminales entran por `http://` dentro de la
  red del local (tráfico que no sale del router). Cifrado con certificado
  propio está previsto como despliegue avanzado.
- **Acceso desde fuera del local** (Internet/remoto): esta instalación es de
  red local, como corresponde a un TPV.
- **Copias fuera del servidor**: automatizar subida a USB/nube es un paso
  manual (ver sección 6).

---

*Servidor instalado con `estructura_data\deploy\install.ps1`. Este fichero y el
resumen de `C:\TPV\conf\install-summary.txt` son la referencia del instalador.*

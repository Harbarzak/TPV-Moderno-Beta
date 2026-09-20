TPV Moderno

Plataforma de Terminal Punto de Venta (TPV) cliente-servidor, pensada para operar en la red local sin depender de Internet. Sustituye a un TPV legado de escritorio (Windows/SQL Server) por un sistema moderno con un servidor central y varios clientes ligeros: mostrador táctil, PWA de camarero para móvil, panel de administración y un Kitchen Display System (KDS) para cocina.

Qué hace
Venta en mostrador: catálogo por categorías/paneles, ticket con descuentos por línea, búsqueda y lector de código de barras, cobro con pago único o mixto (efectivo/tarjeta/otros) y cambio calculado siempre por el servidor.
Caja: apertura con fondo, movimientos de entrada/salida, arqueo por denominaciones y cierre Z con desajuste calculado; informes X (consulta) y Z (histórico).
Tickets y facturas: numeración segura por terminal/año, facturación bajo demanda sobre ventas cobradas, rectificativas, logo de empresa en la cabecera impresa.
Modo restaurante: mapa de mesas, apertura/traspaso/unión/división de mesas, comanda como borrador del motor de ventas.
Móvil camarero (PWA): toma de comandas desde el móvil, sincronizada en tiempo real con el mostrador y la cocina.
KDS (cocina): tablero en tiempo real con estados nuevo/preparando/ listo/servido, por estación, con urgencias y tiempos.
Informes y estadísticas: por producto, categoría, camarero, forma de pago y periodo, con paginación y agregación en SQL (nunca miles de filas al navegador).
Administración: productos, catálogo, usuarios y permisos, formas de pago, terminales, impresoras, backups y auditoría — todo en un panel aparte de la operación diaria.
Impresión y hardware: cola de impresión con reintentos, impresoras de red y periféricos locales (cajón, lector de tarjetas) vía un agente de terminal; todo con adaptadores intercambiables, sin depender de un fabricante.
Fiscalidad: preparado para Veri*Factu/TicketBAI mediante un plugin desacoplado (FiscalAdapter); a día de hoy sin ningún régimen activo (TPV_FISCAL_PROVIDER=none — ver docs/fiscal/README.md).
Funciona offline: el mostrador sigue vendiendo sin conexión a Internet (solo depende de la red local) y reconcilia con idempotencia al recuperar el servidor.
Cómo funciona (arquitectura)

Un único servidor concentra toda la lógica y el estado; los clientes son interfaces React que nunca acceden a la base de datos directamente, solo vía API HTTP/WebSocket:

TPV mostrador ─┐
PWA móvil      ├──HTTPS/WSS──► API FastAPI ──► PostgreSQL (solo el servidor)
Panel admin    │                 │
KDS cocina    ─┘                 └─► Colas de impresión, WebSocket hub, workers
Backend: Python 3.12 + FastAPI + SQLAlchemy 2.x (async) + Alembic, organizado en capas api → services → domain, con repos/ y adapters/ como detalles inyectados.
Base de datos: PostgreSQL 16, con dinero siempre en numeric/Decimal (nunca float), snapshots históricos inmutables en cada venta y baja lógica en los catálogos.
Frontend: 4 aplicaciones React + TypeScript + Vite + Tailwind + Zustand independientes (frontend/ mostrador, frontend/mobile/ PWA, frontend/admin/ administración, frontend/kds/ cocina), servidas todas por el propio backend bajo /app/tpv, /app/movil, /app/admin.
Tiempo real: un hub WebSocket con temas por rol/terminal, replay de eventos perdidos al reconectar y latido cada 15 s; el REST sigue siendo la fuente de verdad si el WebSocket cae.
Seguridad: JWT de acceso corto + refresh rotativo, PIN de terminal, RBAC granular por permiso, Argon2id para contraseñas, auditoría inmutable de toda acción sensible.

El documento completo de arquitectura, decisiones (ADRs) y el estado del proyecto por fases están en ARCHITECTURE.md y PROJECT_STATE.md.

Cómo se instala / despliega

Dos formas de poner el servidor en marcha, mismo backend y mismas pantallas:

Docker (deploy/docker/): PostgreSQL + API + Caddy en contenedores. Ver deploy/docker/README-DESPLIEGUE.md.
Windows nativo (deploy/): install.ps1 instala PostgreSQL, Python, compila los frontends y registra un servicio de Windows. Ver deploy/README-INSTALACION.md.
Linux nativo, sin Docker (deploy/linux/): equivalente a la opción Windows pero con systemd (Debian/Ubuntu). Ver deploy/linux/README-INSTALACION-LINUX.md.

En cualquiera de las tres, abrir http://IP-DEL-SERVIDOR:8000/app/tpv/ (o /app/movil/, /app/admin/) es toda la configuración que necesita un terminal cliente: no hay nada más que instalar en los TPV, tablets o móviles.

Desinstalar

Cada instalador nativo trae su propio desinstalador, con tres niveles (el kit Docker se retira con docker compose down -v):

	Windows (deploy/uninstall.ps1)	Linux (deploy/linux/uninstall.sh)
Solo parar el servicio	(sin opciones)	(sin opciones)
+ borrar datos	-RemoveData	--remove-data
Todo, sin dejar nada huérfano	-Purge	--purge

El nivel "todo" pregunta paso a paso antes de tocar cada cosa (rol y base de datos de PostgreSQL, el propio PostgreSQL si lo instaló el instalador, y el código fuente clonado), así que nada se borra sin confirmación explícita.

Desarrollo local
bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # o source .venv/bin/activate en Linux/Mac
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload

# Frontend (mostrador)
cd frontend
npm install
npm run dev

Cada frontend (frontend/, frontend/mobile/, frontend/admin/, frontend/kds/) tiene su propio package.json y arranca por separado; el vite.config de cada uno hace de proxy hacia la API en desarrollo.

Estructura del repositorio
backend/        API FastAPI, modelos, migraciones y tests
frontend/        Mostrador (React) + mobile/, admin/, kds/ como subproyectos
deploy/          Instaladores: Docker, Windows nativo, Linux nativo
docs/            Arquitectura, decisiones (ADR), diseño, fiscalidad, QA…
ARCHITECTURE.md   Documento de arquitectura completo
PROJECT_STATE.md  Estado del proyecto por fases
Licencia y procedencia

Proyecto propio. Parte del diseño de interfaz se inspiró en el producto público URY (AGPL) solo como referencia de UX, sin incorporar su código — ver docs/agpl/ para la revisión de licencia hecha antes de adoptar cualquier idea de ese proyecto.

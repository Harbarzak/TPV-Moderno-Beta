# PROJECT_STATE — TPV Moderno

Estado del proyecto por fases. Actualizado al cierre de cada fase (solo la fase en curso).

## Progreso

| # | Fase | Estado |
|---|---|---|
| 0 | Arquitectura | ✅ Completada (2026-09-11) |
| 1 | Base de datos | ✅ Completada (2026-09-11) |
| 2 | Backend | ✅ Completada (2026-09-11) |
| 3 | Autenticación | ✅ Completada (2026-09-11) |
| 4 | Análisis URY | ✅ Completada (2026-09-13) |
| 5 | Design System | ✅ Completada (2026-09-14) |
| 6 | Productos | ✅ Completada (2026-09-11) |
| 7 | TPV visual | ✅ Completada (2026-09-11) |
| 8 | Motor de ventas | ✅ Completada (2026-09-11) |
| 9 | Pagos | ✅ Completada (2026-09-11) |
| 10 | Caja | ✅ Completada (2026-09-11) |
| 11 | Tickets/Facturas | ✅ Completada (2026-09-11) |
| 12 | Impresión | ✅ Completada (2026-09-12) |
| 13 | Hardware | ✅ Completada (2026-09-12) |
| 14 | WebSocket | ✅ Completada (2026-09-12) |
| 15 | Modo restaurante | ✅ Completada (2026-09-14) |
| 16 | Móvil camarero | ✅ Completada (2026-09-14) |
| 17 | KDS | ✅ Completada (2026-09-14) |
| 18 | Offline | ✅ Completada (2026-09-12) |
| 19 | Informes | ✅ Completada (2026-09-12) |
| 20 | Migración legado | ⬜ Pendiente |
| 21 | Administración | ✅ Completada (2026-09-13) |
| 22 | Instalador | ✅ Completada (2026-09-12) |
| 23 | Seguridad | ✅ Completada (2026-09-12) |
| 24 | Rendimiento | ✅ Completada (2026-09-12) |
| 25 | QA | ✅ Completada (2026-09-12) |
| 26 | Producción | ✅ Completada (2026-09-13) |

## Fase 0 — Arquitectura (completada 2026-09-11)

- Diseño completo de la plataforma en `ARCHITECTURE.md`: topología LAN cliente-servidor,
  componentes, modelo de datos alto nivel, flujos de venta y caja, seguridad, API `/api/v1`,
  WebSocket con replay, hardware (tpv-agent + impresión de red), backups, observabilidad,
  offline y plan de pruebas. 8 ADRs en `docs/decisions/`.
- Fiscalidad (Veri*Factu/TicketBAI) y CashDro fuera de alcance: solo interfaces de extensión
  `FiscalAdapter` y `CashDropAdapter`, sin implementación.
- Pendiente: todo lo implementable empieza en la fase **Base de datos** (esquema + Alembic).

## Fase 1 — Base de datos (completada 2026-09-11)

- Esquema completo en `docs/database/schema.sql` (40 tablas, 14 ENUM nativos, 11 triggers
  `updated_at`, índices parciales para concurrencia) con su `docs/database/ERD.md`.
- Modelos SQLAlchemy 2.x en `backend/app/db/models/` verificados en paridad con el DDL
  (tablas, enums, índices y constraints con nombre idénticos); migración inicial Alembic
  `0001_initial_schema`; datos mínimos en `docs/database/seed.sql` (sin usuarios, ADR-008).
- 18 tests de integridad (`backend/tests/`) contra PostgreSQL real vía `TPV_TEST_DATABASE_URL`
  (skip limpio si no está definida); en este equipo no hay servidor, quedan pendientes de ejecución.
- Pendiente: fase **Backend** (FastAPI + capa de acceso a datos).

## Fase 2 — Backend base (completada 2026-09-11)

- Esqueleto FastAPI en `backend/app/`: fábrica `create_app()`, configuración por entorno
  (`pydantic-settings`, prefijo `TPV_`), logging JSON con `request_id` (structlog, también para
  uvicorn), errores RFC 9457 con `code` estable, engine SQLAlchemy **async** perezoso hacia
  PostgreSQL + sesiones, CORS configurable y `GET /api/v1/healthz` (liveness) y `/readyz` (readiness,
  503 problem+json sin BD). Capas `api/services/domain/repos/adapters` ya reservadas.
- 10 tests de API en verde (sin necesidad de BD) + smoke test real con uvicorn; el test de
  `/readyz` con BD se activa con `TPV_TEST_DATABASE_URL`. Sin lógica TPV (por diseño de la fase).
- Sin cambios de arquitectura. Pendiente: fase **Autenticación**.

## Fase 3 — Autenticación y permisos (completada 2026-09-11)

- Auth completa en `/api/v1/auth`: Argon2id (contraseña y PIN), JWT de acceso 15 min ligado
  a sesión revocable, refresh opaco rotativo en cookie HttpOnly (reutilizar uno rotado cierra
  la sesión), logout idempotente, `GET /auth/me`, RBAC granular (permisos leídos de BD en cada
  petición), PIN con alcance `pos` que nunca administra, auditoría de accesos en `audit_log` y
  limitador de intentos por IP/ruta (429). `TPV_JWT_SECRET` solo por entorno (ADR-008).
- 11 tests de seguridad en verde sin BD + 13 tests E2E de autenticación (requieren
  `TPV_TEST_DATABASE_URL`, siguen en skip en este equipo). Suite: 21 passed, 30 skipped;
  smoke real con uvicorn verificado.
- Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 6 — Productos (completada 2026-09-11)

- Módulo de catálogo en `/api/v1/catalog` (27 endpoints): CRUD + reordenación de
  departamentos, categorías y productos; tarifas de precio; IVA con vigencia (la nueva
  versión cierra la anterior); histórico de precios inmutable; códigos de barras, imágenes
  y precios por tarifa dentro del payload del producto; `GET /catalog/pos` = snapshot
  vendible en 3 consultas, sin N+1.
- Migración `0002_catalog_pricing` (4 tablas: `price_tiers`, `product_tier_prices`,
  `product_barcodes`, `product_images`); dinero siempre string en JSON (float rechazado);
  DELETE = baja lógica; permisos `products.view`/`products.edit`; auditoría `catalog.*`.
- 24 tests nuevos (10 de esquemas sin BD + 14 E2E con `TPV_TEST_DATABASE_URL`): suite
  31 passed, 44 skipped; smoke real con uvicorn; ERD y README actualizados.
- Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 7 — Paneles y TPV visual (completada 2026-09-11)

- Backend: API de paneles en `/api/v1/catalog` (11 operaciones): CRUD y reordenación de
  paneles y subpaneles, `PUT /panels/{id}/items` reemplaza la rejilla completa con
  validación atómica, `GET /panels` = árbol con snapshot de producto embebido; auditoría
  `catalog.panel_*`; permisos `products.view`/`products.edit`.
- Frontend (`frontend/`): React 18 + TS + Vite + Tailwind + Zustand. Pantalla de venta
  táctil/ratón/teclado: navegación panel→subpanel, rejilla posicionada, ticket local
  persistente (BigInt céntimos, mili-unidades, desglose IVA), búsqueda local (F2) con
  código de barras exacto, lector wedge global, ayuda (F1); Cobrar deshabilitado
  (llega con la fase de venta). 36 tests vitest en verde + build limpio.
- Suite backend: 31 passed, 51 skipped (7 tests E2E de paneles requieren PostgreSQL).
- Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 8 — Motor de ventas (completada 2026-09-11)

- Motor de ventas en `/api/v1/sales` (9 operaciones): crear/recuperar borrador, líneas de
  producto (snapshot precio/IVA) o libre, cantidad, descuento con permiso `sales.discount`,
  cobro en sesión de caja, anulación con motivo y devolución como orden negativa enlazada
  (una sola por venta, cantidades parciales ≤ vendido); `409 SALE_ALREADY_PAID` sobre cobrados.
- Cálculo puro en `app/domain/sales.py` (PVP con IVA, half-up, mili-unidades) validado contra
  un oráculo Decimal independiente (3.520 combinaciones); operaciones críticas transaccionales
  con `SELECT … FOR UPDATE`; ventas cobradas/anuladas nunca se borran; eventos genéricos en
  `sale_events` para un futuro `FiscalAdapter` (sin implementar); auditoría `sales.*`.
- Suite: 40 passed, 58 skipped (7 tests E2E del motor requieren `TPV_TEST_DATABASE_URL`);
  README actualizado. Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 9 — Pagos (completada 2026-09-11)

- Cobro en el cierre (`POST /orders/{id}/close`): pagos obligatorios, el **backend** recalcula
  el total y exige suma exacta (422 `PAYMENT_INSUFFICIENT`/`PAYMENT_EXCESS`); `tendered` solo
  en efectivo → cambio devuelto en la respuesta y congelado en `sale_closed` (no se persiste);
  pagos, totales y evento en la misma transacción (parcial → rollback total; doble close →
  409; concurrentes → un ganador). La devolución también cobra (pagos positivos sobre la
  orden negativa, sin cambio).
- Formas de pago configurables (`payment_methods`, kind cash/card/other) con CRUD en
  `/api/v1/admin/payment-methods`; permisos `payments.take`/`payments.refund`/`admin.parameters`
  (lectura `sales.sell`).
- Suite: 51 passed, 71 skipped (13 tests E2E de pagos + 11 de dominio del cobro); README
  actualizado. Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 10 — Caja (completada 2026-09-11)

- Caja en `/api/v1/cash` (8 endpoints): apertura con fondo inicial, movimientos de
  entrada/salida con motivo, arqueos parciales por denominaciones y cierre Z que congela
  esperado/contado/diferencia en la fila de la sesión; una sola sesión abierta por terminal
  (índice parcial único — la carrera de dos aperturas concurrentes deja un ganador y un 409).
- Informes X (sesión abierta) y Z (histórico): efectivo esperado SIEMPRE recalculado en el
  backend (fondo + ventas en efectivo − devoluciones en efectivo + entradas − salidas) y
  totales por forma de pago; matemática pura en `app/domain/cash.py`.
- Permisos `cash.open`/`cash.movements`/`cash.close`/`reports.view`; auditoría `cash.*`.
  Suite: 57 passed, 78 skipped (6 de dominio de caja + 7 E2E DB-gated); README actualizado.
- Sin cambios de arquitectura. Pendiente: fase **Análisis URY**.

## Fase 11 — Tickets y facturas (completada 2026-09-11)

- Documentos en `/api/v1/documents` (+ configuración en `/api/v1/admin`): ticket emitido
  automáticamente en el cobro y en la devolución (misma transacción; numeración segura por
  terminal vía `document_sequences`), facturas bajo demanda de 1..N ventas cobradas del mismo
  cliente con NIF (serie por año; una venta no se factura dos veces), rectificativas parcial
  y total con importes negativos (`invoices.rectified_invoice_id`, migración `0003`), anulación
  con motivo y reimpresión con contador.
- Payload congelado (snapshot histórico): líneas, pagos, totales y cabecera se renderizan al
  emitir y nunca se regeneran; el logo de cabecera vive como fichero en el volumen
  `TPV_DATA_DIR/logos` (referencia en `parameters`) y se embebe como data URI — cambiar logo
  o datos fiscales no altera los documentos emitidos; sin logo se genera sin él. Sin
  Veri*Factu/TicketBAI (plugin futuro `FiscalAdapter`).
- Desviación menor de ruta: ARCHITECTURE §7.2 decía `POST /api/v1/sales/invoices`; implementado
  como `POST /api/v1/documents/invoices` (facturas desde ventas cobradas, no desde tickets) —
  §7.2 corregido en este cierre. Suite: 70 passed, 92 skipped (13 dominio + 14 E2E DB-gated);
  README actualizado. Pendiente: fase **Impresión** (render físico del documento).

## Fase 12 — Impresión (completada 2026-09-12)

- Cola de impresión desacoplada de ventas vía `PrinterAdapter` (`PrintQueue`, jobs con
  dedupe por documento): el cobro encola el ticket en su misma transacción (sin impresora
  configurada no rompe); ciclo `queued → sent → printed/failed/cancelled` con reintentos
  controlados (máx. 3, backoff 2/6/18 s, recuperación manual auditada) y trazabilidad
  (`attempts`/`last_error`/`sent_at`/`printed_at`, auditoría `printing.*`).
- CRUD de impresoras en `/api/v1/admin/printers` (red `host:puerto` o agente por `device_id`,
  una default activa por tipo, anchos 32/42/48) y cola en `/api/v1/printing` (consulta,
  retry/cancel/confirm, `POST /printing/dispatch` con `SKIP LOCKED`, copias con payload
  congelado); logo de cabecera dimensionado al ancho térmico real (384/512/576 px, nunca
  amplía). Cocina = `printer_kind`, copia = job nuevo. Drivers reales (ESC/POS, Windows,
  tpv-agent) llegan en la fase 13; hoy `NullPrinterAdapter`. Sin migración nueva
  (printers/print_jobs ya existían). Suite: 92 passed, 101 skipped (22 dominio + 9 E2E
  DB-gated); README actualizado. Pendiente: fase **Hardware**.

## Fase 13 — Hardware (completada 2026-09-12)

- Adaptadores de periféricos independientes de fabricante en `app/adapters/hardware.py`:
  cinco Protocolos `runtime_checkable` (`CashDrawerAdapter`, `BarcodeScannerAdapter`,
  `PaymentTerminalAdapter`, `CashDroAdapter` —solo interfaz—, `CustomerDisplayAdapter`)
  registrados en `app.state.hardware` (`HardwareAdapters.defaults()`); sin drivers reales
  aún (tpv-agent, fases 13-14): sustitutos honestos Null (cajón/escáner/display) y
  Simulated marcado «SIMULADO» (pinpad aprueba con `auth_code="SIM-…"`, CashDro entrega
  completa).
- Único encastre en el motor: el cierre (`close_order`) abre el cajón DESPUÉS del commit
  si algún pago usa una forma con `opens_drawer`, con el adaptador inyectado desde la API
  (fase 11, `drawer_adapter` opcional); `HardwareError` (code estable) se registra y la
  venta sigue cobrada — hardware nunca en el camino crítico.
- Suite: 101 passed, 104 skipped (9 unitarios de adaptadores + 3 E2E DB-gated); README
  actualizado. Única edición de ARCHITECTURE: identificador `CashDropAdapter` →
  `CashDroAdapter` (nombre que fija el prompt) e inventario de interfaces al día.
  Pendiente: fase **WebSocket**.

## Fase 14 — WebSocket (completada 2026-09-12)

- Hub `/api/v1/ws` (§8): auth obligatoria en el primer frame (4401 inválido/revocado,
  4400 frame no-auth), suscripción por temas con autorización (denegado no corta:
  `subscribed.denied`), replay por `since_id` desde `event_log` (límite configurable),
  latido ping 15 s y protocolo ready/subscribed/event/unsubscribed/error.
- Eventos nacen en la MISMA transacción del cambio (`event_log` + staging + listeners
  `after_commit`/`after_rollback`): nadie recibe un evento sin confirmar; el bus en
  memoria nunca bloquea al negocio (suscriptor lento → se descarta y se cura con replay).
- Productores: ventas (7), caja (4), documentos (3), catálogo (21, `catalog.changed`),
  impresión (impresora caída → `terminal:{id}`/`system`); KDS llega en fases 15-17 y el
  agente (`agent:{id}`) en la 13. Única edición de ARCHITECTURE: tema `catalog` añadido
  a §8.1. Suite: 110 passed, 113 skipped (9 unitarios bus + 9 E2E WS DB-gated).

## Fase 16 — Móvil camarero (PWA móvil) (completada 2026-09-12)

- PWA independiente en `frontend/mobile/` sobre la misma API: login PIN/contraseña,
  dashboard, venta con auto-save (pedido y líneas nacen en el servidor; cobro por forma
  de pago con cambio calculado por el backend), pedidos abiertos, consulta de productos,
  informe X del turno, histórico Z y caja completa — todo filtrado por permisos; Mesas
  solo se muestra si la sonda detecta el módulo de restaurante (fase **Modo restaurante**).
- Sin lógica de negocio en el cliente: total siempre del `GET` del pedido, contratos
  validados con Zod (dinero string, §3), service worker propio que jamás cachea `/api/`,
  terminal local en Ajustes (el módulo de terminales llega con **Administración**).
- 26 tests vitest en verde; build limpio (manifest + iconos + SW). Sin cambios de
  arquitectura.

## Fase 18 — Offline / reconexión (completada 2026-09-12)

- Idempotencia en el backend sobre la tabla ya existente `idempotency_keys` (sin migración):
  cabecera `Idempotency-Key` con huella SHA-256 de método+ruta+cuerpo, lookup ANTES de validar
  negocio, respuesta congelada en la misma transacción (replay byte-fiel 24 h), reutilizar la
  clave con otro contenido → 409 `IDEMPOTENCY_KEY_REUSED`, carrera resuelta por la BD; clave
  obligatoria en `close` (422 `IDEMPOTENCY_KEY_REQUIRED`), opcional en create-order, líneas y
  caja. 22 unitarios + 8 E2E DB-gated.
- Móvil: sonda `GET /healthz` (TypeError = sin servidor; cualquier HTTP = escucha) con banner
  y re-sondeo cada 15 s solo caído; copia de catálogo validada con Zod; outbox append-only que
  drena EN ORDEN al reconectar con `Idempotency-Key` por operación (4xx → descarta con aviso,
  fallo de red conserva la cola); cobrar/anular y caja requieren servidor; total en cola =
  estimado etiquetado. 44 tests + build limpio.
- Mostrador: misma sonda y banner; catálogo persiste en `tpv-terminal-catalog` (fallback
  validado con Zod) y se refresca al reconectar; el ticket ya era local persistente y no hay
  operación de venta contra servidor (Cobrar sigue deshabilitado) → sin outbox. 91 tests +
  build limpio. Matriz completa en `frontend/README.md`.
- ARCHITECTURE: sin cambios de arquitectura — solo dos desviaciones menores de §12.2
  corregidas en el cierre (persistencia localStorage, no IndexedDB; conflicto = descarte con
  aviso, sin resolución automática). Pendiente: ejecutar los 8 E2E de idempotencia en un
  equipo con PostgreSQL.

## Fase 15 — Informes (completada 2026-09-12)

- Backend `/api/v1/reports` de solo lectura bajo `reports.view` (sin auditoría ni eventos,
  como los informes X/Z de caja): tickets emitidos, facturas (listado + detalle reutilizando
  el render de `/documents`), cierres Z pasados y estadísticas (resumen con desglose de IVA,
  por producto/categoría/camarero/forma de pago/periodo). Anti-tabla-completa (§13): `from`/`to`
  obligatorios y acotados a 366 días, agregaciones en SQL y páginas de ≤200 filas.
- Mostrador: vista «Informes» (botón en la barra superior) con 9 pestañas, rango por defecto
  de 7 días, paginación en servidor (50/página, Anterior/Siguiente) y 403 visible sin
  `reports.view`; contrato espejo validado con Zod en `src/lib/reports.ts`. 101 tests y build
  limpio.
- Desviación menor de §7.2 declarada y corregida en ARCHITECTURE.md: las rutas reales son
  `/reports/cash-closures` y `/reports/stats/*` (no `daily-close`); las equivalencias del
  legado (`/reports/legacy/*`) siguen pendientes de su fase.
- Pendiente: ejecutar los 12 tests E2E de informes en un equipo con PostgreSQL (aquí la suite
  queda en 144 passed / 133 skipped).

## Fase 17 — Seguridad (completada 2026-09-12)

- Auditoría completa de las 17 áreas del prompt (autenticación, autorización, SQL injection,
  XSS, CSRF, CORS, secrets, logs, sesiones, rate limiting, permisos, exposición PostgreSQL,
  backups, endpoints administrativos, WebSocket, subida de archivos, dependencias): veredicto
  **sin vulnerabilidades críticas ni altas en producción** — la base de fases anteriores resultó
  sólida (Argon2id con hash señuelo, JWT verificado antes de BD, refresh solo-SHA-256 con
  rotación y cookie SameSite=strict, RBAC leído por petición, CORS deny-por-defecto, WS con
  mapa cerrado de temas, logo con nombre fijo y mime whitelist, sin multipart). Informe íntegro
  en `docs/security/AUDIT-2026-09-12.md`.
- Correcciones aplicadas: `.gitignore` en raíz y backend (no existían: blindan `.env`,
  `backend/data/` y caches ante un futuro `git init`, ADR-008). Riesgo aceptado y documentado:
  5 avisos npm SOLO en devDependencies (vite/vitest/esbuild; los fixes exigen majors breaking
  con cero impacto en el bundle de producción); pip-audit: entorno Python sin vulnerabilidades
  conocidas.
- Suites intactas tras la auditoría: backend 144 passed / 133 skipped, mostrador 101 passed,
  build limpio (287 kB JS). Sin cambios de arquitectura.

## Fase 24 — Rendimiento (completada 2026-09-12)

- Auditoría «medir primero» (sin PostgreSQL local: SQL auditado de forma estática). API medida
  contra uvicorn real: healthz 1,9 ms p50, readyz fail-fast (503 en 2,6 ms), 401 por JWT ~3 ms
  sin tocar BD. SQL: los 30 índices del schema cubren todas las lookups calientes
  (idempotency.key=PK, token_hash=UNIQUE, replay por topic+id), cero N+1 (snapshots POS y
  paneles en 3 consultas), paginación ya presente en todos los listados; WS (outbox 1024,
  heartbeat 15 s), concurrencia (FOR UPDATE / SKIP LOCKED ya testeados) y memoria acotados.
  Frontend: mostrador 287 kB JS / 86 kB gzip, móvil 250 kB / 73 kB; stores con selectores →
  vender solo re-renderiza el panel del ticket.
- Única mejora implementada —la única que la medición justifica—: `GZipMiddleware` (mín. 1 KiB,
  nivel 6): catálogo POS 94 kB → 13 kB (7,2x) por ~2 ms de CPU; respuestas diminutas quedan sin
  comprimir. 3 tests nuevos (`tests/test_gzip_middleware.py`) + verificación en vivo. Nada de
  índices a ciegas sin EXPLAIN. Informe en `docs/performance/RENDIMIENTO-2026-09-12.md`.
- Suites tras la fase: backend **147 passed / 133 skipped**, mostrador **101 passed**. Sin cambios
  de arquitectura. Pendiente para el despliegue: EXPLAIN real con `pg_stat_statements` y purga de
  tablas acumulativas en el worker de limpieza.

## Fase 19 — Testing (completada 2026-09-12)

- Auditoría de cobertura de las 13 áreas del prompt (unit, integración, API, BD, frontend, E2E,
  concurrencia, pagos, caja, cierres, devoluciones, permisos, WebSocket): todas tienen tests;
  hueco real detectado y tapado — **no existía ninguna prueba automatizada del muro de
  autenticación** (la fase Seguridad lo verificó solo estáticamente).
- Nuevo `tests/test_auth_wall.py`: **90 casos generados desde el contrato OpenAPI real**
  (`app.openapi()`, 95 operaciones) que llaman cada operación de negocio sin credenciales y
  exigen 401 + código estable. Excluidas con justificación: healthz/readyz (sondas),
  login/pin (obtención de credenciales), logout (idempotente por diseño); refresh responde
  401 TOKEN_INVALID (cookie ausente) — fijado como contrato.
- Estrategia documentada en `docs/testing/ESTRATEGIA-2026-09-12.md`: pirámide, mapa
  área→ficheros con conteos, convención DB-gated (`TPV_TEST_DATABASE_URL`) y pendientes
  honestos (correr los 133 DB-gated con PG, Playwright E2E, smoke PWA en dispositivo).
- Suites: backend **370 tests — 237 passed / 0 fallos / 133 DB-gated en skip** (+90 del muro),
  mostrador **101 passed**, móvil **44 passed**. Sin cambios de arquitectura.

## Fase 21 — Instalador (completada 2026-09-12)

- Kit de instalación en `deploy/` para Windows sin conocimientos técnicos: `instalar.bat` →
  `install.ps1` (PowerShell 5.1, todos los textos en español) instala o reutiliza PostgreSQL 16
  (servicio TPV-PostgreSQL), crea BD `tpv` + rol `tpv_app`, venv + dependencias, `alembic upgrade
  head`, seed condicional y alta interactiva del admin; compila los dos frontends con
  `--base=/app/…` y los deja servidos por la API (§1.4). Secretos nuevos y rotados en `conf\.env`
  (fuera del repo, ADR-008); contraseña de admin solo por consola.
- Operación sin consola: auto-arranque al iniciar Windows (tarea TPV-Servidor, SYSTEM), copia
  diaria a las 03:07 con retención 7/4/12 (tarea TPV-Backup, `-Kind auto`), firewall, logs
  diarios purgados a 30 días, resumen instalado en un .txt; además stop/start, backup manual,
  alta de usuarios y `uninstall.ps1` (por defecto conserva datos y copias).
- Descubrimiento del servidor: los clientes usan solo rutas relativas → abrir
  `http://IP:8000/app/tpv/` (mostrador) o `/app/movil/` (PWA) es toda la configuración de un
  terminal. Guía para no-programadores en `deploy/README-INSTALACION.md`.
- Validado: 7 .ps1 parse OK, suites en verde (backend 240 passed/133 skip, mostrador 101,
  móvil 44) y smoke del servidor sirviendo ambos frontends. Pendiente: HTTPS (fase
  Producción), cifrado de los dumps de copia (§10) y ensayo en un equipo Windows destino.

## Fase 22 — Administración (completada 2026-09-13)

- Panel de administración como tercera SPA (`frontend/admin/`, tema claro índigo, login SOLO por
  contraseña — el PIN queda para la operación del TPV): los 14 módulos del prompt (productos,
  categorías, departamentos, paneles, usuarios, camareros, permisos, formas de pago, terminales,
  dispositivos, impresoras, configuración + logo, backups, auditoría) con menú filtrado por
  permisos (`sectionsFor`); la autoridad real sigue siendo el 403 del backend. Contratos espejo
  con Zod (dinero string, §3); 17 tests vitest y build limpio (293 kB JS).
- Backend `/api/v1/admin`: usuarios con contraseña Argon2id y PIN opcional (revoca sesiones),
  roles con matriz de permisos (los del sistema bloqueados), terminales, dispositivos con token
  de alta mostrado UNA sola vez (solo SHA-256 en BD), impresoras con prueba, settings de negocio
  + logo, backups con `pg_dump` (contraseña SOLO por entorno, nunca argv ni audit) y consulta de
  `audit_log` con filtros. Servido en `/app/admin` (§1.4 actualizado en el cierre).
- Suites al cierre: backend 425 tests (0 fallos; 152 DB-gated en skip), admin 17, mostrador 118,
  móvil 44; smoke en vivo con uvicorn de los tres montajes estáticos.
- Pendiente: E2E de administración contra PostgreSQL real; añadir el build admin a
  `install.ps1` (hoy compila mostrador + móvil); consumir el token de dispositivo desde tpv-agent.

## Fase 23 — Despliegue (completada 2026-09-13)

- Kit Docker en `deploy/docker/` (compose con PostgreSQL 16 + API + Caddy como reverse proxy,
  Dockerfile multi-stage que compila las tres SPA con `--base=/app/…` y empaqueta backend +
  frontends + `pg_dump` en una sola imagen; healthchecks, logs rotados 10m×5, seed condicional,
  migraciones en cada arranque, `workers 1` por el hub WS). Secretos solo por `.env` con
  plantilla `.env.example` (placeholders CAMBIAME + comandos de generación, ADR-008); 5 puertos
  de BD sin exponer; `backup.sh`/`restore.sh` con retención.
- `README-DESPLIEGUE.md` documenta los 7 procedimientos exigidos (instalación, actualización,
  backup, restore, cambio de servidor, alta de TPV, alta de móvil) + HTTPS (MODO A HTTP plano /
  MODO B CA interna de Caddy o Let's Encrypt), logs y tabla de recuperación ante fallo.
- Smoke real en vivo contra la pila: migraciones+seed en BD virgen, healthz/readyz, los tres
  frontends vía proxy, alta de admin, login y endpoints autenticados, backup+restore. La
  validación destapó y arregló 3 bugs latentes del repo: 0003 duplicaba columna que 0001
  (create_all desde metadatos actuales) ya creaba (ahora idempotente con `sa.inspect`);
  referencias adelantadas List-antes-de-Row en `reports.py` que revientan con pydantic moderno;
  `touch_last_login(session, user)` pasaba el objeto User donde se esperaba UUID (rompía TODO
  login exitoso contra BD real — los tests DB-gated estaban en skip).
- Suites al cierre: backend 425 tests (0 fallos, 152 DB-gated en skip). Pendiente: ensayar el
  MODO B con dominio real, cifrado de dumps (§10), tpv-agent y E2E contra PostgreSQL.

## Fase 24 — QA final (completada 2026-09-13)

- QA como usuario real contra la pila Docker (api+PG+Caddy, :8081): los 25 escenarios del prompt
  en verde — arnés `docs/testing/qa_scenarios.py` (main 62/62, offline 3/3, down 3/3, recovery
  6/6), informe en `docs/testing/QA-FINAL-2026-09-13.md`.
- Primera ejecución de la suite DB-gated contra PostgreSQL real destapó y arregló 11 bugs de app
  (destacados: permiso de descuento `sales.discount`→`orders.discount` — nadie podía descontar—,
  `await` faltantes sobre AsyncSession, orden no determinista de órdenes en la factura).
- Suites al cierre: backend 424 passed/1 skipped (0 fallos), mostrador 118, admin 17, móvil 44.
- Pendiente (fuera de alcance QA): API de clientes (los permisos customers.* existen pero no hay
  endpoints → facturación siempre 409 sin cliente), tpv-agent real, service worker probado a mano.

## Fase 25 — Revisión final de arquitectura (completada 2026-09-13)

- Revisión como arquitecto externo con verificación dirigida sobre el código; informe con
  propuestas en `docs/REVISION-ARQUITECTURA-2026-09-13.md`. Sin cambios de arquitectura y sin
  código nuevo (la fase solo propone).
- Veredicto: el monolito modular es correcto para la escala; capas, dependencias, concurrencia,
  seguridad, BD y despliegue verificados sin hallazgos estructurales.
- Propuestas: P1 API de clientes (facturación inoperante) y tpv-agent real; P2 contratos
  triplicados en las 3 SPA (test de contrato), outbox PWA → IndexedDB, cifrado de backups;
  P3 higiene (helpers de error duplicados ×7, date.today() local en facturas, N+1 acotado,
  ORDER BY en líneas de factura, fábricas de tests a conftest).

## Fase 26 — Analizar URY antes de diseñar nuestra UI (completada 2026-09-13)

- Análisis del repo público ury-erp/ury (POS sobre Frappe/ERPNext) en docs/ury-analysis.md:
  pila, navegación, flujo de venta, tablet/móvil, estados, táctil, offline, impresión y pagos,
  con tabla de decisión adoptar/adaptar/no adoptar y motivo. Sin código nuevo.
- Conclusión: nuestra pila UI y nuestros invariantes (PWA offline, pago mixto, caja diaria)
  son iguales o superiores a los de URY; a importar: sidebar de categorías, semáforo de mesas
  con tiempos, filtro «recién cobradas» y (con backend) comentarios por línea, cliente+favoritos,
  impresoras por zona y motivo de KOT. A no importar: QZ Tray, i18n/RTL y su modelo de
  un frontend por dispositivo.

## Fase 27 — Diseñar nuestro Design System inspirado en URY (completada 2026-09-14)

- Design System definido como documento en `docs/design-system.md` (sin código ni lógica de
  negocio): tokens semánticos de color para los 3 temas reales (mostrador oscuro+ámbar,
  móvil teal, admin índigo), tipografía con `tabular-nums` para dinero, escala táctil
  (44/48/56 px), componentes base (botones, inputs+keypad, cards, dialogs, toast, tablas)
  y patrones de dominio: producto, ticket, pagos, mesas (semáforo URY mapeado a nuestros
  estados) y cocina (KDS), más navegación por SPA y estados transversales.
- Prioridades en orden (velocidad → legibilidad → táctil → accesibilidad → coherencia) con
  reglas operativas; 1 toque añade producto y la ficha por pulsación larga (nunca doble clic);
  estados de mesa/cocina siempre color+texto. Sin cambios de arquitectura.
- Adopción sin romper: obligatoria en las fases futuras (restaurante, KDS) y pantallas
  nuevas; lo ya construido (QA verde) no se reescribe; kit compartido `@tpv/ui` solo si
  aparece un 4º cliente (criterio de la revisión 25).

## Fase 28 — Construir el TPV visual (completada 2026-09-14)

- Mostrador completo sobre el Design System, ahora en código: tokens semánticos en
  tailwind.config.js (surface/ink/accent/success/warning/danger/info + foco visible),
  toasts propios en zustand (éxito 4 s, error persistente, máx 3, reemplazo por clave) y
  keypad táctil propio (coma única, «00», topes del contrato de dinero, nunca teclado del SO).
- La venta entera: TopBar con estado SIEMPRE visible y con texto (conexión, terminal,
  caja), categorías + productos, búsqueda (F2), ayuda (F1) y lector de códigos, ticket con
  +/- y descuento por línea (presets + keypad; `lineTotalDiscounted` réplica céntimo-exacta
  de `app/domain/sales.py`), cliente como hueco honesto (no hay API de clientes, P1 de la
  revisión 25) y cobro real de 720 px: pago mixto con pendiente en vivo, efectivo con
  entregado + cambio del backend y flujo create→líneas→close REANUDABLE con Idempotency-Key
  por paso (reintento tras fallo de red sin duplicar pedido, líneas ni cobro). F4 cobra;
  vaciado con ConfirmDialog; sin Three.js.
- Pendientes anotados: API de clientes (P1), arqueo/cierre de caja en mostrador (hoy en
  móvil/informes), impresión real del ticket (tpv-agent). Suites al cierre: frontend
  141 tests en verde y build `tsc -b && vite build` limpio; sin cambios de arquitectura.

## Fase 15 — Modo restaurante + mesas (completada 2026-09-14)

- Backend `/api/v1/restaurant` bajo `restaurant.operate`: CRUD de zonas y mesas (con
  `pos_x`/`pos_y` para el plano), abrir/traspasar/unir/dividir sesión de mesa, PATCH de
  sesión (camarero, comensales, notas) y pedir/cancelar cuenta; estado derivado
  free/open/bill; migración `0005_restaurant_floor` aditiva; tema WS «restaurant» añadido
  al hub; 8 tests E2E DB-gated.
- Mostrador: vista «Sala» (botón en la TopBar) con mapa 2D táctil — mesas posicionadas por
  porcentaje, semáforo siempre texto+tiempo (Libre/Abierta/Cuenta pedida, §3.2 del Design
  System), chips de zona + búsqueda, en vivo por WS con sondeo de 15 s; modo mesa con los
  paneles de venta y la comanda EN EL SERVIDOR (borrador del motor de ventas) y cobro con
  el CheckoutDialog de la fase 28; diálogos de abrir/traspasar/unir/dividir/sesión/ajustes
  del plano. Sin Three.js.
- Móvil: la pantalla «Mesas» (fase 16, hasta hoy sonda) lee ya el plano real
  (`GET /restaurant/tables`, solo lectura).
- Suites al cierre: backend 446 tests — 445 passed/1 skipped (0 fallos), frontend 159 tests
  en verde (24 ficheros), builds de las 3 SPA limpios. Sin cambios de arquitectura.
  Pendiente: Reservation (fuera del alcance del prompt), comanda a cocina/KOT (fase KDS),
  impresión de comanda (tpv-agent).

## Fase 16 — Móvil camarero (pedido en mesa) (completada 2026-09-14)

- PWA móvil: flujo Mesa → categoría → producto → modificadores/notas → enviar. Tocar una
  mesa libre la abre (POST `/restaurant/tables/{id}/open` con terminal + comensales
  opcionales + Idempotency-Key; abrir CREA el pedido) y entra directa al pedido; abiertas
  o con cuenta entran en su pedido. Nueva pantalla de pedido: chips de categoría
  (`GET /catalog/categories`, array pelado), carta de botones grandes
  (`GET /catalog/pos`; toque añade, pulsación larga añade con nota, pesados con teclado
  de kilos) y ticket del servidor debajo — +/- en no pesables, cantidad tocable en
  pesables, nota editable y quitar línea (PATCH/DELETE de `/sales/orders/{id}/lines`).
  «Enviar» vuelve a Mesas: todo queda ya persistido línea a línea en el servidor.
- Corregido bug de fase 30: la pantalla Mesas parseaba `GET /restaurant/tables` como array
  pelado cuando el backend devuelve `{items}` — habría reventado en Zod al abrir.
- Honestidad: modificadores = notas de línea (el backend no tiene concepto de
  modificadores; decisión de fase Productos); la mesa es estado compartido → mutaciones
  SIEMPRE con servidor (sin outbox, que es del ticket de barra), sin conexión la pantalla
  queda en lectura; sincronía con el mostrador ya live (WS/sondeo) y cocina queda para la
  fase KDS. El waiter del seed ya tiene `products.view` (sin migración).
- Suites al cierre: móvil 47 tests en verde (9 ficheros) + build limpio, mostrador 162
  tests (25 ficheros) + build limpio; backend sin cambios (446 tests de la fase anterior).
  Sin cambios de arquitectura. Pendiente: KDS (fase 17), impresión de comanda (tpv-agent).

## Fase 17 — KDS (completada 2026-09-14)

- Backend `/api/v1/kds` bajo `kds.operate`: tablero (`GET /kds/board`, cabecera DERIVADA de
  las líneas — el cliente nunca decide estado), estaciones, avance de línea y comanda
  (PATCH de estado, 409 si la transición no procede), urgencia y KOT (auto por línea nueva
  al vender, en la MISMA transacción y sin romper el cobro; reimpresión completa, 409 sin
  impresora de cocina). Rectificación de comanda mientras pending/preparing, congelada en
  ready/served; quitar línea de venta BORRA la de cocina (FK sin cascade, trazabilidad en
  `event_log`) y la comanda sin líneas desaparece. Migración 0006_kds_stations; eventos
  `kds.*` en el tema «kds».
- Cuarta SPA `frontend/kds` (dev 5175, tema oscuro): 4 columnas NUEVO/PREPARANDO/LISTO/
  SERVIDO, filtro por estación que recorta líneas (oculta comandas que quedan vacías),
  urgencias, tiempos con semáforo 10/20 min + aviso sonoro una vez por comanda, avance
  tocando la línea, botones masivos y reimpresión. En vivo por WS (evento ⇒ releer el
  tablero, debounce 200 ms; el sobre nunca lleva estado) con sondeo de respaldo de 15 s
  sin hub. Cero negocio en cliente.
- Corregido el handshake WS del cliente compartido: `auth` es SIEMPRE la primera trama
  (antes el hub cerraba con 4401 en bucle); suscripción al recibir «ready». Fix aplicado
  a mostrador, móvil y KDS.
- Suites al cierre: backend 481 tests — 480 passed/1 skipped (0 fallos), kds 23 tests en
  verde + build limpio, mostrador 185 (28 ficheros) + build limpio, móvil 47 + build
  limpio. ARCHITECTURE: solo §1.4 (montaje `/app/kds` ya real) y §3 (KitchenStation y
  estado derivado); por lo demás, sin cambios de arquitectura. Pendiente: impresión real
  de KOT (tpv-agent).

## Fase 33 — Revisión AGPL / reutilización URY (completada 2026-09-14)

- Revisión dirigida antes de incorporar código de URY: búsqueda de marcas de URY y su
  stack (`ury`, `socket.io`, `qz-tray`, `jsrsasign`, `frappe`) en las 4 SPA y el backend
  + dependencias exactas en los 4 lockfiles → **cero código AGPL incorporado**: todas
  las coincidencias eran subcadenas al azar en hashes base64, salvo la cita textual
  «mesas/URY-style» en la descripción del parámetro `restaurant.enabled` (seed.sql),
  conservada como atribución honesta.
- Inventario por pieza con los 7 puntos del prompt en
  `docs/agpl/REVISION-AGPL-2026-09-14.md`: todo lo adoptado de URY (sidebar de
  categorías, semáforo de mesas, filtro recién cobradas, semáforo/timers del KDS, notas
  a cocina) está reimplementado desde cero. Pendiente de adaptar con diseño propio:
  enrutado de impresoras por sala/unidad + motivo de KOT (con tpv-agent) y
  cliente+pax+favoritos (bloqueado por la P1 de API de clientes).
- **ADR-009**: ratifica ADR-007 (sin obligaciones AGPL, licencia propia intacta) y fija
  el checklist obligatorio para incorporar CUALQUIER código de terceros (fichero,
  licencia, dependencias, copiar/modificar/estudiar, procedencia, obligaciones, ADR);
  con dudas → versión propia inspirada en su comportamiento.
- Sin cambios de arquitectura (única edición: el índice de referencias cita ahora
  ADR-001…ADR-009). Sin suites afectadas (no se tocó código).

## Fase 34 — Fiscalidad (Veri*Factu / TicketBAI) (completada 2026-09-14)

- Plugin fiscal desacoplado del motor (ADR-010): consume el outbox `sale_events` (fase 06)
  con `FOR UPDATE SKIP LOCKED`, transforma cada evento en UN `FiscalDocument`
  (`UNIQUE(sale_event_id)`, idempotente) y lo certifica vía `FiscalAdapter`; traza
  append-only en `fiscal_events` + resumen por descarga en `audit_log` (`fiscal.dispatch`).
  API `/api/v1/fiscal` (status/documents/dispatch) bajo `fiscal.view`/`fiscal.dispatch`;
  migración 0007 aditiva (2 tablas, 3 enums, 2 permisos). Dinero siempre string (§3); sin
  `updated_at` — el histórico ES `fiscal_events`; máquina de estados
  pending→sent→accepted/rejected (rejected reencolable/cancelable).
- Decisión de alcance del usuario (2026-09-14): **"ninguno aún"** — sin desarrollo legal.
  `TPV_FISCAL_PROVIDER=none` consume eventos sin certificar; verifactu/ticketbai quedan como
  esqueletos seleccionables que fallan con `FISCAL_NOT_IMPLEMENTED` (fail-fast al arrancar).
  Normativa verificada en fuentes oficiales: Veri*Factu aplazada a 1-1-2027/1-7-2027 (RDL
  15/2025); TicketBAI en vigor en Euskadi desde 1-1-2026 (tres regímenes, uno por
  Diputación). Documentado en `docs/fiscal/README.md` y `docs/decisions/ADR-010-*`.
- ARCHITECTURE actualizada con cambios reales: §0/§1.1/§1.2/§2 (componente 8 ya no es
  "interfaz vacía"), §4.1 (cobro deja `sale_event` en outbox), §14 y riesgos; índice cita
  ADR-001…ADR-010.
- Suites al cierre: backend **518 passed, 1 skipped** (485 + 34 tests fiscales nuevos: 28 de
  dominio sin BD + 6 E2E DB-gated; ajustados los recuentos de seed en `test_integrity`).
  Pendiente: desarrollar un adaptador real cuando se active un régimen (checklist en
  `docs/fiscal/README.md`).

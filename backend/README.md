# TPV Moderno — backend

Servidor FastAPI (Python 3.12+, SQLAlchemy 2.x async, PostgreSQL 16). Los clientes nunca
acceden a la base de datos: solo este proceso habla con PostgreSQL (ADR-002).

## Estructura

```
app/
  main.py        fábrica create_app() + instancia `app` (uvicorn app.main:app)
  core/          configuración (Settings), logging JSON, errores RFC 9457, middleware,
                 seguridad (Argon2id + JWT), limitador de intentos (fase 03) y
                 bus de eventos en memoria (fase 12)
  api/v1/        routers /api/v1 (healthz, readyz, auth, catalog, sales, payments,
                 cash, documents, printing, ws)
  db/            engine async perezoso, modelos SQLAlchemy (fase 01)
  domain/        lógica pura (ventas, pagos, caja, documentos, impresión) —
                 api → services → domain
  services/      motores de dominio (auth, catálogo, paneles, ventas, pagos, caja,
                 documentos, impresión, eventos) — nunca importan de api
  repos/         acceso a datos (auth, catálogo, paneles, ventas, pagos, caja,
                 documentos, impresión, eventos)
  adapters/      PrinterAdapter de impresión (NullPrinterAdapter, fase 10) y los
                 cinco Protocolos de periféricos (fase 11: cajón, escáner,
                 pinpad, CashDro, display de cliente); FiscalAdapter y drivers
                 reales, fases 13-14
alembic/         migraciones (0001 = esquema completo; 0002 = catálogo/tarifas, fase 04;
                 0003 = rectificativas, fase 09)
tests/           test_api.py, test_catalog_schemas.py, test_security.py,
                 test_events_bus.py, test_idempotency_unit.py y los de
                 dominio/adaptadores (sin BD) ·
                 test_integrity.py, test_auth_api.py, test_catalog_api.py,
                 test_panels_api.py, test_sales_api.py, test_payments_api.py,
                 test_cash_api.py, test_documents_api.py, test_printing_api.py,
                 test_hardware_api.py, test_ws_api.py y test_idempotency_api.py
                 (requieren PostgreSQL: TPV_TEST_DATABASE_URL)
```

## Puesta en marcha

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .                                    # o: pip install fastapi uvicorn[standard] ...

# Variables de entorno (plantilla .env.example; secretos nuevos, ADR-008)
set TPV_DATABASE_URL=postgresql+psycopg://USUARIO:CONTRASENA@localhost:5432/tpv
set TPV_JWT_SECRET=<generar: python -c "import secrets; print(secrets.token_urlsafe(64))">

alembic upgrade head                                # crea el esquema (fase 01)
uvicorn app.main:app --reload --port 8000
```

Endpoints del esqueleto: `GET /api/v1/healthz` (liveness, sin BD) y `GET /api/v1/readyz`
(readiness, `SELECT 1`; 503 problem+json si la BD no responde). Errores en formato
RFC 9457 (`application/problem+json`) con `code` estable. Cada respuesta lleva
`X-Request-ID` correlacionado con los logs JSON.

## Autenticación (fase 03, `ARCHITECTURE.md` §6)

- `POST /api/v1/auth/login` — usuario+contraseña (Argon2id) → access token + cookie de refresh
- `POST /api/v1/auth/pin` — PIN de camarero → access token con alcance `pos` (venta; nunca administra)
- `POST /api/v1/auth/refresh` — rotación: consume el refresh (cookie `tpv_refresh` HttpOnly o cuerpo) y emite uno nuevo; reutilizar uno rotado cierra la sesión
- `POST /api/v1/auth/logout` — revoca la sesión y borra la cookie (idempotente)
- `GET  /api/v1/auth/me` — identidad, rol, permisos y alcance del token actual

El access token es un JWT HS256 de 15 min (claims `sub`, `sid`, `role`, `scope`) que viaja
en `Authorization: Bearer`; la revocación es inmediata porque la sesión (`user_sessions`)
y los permisos del rol se comprueban en BD en cada petición. Los errores 401 llevan
`WWW-Authenticate: Bearer`. Login y PIN están limitados por IP y ruta (429 + `Retry-After`)
y cada acceso (válidos y fallidos) queda en `audit_log`. `TPV_JWT_SECRET` es obligatoria en
producción (mínimo 32 caracteres); sin ella la app arranca pero los endpoints auth fallan.
Los clientes sin BD activa ven fallos genéricos (500), nunca detalles internos.

## Catálogo de productos (fase 04, `/api/v1/catalog`)

CRUD de departamentos, categorías, productos, tarifas de precio e IVA con vigencia.
Reglas del contrato: el dinero viaja **siempre como string** en JSON (nunca float, §3);
`DELETE` = baja lógica (`active=false`); lectura exige `products.view` (los tokens de PIN
con alcance `pos` pueden leer: leer el catálogo es vender) y escritura `products.edit`.
Cada operación queda en `audit_log` (`catalog.*`).

| Grupo | Endpoints |
|---|---|
| Departamentos | `GET/POST /departments` · `PATCH/DELETE /departments/{id}` · `POST /departments/reorder` |
| Categorías | `GET/POST /categories[?department_id=]` · `PATCH/DELETE /categories/{id}` · `POST /categories/reorder` |
| Productos | `GET/POST /products` (búsqueda/paginación) · `GET/PATCH/DELETE /products/{id}` · `GET /products/{id}/prices` (histórico) · `POST /products/reorder` |
| Tarifas | `GET/POST /tiers` · `PATCH /tiers/{id}` |
| IVA | `GET /tax-rates[?only_current=]` · `POST /tax-rates` (nueva versión; cierra la vigente) |
| TPV | `GET /pos` — snapshot vendible en **3 consultas** (productos+jerarquía+IVA, códigos de barras y precios por tarifa), sin N+1 |

Detalles: cambiar el precio no lo sobreescribe — cierra la fila vigente de `product_prices`
y abre una nueva (histórico inmutable); los códigos de barras, imágenes y precios por
tarifa se envían dentro del payload del producto y, si el campo viene, la colección se
reemplaza completa (campo ausente = no tocar). Los modificadores de producto no existen
en la arquitectura y no se exponen. La migración `0002_catalog_pricing` añade las tablas
`price_tiers`, `product_tier_prices`, `product_barcodes` y `product_images`.

## Paneles del TPV (fase 05, `/api/v1/catalog/panels`)

El árbol táctil **Paneles → SubPaneles → Productos** que pinta la pantalla de venta.
Cada respuesta de árbol (`GET /panels`) lleva el snapshot vendible de cada producto
embebido, así que el cliente no necesita una segunda consulta para dibujar botones.
Reglas: solo filas activas; un item cuelga de un panel (botón directo) o de un subpanel,
nunca de ambos (`ck_panel_items_parent`); orden por `sort_order` y posición libre en la
rejilla (`grid_row`/`grid_col`, 0-19); `PUT /panels/{id}/items` **reemplaza la rejilla
completa** y valida todo antes de tocar datos (404 producto inexistente/inactivo, 422
subpanel ajeno, 409 posición duplicada). El dinero sigue siendo string y las escrituras
quedan en `audit_log` (`catalog.panel_*`).

| Grupo | Endpoints |
|---|---|
| Paneles | `GET/POST /panels` · `PATCH/DELETE /panels/{id}` · `POST /panels/reorder` |
| SubPaneles | `POST /panels/{id}/subpanels` · `PATCH/DELETE /subpanels/{id}` · `POST /subpanels/reorder` |
| Rejilla | `PUT /panels/{id}/items` (reemplazo completo) · `DELETE /panel-items/{id}` |
| Snapshot | `GET /panels` — árbol con producto embebido · `GET /pos` — lista plana vendible |

Permisos: lectura `products.view`, escritura `products.edit` (mismos que el catálogo).
El diseñador visual de rejillas (arrastrar/colocar botones) llega con la fase de
Administración; aquí se consume y expone la API.

## Motor de ventas (fase 06, `/api/v1/sales`)

Ciclo completo de venta: **crear borrador → líneas → cobrar** (o anular/devolver).
"Guardar venta" es el borrador persistido (cada operación hace auto-save);
"recuperar venta" es `GET /orders?status=draft`. El cálculo vive en `app/domain/sales.py`
(puro, con oráculo de tests): PVP con IVA incluido → bruto → descuento → base por
cociente, redondeo half-up en céntimos; cantidades en mili-unidades (1 ud = 1000);
la devolución es una orden **negativa exacta** enlazada por `refunds` (una sola por
venta original). Toda operación crítica es transaccional y bloquea la fila del pedido
(`SELECT … FOR UPDATE`); las ventas cobradas o anuladas **nunca se borran**. Cada
cierre/anulación/devolución emite un evento genérico en `sale_events`
(`sale_closed` / `sale_voided` / `refund_issued`, dinero como string) — la interfaz
para un futuro `FiscalAdapter`, sin implementar aquí. Queda en `audit_log` como
`sales.order_created`, `sales.line_added/updated/removed`, `sales.order_closed`,
`sales.order_voided`, `sales.refund_issued`.

| Operación | Endpoint |
|---|---|
| Crear borrador | `POST /orders` (exige terminal activo; 1 draft por mesa) |
| Recuperar/listar | `GET /orders?status=draft` · `GET /orders/{id}` |
| Añadir línea | `POST /orders/{id}/lines` (producto con snapshot de precio/IVA, o artículo libre) |
| Editar línea | `PATCH /orders/{id}/lines/{line_id}` (cantidad, descuento, notas) |
| Quitar línea | `DELETE /orders/{id}/lines/{line_id}` (solo borrador) |
| Cobrar | `POST /orders/{id}/close` (carga en sesión de caja abierta del mismo terminal) |
| Anular | `POST /orders/{id}/void` (motivo obligatorio, autor y fecha) |
| Devolver | `POST /orders/{id}/refund` (líneas parciales ≤ vendido; 409 si ya hay devolución) |

Permisos: `sales.sell` (crear, editar líneas y cobrar), `sales.void` (anular),
`sales.refund` (devolver); descuento > 0 exige además `orders.discount` (403 sin él).
Errores clave: `409 SALE_ALREADY_PAID` (operación sobre un cobrado), `409 CONFLICT`
(mesa ocupada, venta sin líneas, doble anulación/devolución, caja cerrada o ajena).

## Pagos (fase 07, cierre de venta + `/api/v1/admin/payment-methods`)

El cobro vive en el cierre: `POST /orders/{id}/close` exige al menos un pago y
es el **backend** quien recalcula el total y valida que la suma de importes
cubra EXACTAMENTE la deuda (422 `PAYMENT_INSUFFICIENT` si falta, 422
`PAYMENT_EXCESS` si sobra sin efectivo que la absorba) — el frontend nunca es
autoridad para determinar que una venta está pagada. `tendered` solo se admite
en efectivo y debe cubrir el importe; el cambio (`tendered − amount`, sumado
entre todos los efectivos) no se persiste: se congela en el payload del evento
`sale_closed` y se devuelve en la respuesta (`change_total`). Pagos, totales y
evento se escriben en la MISMA transacción; el cobro parcial se rechaza con
rollback total (la orden sigue en borrador, sin pagos ni eventos) y un doble
close devuelve 409 `SALE_ALREADY_PAID` (bloqueo `FOR UPDATE` serializa cobros
concurrentes: exactamente un ganador). La devolución también cobra: `payments`
con suma exacta del importe devuelto, sin `tendered` ni cambio; sus pagos se
guardan positivos sobre la orden negativa.

| Grupo | Endpoints |
|---|---|
| Formas de pago | `GET/POST /admin/payment-methods` · `PATCH/DELETE /admin/payment-methods/{id}` (DELETE = baja lógica) |
| Cobro | `POST /orders/{id}/close` (pagos obligatorios, simples o mixtos) |
| Devolución | `POST /orders/{id}/refund` (pagos obligatorios, suma exacta) |

Formas de pago configurables en `payment_methods` (código único, `kind`
efectivo/tarjeta/otro —gobierna la semántica del cobro—, `opens_drawer`,
`sort_order`). Permisos: cobrar `payments.take`, devolver `payments.refund`,
lectura de formas `sales.sell`, escritura `admin.parameters`; auditoría
`payments.method_created/updated/deactivated` (y pagos incluidos en
`sales.order_closed`/`sales.refund_issued`). El mapa `sales.*` ↔ permisos del
seed (`orders.*`/`payments.*`) se unificará en la fase de Administración.

## Caja (fase 08, `/api/v1/cash`)

Sesiones de caja con ciclo **apertura → venta → X → cierre (Z)** (ARCHITECTURE §5):
`POST /sessions` abre con fondo inicial; una sola sesión abierta por terminal
(índice parcial único en BD — la carrera de dos aperturas concurrentes deja
exactamente un ganador y un 409). El efectivo **esperado** lo calcula SIEMPRE
el backend desde las piezas (fondo + ventas en efectivo − devoluciones en
efectivo + entradas − salidas, derivadas del signo de `orders.total_amount`);
la **diferencia** de cada arqueo es contado − esperado. Los arqueos parciales
(`POST /sessions/{id}/counts`, recuento por denominaciones) no cierran nada; el
cierre Z (`POST /sessions/{id}/close`) exige recuento y congela
esperado/contado/diferencia en la fila de la sesión. El listado X se consulta
N veces sin cerrar; el Z queda en el histórico (`GET /sessions?status=closed`)
para el cuadre diario. Ni movimientos ni arqueos ni cierre sobre una sesión
cerrada (409).

| Grupo | Endpoints |
|---|---|
| Apertura | `POST /sessions` (terminal activo, fondo inicial) |
| Consulta | `GET /sessions/current?terminal_id=` · `GET /sessions/{id}` · `GET /sessions/{id}/report` (X o Z) |
| Histórico | `GET /sessions?terminal_id=&status=&opened_from=&opened_to=` (informes Z) |
| Movimientos | `POST /sessions/{id}/movements` (entrada/salida con motivo obligatorio) |
| Arqueos | `POST /sessions/{id}/counts` (parcial) · `POST /sessions/{id}/close` (cierre Z) |

Permisos: abrir y consultar `cash.open` (el camarero abre y ve su X), movimientos
`cash.movements`, arqueos y cierre `cash.close`, histórico/cuadres `reports.view`;
auditoría `cash.session_opened`, `cash.movement_created`, `cash.count_recorded`,
`cash.session_closed`.

## Tickets y facturas (fase 09, `/api/v1/documents`)

Dos documentos separados por diseño: el **ticket** es comercial y se emite
AUTOMÁTICAMENTE en el cobro (y en la devolución, cuya orden es negativa y cuyo
ticket referencia al original vía `refunds`) — DENTRO de la misma transacción
del cobro, así que una venta cobrada sin ticket es imposible. La **factura** es
fiscal y va bajo demanda: agrupa 1..N ventas cobradas, positivas y del MISMO
cliente con NIF (serie por año, `invoices.series`); una venta no se factura dos
veces (`UNIQUE` en `invoice_lines.order_id`); anulación con motivo (nunca se
borra). La **rectificativa** usa su propia serie anual (`invoices.rect_series`,
defecto `R`) con importes negativos: *parcial* (los de la orden de devolución,
una sola rectificativa por devolución) o *total* (desglose de la original
invertido; sin líneas nuevas). Numeración segura: secuencias en
`document_sequences` con `INSERT … ON CONFLICT DO NOTHING` + `SELECT … FOR
UPDATE` — tickets por terminal (`tickets.series`, defecto `A`), facturas y
rectificativas por serie y año (`FAC 2026/000001`, `R 2026/000001`).

El documento es un **snapshot congelado**: el payload JSONB (líneas, pagos,
totales, cabecera) se renderiza en el momento de emitir y nunca se regenera —
cambiar el logo o los datos fiscales no altera los documentos ya emitidos. La
cabecera lleva los datos del negocio (nombre, NIF, dirección, teléfono en
`parameters`) y el **logo** configurado en Configuración/Parámetros: el fichero
vive en el volumen `TPV_DATA_DIR/logos/` (nunca en la BD) y se embebe en cada
documento como data URI. Sin logo configurado, el documento se genera sin él.
Solo png/jpeg, máximo 512 KiB. Sin nada específico de Veri*Factu/TicketBAI:
esa integración es un plugin futuro (`FiscalAdapter`, fases posteriores).

| Grupo | Endpoints |
|---|---|
| Ticket (emisión) | automático en `POST /sales/orders/{id}/close` y `…/refund` (respuesta con `ticket.id` y `ticket.doc_number`) |
| Ticket (consulta) | `GET /documents/tickets/{id}` · `POST /documents/tickets/{id}/reprint` (contador de reimpresiones) |
| Facturas | `POST /documents/invoices` (`order_ids`, 1..100) · `GET /documents/invoices/{id}` · `POST /documents/invoices/{id}/void` |
| Rectificativas | `POST /documents/invoices/{id}/rectifications` (`mode: partial\|total`, `refund_order_id` en parcial) |
| Configuración | `GET/PATCH /admin/business-settings` (datos fiscales) · `PUT/DELETE /admin/logo` |

Permisos: consultar/reimprimir tickets `tickets.reprint` (camarero y jefe),
emitir y rectificar facturas `invoices.issue`, anular `invoices.void`,
configuración del negocio y logo `admin.parameters`; auditoría
`documents.ticket_issued`, `documents.ticket_reprinted`, `documents.invoice_issued`,
`documents.invoice_voided`, `documents.invoice_rectified`,
`documents.business_settings_updated`, `documents.logo_updated/removed`.
Migración `0003_invoice_rectifications` añade `invoices.rectified_invoice_id`
(aditiva, sin UNIQUE: admite varias parciales).

## Impresión (fase 10, `/api/v1/printing` y `/api/v1/admin/printers`)

Capa de impresión **desacoplada de ventas** vía `PrinterAdapter`
(`app/adapters/printing.py`): quien emite un documento entrega su payload
congelado a la cola (`PrintQueue`) y punto. El cobro encola el ticket en la
MISMA transacción (`services/documents.py → services/printing.py`); sin
impresora configurada no encola nada y el cobro no se rompe. Los jobs son
idempotentes por documento (`dedupe_key = "ticket:<id>"`, índice único parcial
con `ON CONFLICT DO NOTHING`); las copias y el job de prueba crean trabajos
nuevos sin dedupe. La **cocina** es un `printer_kind` (no un adaptador) y la
**copia** es un job nuevo con el payload congelado. Hoy está registrado un
`NullPrinterAdapter`; los drivers reales (térmica ESC/POS, Windows, red 9100,
tpv-agent) implementarán la misma interfaz en la fase 13.

Ciclo del `PrintJob`: `queued → sent → printed`, con `failed` (error trazado
en `last_error`, `attempts` contados) y `cancelled`; `printed`/`cancelled` son
terminales. Reintentos controlados: máx. 3 intentos automáticos con backoff
2 s → 6 s → 18 s (tope 5 min); agotados, el job queda `failed` hasta el
reintento manual (`POST …/retry`, auditado, que resetea `attempts`). El
despacho es manual (`POST /printing/dispatch`) hasta que llegue el
tpv-agent/websocket (fases 13-14) y usa `FOR UPDATE SKIP LOCKED` (varios
despachadores concurrentes son seguros). El logo de cabecera se dimensiona al
ancho real de la impresora térmica (32/42/48 columnas → 384/512/576 px, nunca
se amplía, sin decodificar la imagen: cabeceras PNG/JPEG); formato
desconocido → se imprime sin logo, la cola nunca se rompe.

| Grupo | Endpoints |
|---|---|
| Impresoras | `GET/POST /admin/printers` (`?include_inactive=`) · `PATCH/DELETE /admin/printers/{id}` (baja lógica) · `POST /admin/printers/{id}/test` |
| Cola | `GET /printing/jobs[?status=&printer_id=&limit=]` · `GET /printing/jobs/{id}` |
| Ciclo de vida | `POST /printing/jobs/{id}/retry` · `…/cancel` · `…/confirm` (confirmación de impresión) |
| Despacho | `POST /printing/dispatch[?limit=]` (cola + reintentos vencidos) |
| Copias | `POST /printing/copies/tickets/{id}` · `POST /printing/copies/invoices/{id}` (payload congelado) |

Permisos: todo el CRUD de impresoras y la cola/despacho `admin.printers`,
copia de ticket `tickets.reprint`, copia de factura `invoices.issue`;
auditoría `printing.printer_created/updated/deactivated`,
`printing.job_retried`, `printing.job_cancelled`, `printing.test_enqueued`,
`printing.copy_enqueued`. Una sola impresora activa por defecto y tipo
(reasignación automática al marcar otra); el ancho térmico admite 32/42/48
columnas; `connection` admite `network` (exige `address host:puerto`) y
`agent` (exige `device_id` de un dispositivo activo, sin `address`).

## Hardware (fase 11, `app/adapters/hardware.py`)

Adaptadores de periféricos **independientes de fabricante**: el dominio solo
conoce los cinco Protocolos `runtime_checkable` — `CashDrawerAdapter`,
`BarcodeScannerAdapter`, `PaymentTerminalAdapter`, `CashDroAdapter` y
`CustomerDisplayAdapter` — registrados en `app.state.hardware`
(`HardwareAdapters.defaults()`). Cada integración es sustituible sin tocar el
motor de ventas: los drivers reales (tpv-agent, fases 13-14) serán solo OTRA
implementación de estas mismas interfaces.

Sustitutos honestos hoy, uno por tipo de dispositivo:

| Dispositivo | Hoy | Comportamiento |
|---|---|---|
| Cajón portamonedas | `NullCashDrawerAdapter` | Registra las aperturas solicitadas |
| Lector de códigos | `NullBarcodeScannerAdapter` | `next_scan()` devuelve lo encolado en tests (el lector vía tpv-agent llegará en la fase 13; el wedge del cliente de la fase 07 no pasa por aquí) |
| Terminal de pago | `SimulatedPaymentTerminalAdapter` | Aprueba siempre con `auth_code="SIM-…"`, resultado marcado «SIMULADO» |
| CashDro | `SimulatedCashDroAdapter` | Confirma la entrega completa, marcada «SIMULADA» (fase: SOLO interfaz) |
| Display de cliente | `NullCustomerDisplayAdapter` | Recuerda lo último que se le habría mostrado |

**Regla de oro**: el hardware nunca está en el camino crítico de una venta.
Los efectos físicos ocurren DESPUÉS del commit y son best-effort: el único
encastre actual es el kick del cajón en `close_order` — si algún pago usa una
forma con `opens_drawer` (fase 07), el servicio abre el cajón una vez
confirmada la venta, inyectado desde la API; un `HardwareError` (con `code`
estable, por defecto `HARDWARE_UNAVAILABLE`) se registra en el log y la venta
sigue cobrada con su ticket. `HardwareError` es la única excepción que un
driver puede lanzar.

Tests: `tests/test_hardware_adapters.py` (Protocolos, sustitutos y error, sin
BD) y `tests/test_hardware_api.py` (E2E: efectivo abre, tarjeta no, y un cajón
atascado no revienta la venta).

## WebSocket (fase 12, `/api/v1/ws`)

Hub único de sincronización en tiempo real (ARCHITECTURE §8): un solo endpoint,
todos los clientes (TPV de venta, KDS, supervision). **El WS acelera, nunca
bloquea**: la fuente de verdad es la tabla `event_log` y el negocio no espera
al socket. Cada cambio de negocio inserta su evento en `event_log` EN LA MISMA
transacción (si el cambio revierte, el evento nunca existió) y un listener
`after_commit` lo reparte por un bus pub/sub en memoria (interfaz abstracta
lista para Redis cuando haya varios procesos, §8.3).

**Autenticación obligatoria en el primer frame**: el cliente envía
`{"type": "auth", "token": "<jwt>"}` en los primeros 10 s
(`TPV_WS_AUTH_TIMEOUT_SECONDS`); sin token válido o con la sesión revocada el
servidor cierra con **4401**, y un primer frame que no sea `auth` cierra con
**4400**. Después:

| Cliente → | Servidor → |
|---|---|
| `{"type": "subscribe", "topics": [...], "since_id": N?}` | `{"type": "subscribed", "topics": [...concedidos...], "denied": [...], "replayed": N, "cursor": M}` (el replay, si lo hay, llega ANTES como frames `event`) |
| `{"type": "unsubscribe", "topics": [...]}` | `{"type": "unsubscribed", "topics": [...]}` |
| `{"type": "pong"}` (respuesta al ping; opcional) | `{"type": "event", "event": {id, event_id, topic, type, payload, actor_user_id, occurred_at}}` |
| `{"type": "ping"}` (opcional) | `{"type": "ping"}` (latido cada `TPV_WS_HEARTBEAT_SECONDS`, 15 s) |
| cualquier otra cosa | `{"type": "error", "code": "WS_UNKNOWN_FRAME"}` (la conexión sigue) |

Temas y permiso exigido (un tema sin permiso **no corta la conexión**: aparece
en `subscribed.denied`): `sales` → `sales.sell` · `catalog` → `products.view` ·
`cash` → `cash.open` · `kds` → `sales.sell` · `system` → cualquier
autenticado · `terminal:{uuid}` → `cash.open` (eventos del TPV afectado) ·
`agent:{uuid}` reservado al tpv-agent (fase 13). Los eventos que produce hoy el
backend: `sales.created/updated/closed/voided/refunded`,
`cash.opened/movement_created/count_recorded/session_closed`,
`catalog.changed` (entidad/acción/id, para invalidar caché), y
`printing.printer_down` / `system.printer_down` cuando un job de impresión
agota sus reintentos.

**Reconexión y replay**: cada evento lleva `id` (bigserial de `event_log`,
monótono). El cliente recuerda el último `id` recibido y, al reconectar (con
backoff), se suscribe con `since_id=<último id>`: el servidor le repasa lo
perdido (hasta `TPV_WS_REPLAY_LIMIT`, 500) y sigue en vivo sin hueco.
Deduplicar por `event_id` en el cliente (un evento puede llegar dos veces si
coinciden replay y vivo). El latido del servidor mantiene el socket atravesando
proxies; si el cliente no responde, el cierre lo limpia. No se envían datos
innecesarios: el evento es un aviso mínimo — la lectura completa es la API
REST normal.

## Idempotencia (fase 14, cabecera `Idempotency-Key`)

La red de un TPV se corta y el cliente reintenta: para que eso nunca duplique
una venta ni un movimiento, las escrituras de dinero aceptan (o exigen) la
cabecera `Idempotency-Key` (máx. 200 caracteres). El servidor (`app/services/
idempotency.py`) calcula la huella SHA-256 de `método + ruta + cuerpo` y guarda
clave + huella + **respuesta congelada** en la MISMA transacción que el efecto:
si la petición no llega a confirmarse, la clave tampoco existe. Con la misma
clave y la misma huella, el reintento recibe la respuesta congelada byte a byte
durante 24 h sin ejecutar nada; con la misma clave y contenido o endpoint
distinto → 409 `IDEMPOTENCY_KEY_REUSED`. La comprobación va ANTES de la
validación de negocio: un replay gana incluso sobre un 409 `SALE_ALREADY_PAID`
(devuelve lo que ya ocurrió, no un error). Si dos peticiones concurrentes
llegan con la misma clave, la segunda choca con la clave única, reintenta el
lookup y sirve replay o 409 — nunca ejecuta dos veces. Una clave vencida se
libera (se borra y la operación se ejecuta nueva).

| Operación | Clave | Sin clave |
|---|---|---|
| `POST /sales/orders` (abrir borrador) | opcional | se ejecuta normal |
| `POST /sales/orders/{id}/lines` (añadir línea) | opcional | se ejecuta normal |
| `POST /sales/orders/{id}/close` (**cobro**) | **obligatoria** | 422 `IDEMPOTENCY_KEY_REQUIRED` |
| `POST /cash/sessions` (apertura de caja) | opcional | se ejecuta normal |
| `POST /cash/sessions/{id}/movements` | opcional | se ejecuta normal |

Los clientes: el móvil envía una clave por operación en cola (reintento seguro
tras un corte) y el cobro móvil reutiliza la clave SOLO con el cuerpo idéntico
al intento anterior (un cambio de importe/pagos es un intento NUEVO con clave
nueva — cobrar distinto nunca se come el 409 de una venta ya pagada).

## Informes (fase 15, `/api/v1/reports`)

Consultas de SOLO LECTURA, todas bajo el permiso `reports.view` y sin auditoría
ni eventos (un informe no muta estado, igual que los informes X/Z de caja):

| Ruta | Qué da |
|---|---|
| `GET /reports/tickets` | tickets emitidos (join con su pedido: total, usuario, reimpresiones) |
| `GET /reports/invoices` | facturas por fecha de emisión (+ filtros `status`/`series`; sin `payload` en el listado) |
| `GET /reports/invoices/{id}` | factura detallada: el mismo render que `/documents`, aquí bajo `reports.view` |
| `GET /reports/cash-closures` | cierres Z pasados (sesiones cerradas con el cuadre congelado) |
| `GET /reports/stats/summary` | ventas, devoluciones y anuladas, venta neta, ticket medio y desglose de IVA |
| `GET /reports/stats/by-product` | ventas por producto |
| `GET /reports/stats/by-category` | ventas por categoría actual (`(sin categoría)` con coalesce) |
| `GET /reports/stats/by-waiter` | ventas por camarero |
| `GET /reports/stats/by-payment-method` | ventas por forma de pago (devoluciones POSITIVAS, convención del informe Z) |
| `GET /reports/stats/by-period` | series temporales (`interval` = hour/day/week/month vía `date_trunc`) |

Reglas anti-tabla-completa (§13):

- `from`/`to` OBLIGATORIOS en todas las consultas y acotados a 366 días
  (`app/domain/reports.py::bounded_range` → 422 si el rango queda invertido o
  se excede el techo).
- Agregaciones en SQL (`app/repos/reports.py`): un `case` por el signo del
  importe separa ventas de devoluciones; las estadísticas solo cuentan pedidos
  `paid` en la ventana de `paid_at` (los `voided` van aparte por `voided_at`);
  el IVA agrupa `order_lines` por `tax_rate`; el `doc_number` sale con
  `jsonb_extract_path_text`, nunca el `payload` completo en listados.
- Paginación `{items, total, limit, offset}` con `limit ≤ 200` (defecto 50);
  `total` cuenta filas o grupos, nunca órdenes crudas.
- El dinero sale como string con 2 decimales (§3); las métricas derivadas
  (venta neta, ticket medio) se calculan en céntimos enteros con el redondeo
  half-up del motor de ventas (`app/domain/reports.py` reutiliza las ayudas de
  `app/domain/sales.py`).

## Plugin fiscal (fase 34, `/api/v1/fiscal` y `app/adapters/fiscal.py`)

Fiscalidad como PLUGIN desacoplado (ADR-010): el motor de ventas NO la conoce —
sigue emitiendo `sale_events` genéricos (fase 06) y el plugin los consume. Elegir
régimen es cambiar UNA variable (`TPV_FISCAL_PROVIDER`); un valor erróneo falla
en el arranque (`build_fiscal_adapter`), no en la primera descarga.

| Ruta | Permiso | Qué hace |
|---|---|---|
| `GET /fiscal/status` | `fiscal.view` | proveedor activo, cola del outbox, documentos por estado |
| `GET /fiscal/documents` | `fiscal.view` | listado paginado (`?status=`) de documentos fiscales |
| `GET /fiscal/documents/{id}` | `fiscal.view` | documento con su traza completa (`fiscal_events`) |
| `POST /fiscal/dispatch` | `fiscal.dispatch` | una pasada: consumir outbox + enviar pendientes |

- **Proveedores**: `none` (defecto — consume eventos SIN crear documentos), y
  `verifactu` / `ticketbai` como ESQUELETOS seleccionables y auditables que
  rechazan con `FISCAL_NOT_IMPLEMENTED` (decisión 2026-09-14: sin desarrollo
  legal; ver `docs/fiscal/README.md` y `docs/decisions/ADR-010-*`).
- **Seguridad de repetición**: el consumo vive en el outbox
  (`sale_events.dispatched_at` + `FOR UPDATE SKIP LOCKED`) y la transformación
  es idempotente (`UNIQUE` en `fiscal_documents.sale_event_id`). Ejecutar el
  dispatch tarde o dos veces nunca pierde ni duplica: apto para un cron.
- **Auditoría total**: cada operación deja fila append-only en `fiscal_events`
  (queued/dispatched/accepted/rejected) y cada descarga un resumen en
  `audit_log` (`action = 'fiscal.dispatch'`). Sin `updated_at` en documentos:
  el histórico ES `fiscal_events`.
- **Máquina de estados**: `pending → sent → accepted | rejected`,
  `rejected → pending | cancelled`; `accepted` y `cancelled` terminales. Un
  fallo del adaptador deja el documento `pending` con `attempts+1` y el código
  de error — el fiscal jamás revierte una venta ya cobrada.

## Variables de entorno (prefijo `TPV_`)

| Variable | Defecto | Uso |
|---|---|---|
| `TPV_DATABASE_URL` | *(vacía)* | URL async `postgresql+psycopg://…`; vacía = sin BD (`/readyz` → 503) |
| `TPV_ENV` | `dev` | Entorno lógico (dev/test/prod) |
| `TPV_LOG_LEVEL` | `INFO` | Nivel de logging (JSON en stdout) |
| `TPV_CORS_ORIGINS` | *(vacía)* | Orígenes permitidos, separados por comas (solo los servidos por el servidor) |
| `TPV_DB_POOL_SIZE` / `TPV_DB_MAX_OVERFLOW` | `5` / `10` | Pool de conexiones |
| `TPV_DB_NULL_POOL` | `false` | Desactivar el pool (tests con loops efímeros, scripts) |
| `TPV_JWT_SECRET` | *(vacía)* | Secreto HMAC de los access tokens (mín. 32 caracteres; ADR-008) |
| `TPV_ACCESS_TOKEN_MINUTES` | `15` | Vida del JWT de acceso |
| `TPV_REFRESH_TOKEN_HOURS` | `12` | Vida del refresh rotativo (cookie HttpOnly) |
| `TPV_AUTH_RATE_LIMIT_ATTEMPTS` | `10` | Intentos por IP y ruta en login/pin |
| `TPV_AUTH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Ventana del limitador |
| `TPV_DATA_DIR` | `data` | Volumen de ficheros del servidor (logos de cabecera, fase 09); nunca en la BD |
| `TPV_WS_HEARTBEAT_SECONDS` | `15` | Latido ping del WebSocket |
| `TPV_WS_AUTH_TIMEOUT_SECONDS` | `10` | Plazo para el frame `auth` tras conectar |
| `TPV_WS_REPLAY_LIMIT` | `500` | Máximo de eventos reenviados por `since_id` |
| `TPV_FISCAL_PROVIDER` | `none` | Régimen del plugin fiscal: `none` / `verifactu` / `ticketbai` |

Para Alembic se usa la misma `TPV_DATABASE_URL` (`alembic/env.py`).

## Tests

```bash
pip install pytest httpx
pytest                                   # API + seguridad + esquemas + dominio: no necesita BD
set TPV_TEST_DATABASE_URL=postgresql+psycopg://…   # opcional: añade tests contra PG real
pytest                                   # incluye test_integrity.py, test_auth_api.py,
                                         # test_catalog_api.py, test_panels_api.py,
                                         # test_sales_api.py, test_payments_api.py,
                                         # test_cash_api.py, test_documents_api.py,
                                         # test_printing_api.py, test_hardware_api.py,
                                         # test_ws_api.py, test_reports_api.py,
                                         # test_fiscal_api.py y readyz con BD
```

Estado actual (fase 15): **144 passed, 133 skipped** — los saltos son los tests de BD
(incluidos los 7 del motor de ventas, los 13 de pagos, los 7 de caja, los 14 de
documentos, los 9 de impresión, los 3 de hardware, los 9 del hub WebSocket, los 8 de
idempotencia y los 12 de informes), que se activan solos al definir `TPV_TEST_DATABASE_URL`. Los tests de dominio sin BD validan el cálculo contra
oráculos independientes: `test_sales_domain.py` (3.520 combinaciones de precio ×
cantidad × descuento × IVA, redondeo half-up, simetría de devolución y aditividad
de tramos), `test_payments_domain.py` (pago exacto, cambio, mixto, parcial
insuficiente, exceso sin efectivo, tendered inválido y casos límite),
`test_cash_domain.py` (contado por denominaciones, efectivo esperado y
diferencia), `test_documents_domain.py` (numeración, fusión e inversión de
desgloses de IVA, cabecera y payloads congelados), `test_printing_domain.py`
(ancho térmico y dimensionado del logo, backoff de reintentos, data URI y
dimensiones PNG/JPEG, máquina de estados de la cola) y `test_reports_domain.py`
(acotación de rangos a 366 días, venta neta y ticket medio half-up).

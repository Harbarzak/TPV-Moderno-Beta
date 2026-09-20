# TPV Moderno — Documento de Arquitectura

| Campo | Valor |
|---|---|
| Fase | 00 · Arquitectura (Arquitecto principal) |
| Fecha | 2026-09-11 |
| Estado | Definitivo para esta fase; evoluciona por ADRs en `docs/decisions/` |
| Referencia funcional | TPV legado "Visual TPV" (Windows, SQL Server/Express, DBF) |
| Referencia de producto/UX | URY (github.com/ury-erp/ury) — **solo acelerador de UX/producto, nunca código** (AGPL, ver ADR-007) |
| Alcance fiscal | **Plugin construido, sin régimen activo** (`TPV_FISCAL_PROVIDER=none`, ADR-010); los adaptadores concretos (Veri*Factu, TicketBAI) siguen sin desarrollar |

---

## 0. Resumen ejecutivo

Plataforma TPV cliente-servidor para operar **en LAN sin depender de Internet**. Un único servidor
(Python/FastAPI + PostgreSQL) concentra toda la lógica y el estado; los clientes (TPV de venta,
PWA móvil de camarero, KDS y panel de administración) son interfaces React que **nunca acceden a la
base de datos directamente**, solo vía API HTTPS/WebSocket. Internet se reserva para integraciones
externas, actualizaciones y backups remotos. La fiscalidad es un **plugin desacoplado** (fase 34,
ADR-010) que consume los `sale_events` del motor sin tocarlo; los adaptadores concretos
(Veri*Factu, TicketBAI) quedan aplazados como esqueletos seleccionables, y CashDro sigue
solo-interfaz.

---

## 1. Arquitectura completa

### 1.1 Topología

```
                            ┌──────────────────────────── LAN del local ────────────────────────────┐
                            │                                                                        │
 ┌──────────────┐   HTTPS   │  ┌─────────────────────────── SERVIDOR TPV ───────────────────────┐    │
 │ TPV Venta #1 │◄─────────►│  │                                                                │    │
 │ (React,      │   WSS     │  │  FastAPI (uvicorn)                                             │    │
 │  navegador)  │◄─────────►│  │   ├─ API REST /api/v1 (auth, catálogo, ventas, caja, …)        │    │
 └──────────────┘           │  │   ├─ WebSocket Hub /ws (eventos + colas de impresión)          │    │
                            │  │   ├─ Motores de dominio (ventas, pagos, caja, impresión)       │    │
 ┌──────────────┐           │  │   ├─ Workers (cierres programados, informes, backups, colas)   │    │
 │ TPV Venta #N │◄──────┐   │  │   └─ Plugin fiscal (FiscalAdapter, outbox — ADR-010)           │    │
 └──────────────┘       │   │  │                                                                │    │
                        │   │  │  PostgreSQL 16  (acceso EXCLUSIVO del servidor)                │    │
 ┌──────────────┐       │   │  │  Volúmenes: logs, WAL, dumps, ficheros de impresión            │    │
 │ PWA Camarero │◄──────┼──►│  └────────────────────────────────────────────────────────────────┘    │
 └──────────────┘       │   │                    │                                                     │
                        │   │                    ▼                                                     │
 ┌──────────────┐       │   │           ┌──────────────────┐        ┌─────────────────────────┐      │
 │ KDS Cocina   │◄──────┘   │           │ NAS / destino    │        │ Impresoras de red       │      │
 └──────────────┘           │           │ backups (pg_base │        │ (ESC/POS TCP 9100)      │      │
                            │           │ backup + WAL)    │        └──────────▲──────────────┘      │
 ┌──────────────┐           │           └──────────────────┘                   │ server-direct       │
 │ Panel Admin  │◄─────────►│                                                  │                     │
 └──────────────┘           │  ┌───────────────────────┐        ┌──────────────┴──────────┐          │
                            │  │ tpv-agent (terminal)  │───────►│ Periféricos locales:    │          │
                            │  │ impresión USB/serie,  │  USB/  │ impresora ticket, cajón,│          │
                            │  │ cajón, lector tarjetas│  serie │ visor, lector tarjetas  │          │
                            │  └───────────▲───────────┘        └─────────────────────────┘          │
                            │              │ WSS (subtópico de agente)                               │
                            └──────────────┼─────────────────────────────────────────────────────────┘
                                           │
                     Internet (opcional): actualizaciones, backups remotos, integraciones externas
```

### 1.2 Principios arquitectónicos

1. **Un servidor, muchos clientes ligeros.** Todo el estado y la lógica de negocio viven en el
   servidor; los clientes son vistas + cachés de presentación.
2. **La base de datos no se expone.** Ni siquiera en LAN: solo el proceso del servidor abre
   conexiones a PostgreSQL (ADR-002).
3. **LAN-first.** Cero llamadas a Internet en el camino crítico de venta. Internet es optativo y
   solo para: actualizaciones, backups remotos cifrados e integraciones externas.
4. **Eventos como tejido conectivo.** Cualquier acción relevante (venta cerrada, movimiento de caja,
   pedido de cocina) produce un evento; REST responde y el WebSocket difunde.
5. **Idempotencia y trazabilidad.** Operaciones de dinero llevan clave de idempotencia; toda
   operación sensible queda en audit log.
6. **Puntos de extensión, no integraciones.** El fiscal ya es plugin real pero desacoplado
   (consume el outbox `sale_events`, ADR-010); los adaptadores de hardware siguen como
   interfaces cuya implementación real llega en fases posteriores.
7. **Tecnología por convención.** Se evita la pluralidad de frameworks: un stack decidido y maduro.

### 1.3 Decisiones tecnológicas (definitivas, no negociables en esta fase)

| Capa | Elección | Justificación breve |
|---|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2.x (async) + Alembic | Tipado, rendimiento adecuado a LAN, ecosistema, migraciones versionadas |
| BD | PostgreSQL 16 | Transaccionalidad estricta (dinero), secuencias legales, WAL para PITR |
| Frontend TPV | React 18 + TypeScript + Vite + Tailwind + Radix UI + Lucide + Zustand + Zod | UI rápida, accesible y tipada; estado local simple en cliente |
| Móvil | PWA (mismo stack) sobre la misma API | Sin tiendas de apps; instalación desde el servidor LAN |
| KDS | Cliente React independiente | Ciclo de vida y despliegue separados del TPV principal |
| Gráficos | Three.js **prohibido** en pantalla de venta; solo dashboard/estadísticas si aporta valor real | La pantalla de venta exige latencia y fiabilidad, no efectos |
| Servidor físico | Mini-PC/headless en el local, SSD, UPS | Hardware barato y reemplazable; el estado vive en la BD |

### 1.4 Despliegue

- Servidor como servicios del sistema operativo (Windows Service o systemd según host), con
  reinicio automático.
- Un único proceso uvicorn (varios workers solo si el hardware lo permite; el hub WebSocket asume
  proceso único — ver §8).
- HTTPS en LAN: certificado emitido por una CA local del instalador (los clientes confían la CA en
  el alta del terminal); WSS para WebSockets.
- Versiones e imágenes del frontend servidas por el propio servidor (`/app/tpv`, `/app/movil`,
  `/app/admin`, `/app/kds`), de modo que "actualizar" = actualizar el servidor.

---

## 2. Componentes del sistema

| # | Componente | Responsabilidad |
|---|---|---|
| 1 | **API REST (FastAPI)** | Endpoints `/api/v1/*`: autenticación, catálogo, ventas, pagos, caja, tickets, informes, administración, dispositivos |
| 2 | **Motor de ventas** | Caso de uso central: alta/modificación de tickets, líneas, descuentos, impuestos, cierre y anulaciones. Transaccional |
| 3 | **Motor de pagos** | Cobros parciales/múltiples, validación contra importe pendiente, adaptadores de TPV físico (interfaz, sin implementación ahora) |
| 4 | **Motor de caja** | Sesiones de caja, movimientos, arqueos, cierres X/Z e informe de cierre de día |
| 5 | **Motor de tickets/facturas** | Series de numeración, formato fiscal básico, render para impresión |
| 6 | **Servicio de impresión** | Cola de `PrintJob`, render ESC/POS/PDF, reintento y estados |
| 7 | **Hub WebSocket** | Autenticación de conexión, salas por terminal/rol/tema, difusión de eventos con replay |
| 8 | **`FiscalAdapter`** | Plugin fiscal desacoplado (fase 34, ADR-010): consume el outbox `sale_events` (idempotente, `UNIQUE` por evento), transforma en `FiscalDocument` y certifica vía adaptador seleccionable (`TPV_FISCAL_PROVIDER`: none/verifactu/ticketbai) con traza `FiscalEvent` + auditoría `fiscal.dispatch`. `none` consume sin certificar; verifactu/ticketbai son esqueletos (`FISCAL_NOT_IMPLEMENTED`) — sin desarrollo legal (ADR-003 en su principio) |
| 9 | **Adaptadores de hardware** | `PrinterAdapter`, `CashDrawerAdapter`, `BarcodeScannerAdapter`, `PaymentTerminalAdapter`, `CashDroAdapter` (CashDro solo interfaz), `CustomerDisplayAdapter` — interfaces definidas en fase 11 (sustitutos Null/Simulated); drivers reales en fases 13–14 |
| 10 | **tpv-agent** | Demonio ligero en cada terminal con periféricos locales (USB/serie): imprime, abre cajón, habla con el lector de tarjetas; se conecta al servidor por WSS |
| 11 | **Workers** | Cierres programados, generación de informes, backups, purgas, reenvío de colas pendientes |
| 12 | **Módulo de administración** | Departamentos, formas de pago, usuarios/camareros, parámetros, terminales, impresoras |
| 13 | **Capa de informes** | Listado X, Listado Z, Cuadre de caja, Tickets emitidos, Facturas detallado, Cierre pasado, Estadísticas |
| 14 | **AuthN/AuthZ** | JWT (access+refresh), PIN por usuario para cambio rápido en terminal, RBAC granular |
| 15 | **Audit log** | Registro inmutable de acciones sensibles |
| 16 | **Observabilidad** | Logs estructurados, métricas Prometheus, healthchecks |

Capas internas del backend (repositorio único `backend/`):

```
api/        routers FastAPI (validación Pydantic, HTTP puro)
services/   motores de dominio (transacciones, reglas)
domain/     entidades y lógica pura (totales, impuestos, estados)
repos/      acceso a datos SQLAlchemy 2.x
adapters/   FiscalAdapter, PrinterAdapter, PaymentTerminalAdapter, …
ws/         hub, salas, serialización de eventos
workers/    tareas programadas
core/       configuración, seguridad, logging
```

Regla de dependencias: `api → services → domain`; `repos` y `adapters` son detalles inyectados.
Los servicios nunca importan de `api`.

---

## 3. Modelo de datos (alto nivel)

> Detalle de tablas, índices y migraciones: **fase Base de datos**. Aquí solo dominios y relaciones.

### Dominios y entidades principales

**Catálogo (Productos)**
- `Department` (departamento, del legado): agrupación contable/estadística de nivel 1.
- `Category` (categoría): agrupación de venta; opcionalmente hija de departamento.
- `Product`: datos de venta (nombre, PVP, tipo IVA, ordenación,flags: pesable, preparable en cocina, bloqueado).
- `Panel` / `SubPanel`: rejillas de botones del TPV visual; referencian productos; ordenables por arrastrar.
- `TaxRate`: tipos de IVA vigentes con validez temporal (para históricos inmutables de tickets).

**Personal y seguridad**
- `User` (camareros y personal): credencial de acceso + PIN de terminal, rol.
- `Role` / `Permission`: RBAC granular (cobrar, anular, descuento, abrir cajón, cierres, administración…).
- `Session` (JWT refresh) y `Terminal` (TPVs registrados con su configuración de impresión).

**Ventas**
- `Order` (ticket): terminal, camarero, sesión de caja, estado (abierto, cobrado, anulado, facturado), totales, serie+número.
- `OrderLine`: producto snapshot (nombre, precio e IVA **copiados al vender**), cantidad, descuentos, notas.
- `Invoice`: factura asociada a uno o más tickets, serie legal por año, datos de cliente.
- `Refund`/`VoidEvent`: anulaciones y devoluciones con referencia al original y autor.

**Pagos**
- `PaymentMethod`: efectivo, tarjeta, mixto, crédito interno… (configurable; heredado de "Formas de Pago").
- `Payment`: importe, método, terminal de cobro, referencia externa (para futuros TPVs físicos).

**Caja**
- `CashSession`: apertura con saldo inicial, usuario y terminal; cierre con arqueo y desajuste calculado.
- `CashMovement`: entradas/salidas manuales con motivo.
- `CashCount` (arqueo): recuento por denominación.
- `DailyClose` (cierre de día Z): snapshot inmutable de totales del día por método de pago, departamento, camarero e IVA.

**Restaurante (modo restaurante, fase 16)**
- `Zone`, `Table`, `Reservation` (mínimo), ocupación de mesa ↔ `Order`.

**KDS (fase 18)**
- `KitchenStation` (estaciones de cocina) y `KitchenTicket` con sus líneas: cada línea de
  venta "preparable" crea una línea de cocina (enlace 1:1); estados (pendiente, en curso,
  listo, servido) con timestamps; el estado de la comanda se DERIVA de sus líneas.

**Sistema**
- `EventLog`: eventos del hub WebSocket con `event_id` monotono para replay (§8).
- `PrintJob`: cola de impresión con estado y reintentos.
- `AuditLog`: quién/cuándo/qué/cuándo-antes-después en acciones sensibles.
- `Parameter`: parámetros globales del sistema (equivalente a "Parámetros" del legado).
- `Sequence`: series de numeración (tickets por terminal, facturas por año) con control de huecos.

### Reglas transversales

- **Inmutabilidad económica:** un ticket cobrado no se muta; las correcciones son eventos
  (anulación/refundo) referenciando al original.
- **Snapshot de precios:** las líneas guardan precio e IVA del momento de la venta; el catálogo
  puede cambiar después sin alterar históricos.
- **Dinero:** `numeric(12,2)` en BD, `Decimal` en Python, **string en JSON**. Prohibido `float`.
- **Tiempos:** `timestamptz` UTC en BD; conversión a zona local solo en presentación.
- **Borrado:** sin borrados físicos en dominios económicos; baja lógica (`active=false`).

---

## 4. Flujo de ventas

### 4.1 Venta estándar (bar/cafetería)

```
 camarero            TPV (React)              servidor FastAPI                     periféricos
    │  login/PIN   ─► │                     │                                     │
    │                 │  abre/reusa sesión  │  POST /cash/sessions (si aplica)    │
    │  selecciona  ─► │  panel/subpanel     │                                     │
    │  productos      │  carrito local      │  (draft solo en cliente; sin        │
    │                 │  (Zustand)          │   escritura en BD hasta cobrar)     │
    │  cobra       ─► │  POST /sales/orders │  valida → calcula IVA →             │
    │                 │  {idempotency_key,  │  asigna serie+número →              │
    │                 │   líneas, pagos}    │  COMMIT atómico + sale_event       │
    │                 │                     │  en el outbox fiscal (ADR-010): lo │
    │                 │                     │  consume POST /fiscal/dispatch     │
    │                 │                     │  ── evento sales.created ──►        │  PrintJob → impresora
    │                 │  ◄─ ticket cerrado  │  ── evento kds.* ──► (si procede)   │  cajón → apertura
```

Puntos clave:
1. El ticket se edita **en el cliente** (rápido, tolerante a reinicios del borrador); solo el cobro
   escribe en BD de forma atómica.
2. El cobro es una única llamada idempotente: si se pierde la respuesta, reintentar con la misma
   `idempotency_key` devuelve el mismo ticket sin duplicar.
3. Numeración por terminal (serie por `Terminal`), sin dependencia de red entre TPVs.
4. Descuentos/anulaciones de línea en ticket abierto quedan auditados por usuario.
5. El commit deja el `sale_event` en el outbox (ADR-010); el plugin fiscal lo consume aparte
   (`POST /fiscal/dispatch`). Si falla la certificación, la venta queda válida y el DOCUMENTO
   fiscal queda reencolable con su traza — el reintento jamás revierte la venta.

### 4.2 Casos derivados

- **Factura:** sobre ticket(s) cobrado(s) → datos fiscales de cliente → `Invoice` con serie anual.
- **Anulación de ticket cobrado:** requiere permiso + motivo + autor; genera evento económico
  inverso, nunca borra.
- **Devolución:** ticket nuevo negativo referenciando al original; reembolso por forma de pago.
- **Modo restaurante (fase 16):** el draft se persiste en servidor ligado a mesa para traspaso entre
  camareros/terminales; el cobro mantiene el mismo flujo.

---

## 5. Flujo de caja

```
APERTURA ──► VENTA ──► [X opcional] ──► CIERRE DÍA (Z)
   │            │            │                │
 saldo     efectivo entra   listado X       arqueo por denominaciones
 inicial   (pagos) y        (consulta,      → cuadre (diferencia)
 + mov.    movimientos      no cierra)      → DailyClose inmutable
 manuales                                   → sesión cerrada
```

1. **Apertura:** `POST /cash/sessions` con saldo inicial (contador físico), usuario y terminal. Solo
   una sesión abierta por terminal (configurable por usuario).
2. **Impacto automático:** cada pago en efectivo decrementa/aumenta el esperado de la sesión;
   otros métodos solo acumulan totales.
3. **Movimientos manuales:** entradas/salidas con motivo y permiso (auditoría obligatoria).
4. **Listado X:** totales acumulados de la sesión sin cerrarla (consultable N veces).
5. **Cierre/arqueo:** recuento físico por denominación → el sistema calcula **desajuste** (diferencia)
   → firma del cierre → `CashSession` cerrada e `DailyClose` creado como snapshot inmutable.
6. **Cierre de día:** el worker o el administrador consolida el día: totales por forma de pago,
   departamento, camarero, IVA y tickets; equivalente funcional al "Cierre Día" del legado, y base
   de los listados (X, Z, Cuadre, Tickets emitidos, Facturas detallado, Cierre pasado, Estadísticas).
7. **Sesión cruzada de medianoche:** si la jornada cruza las 00:00 (configurable), el "día fiscal"
   sigue la jornada, no la fecha calendario.

---

## 6. Seguridad

| Área | Decisión |
|---|---|
| Identidad | Usuario+contraseña para entrar; **PIN corto por usuario** para operaciones rápidas en terminal (cambio de camarero, autorizaciones) |
| Tokens | JWT access (15 min) + refresh (rotativo, revocable) en cookies `HttpOnly` de LAN; WSS autentica con token en primer mensaje |
| Autorización | RBAC granular (permiso por acción sensible: cobrar, anular, descuento %, abrir cajón, mov. caja, cierres, administración). "Camarero" no ve administración |
| Transporte | HTTPS/WSS obligatorios en LAN con CA local; HSTS cuando aplique; sin HTTP plano |
| Base de datos | Usuario de BD exclusivo del servidor, privilegios mínimos; PostgreSQL sin escucha pública y firewall de host; **ningún cliente conoce credenciales de BD** |
| Secretos | Todo en variables de entorno / secret local cifrado (`.env` fuera del repo, plantilla `.env.example`); **credenciales nuevas y rotadas**; prohibido copiar nada de `VisTPV.ini` del legado |
| Idempotencia | Claves de idempotencia en cobros/pagos para evitar duplicados por reintentos |
| Validación | Pydantic en entrada, Zod en cliente; rechazo por defecto (`extra=forbid`) |
| CORS | Solo orígenes servidos por el propio servidor |
| Rate limiting | Por IP/ruta en rutas de auth y endpoints caros |
| Auditoría | `AuditLog` inmutable con usuario, IP, acción, payload antes/después para: anulaciones, descuentos, movimientos de caja, cierres, cambios de parámetros y usuarios |
| Dispositivos | Cada terminal se alta con token de dispositivo; el tpv-agent se autentica como terminal, no como usuario |
| Hardening servidor | Actualizaciones de SO programadas, backups cifrados, sin escritorio remoto expuesto salvo VPN/Tailscale (opcional soporte remoto) |

---

## 7. Diseño de API

### 7.1 Convenciones

- Prefijo `/api/v1`. JSON UTF-8. Fechas ISO-8601 UTC. **Dinero como string decimal** (`"12.34"`).
- Errores: RFC 9457 (`application/problem+json`) con `code` estable (`SALE_ALREADY_PAID`,
  `PERMISSION_DENIED`, `VALIDATION_ERROR`…).
- Paginación cursor-based (`?limit=&cursor=`) en listados grandes; offsets solo en informes.
- Idempotencia: cabecera `Idempotency-Key` en POST de ventas, pagos y movimientos de caja
  (deduplicación en servidor durante 24 h).
- Versionado por URL; compatibilidad hacia atrás dentro de `v1`.

### 7.2 Recursos principales

```
POST   /api/v1/auth/login                  → JWT (usuario+contraseña)
POST   /api/v1/auth/pin                    → JWT de operación (PIN de camarero)
POST   /api/v1/auth/refresh | /logout

GET    /api/v1/catalog/panels              → paneles+subpaneles+productos (caché de terminal)
CRUD   /api/v1/catalog/departments | categories | products
CRUD   /api/v1/catalog/panels/{id}/items   → diseño de rejillas

GET    /api/v1/staff/users | roles
POST   /api/v1/terminals                   → alta de terminal (token de dispositivo)

POST   /api/v1/sales/orders                → crear/cobrar ticket (idempotente)
POST   /api/v1/sales/orders/{id}/lines     → (solo drafts de restaurante)
POST   /api/v1/sales/orders/{id}/void      → anulación con motivo
POST   /api/v1/sales/orders/{id}/refund    → devolución
POST   /api/v1/documents/invoices          → factura desde ventas cobradas (+/{id}/void · /{id}/rectifications)

POST   /api/v1/cash/sessions               → apertura
POST   /api/v1/cash/sessions/{id}/movements|count|close
GET    /api/v1/cash/sessions/current       → sesión activa del terminal
GET    /api/v1/cash/reports/x | z | cuadre

GET    /api/v1/reports/tickets | invoices | invoices/{id} | cash-closures
GET    /api/v1/reports/stats/summary | by-product | by-category | by-waiter
                                     | by-payment-method | by-period
       (solo lectura bajo reports.view; from/to obligatorios ≤366 días, páginas ≤200)

CRUD   /api/v1/admin/parameters | payment-methods | printers
GET    /api/v1/healthz | /readyz | /metrics (esta última solo LAN de gestión)

WS     /api/v1/ws                          → hub (ver §8)
```

### 7.3 Ejemplo de contrato (cobro)

```jsonc
POST /api/v1/sales/orders
{
  "idempotency_key": "3f9c…",
  "terminal": "TPV-1",
  "waiter_id": 7,
  "lines": [
    { "product_id": 42, "qty": "2", "notes": "sin hielo", "discount_pct": "0.00" }
  ],
  "payments": [
    { "method": "CARD", "amount": "8.47" }
  ]
}
→ 201 { "order_id": 1042, "number": "TPV1-000123", "total": "8.47",
        "tax_breakdown": [{"rate": "10.00", "base": "7.70", "amount": "0.77"}] }
```

---

## 8. Estrategia WebSocket

### 8.1 Modelo

- Endpoint único `wss://server/api/v1/ws` sobre el mismo FastAPI. Autenticación en el primer
  mensaje (token JWT o token de dispositivo/tpv-agent).
- **Temas (topics):** el cliente se suscribe explícitamente:
  - `terminal:{id}` — eventos dirigidos a un TPV (impresión, turnos).
  - `sales` — nuevas ventas/cobros (para KDS-lite, estadísticas vivas).
  - `catalog` — cambios de catálogo/paneles/tarifas/IVA (aviso mínimo; los terminales
    invalidan su caché y re-hidratan por REST, fase 12).
  - `kds` — comandas (estados de cocina).
  - `cash` — sesiones y movimientos (supervisión).
  - `agent:{terminal}` — canal de instrucciones hardware del tpv-agent.
  - `system` — avisos (parámetros cambiados, cierre forzado, actualización disponible).
- **Payload de evento:** `{ event_id, type, occurred_at, actor, data }` con `event_id` monotónico
  persistido en `EventLog`.

### 8.2 Fiabilidad

- **Replay:** al reconectar, el cliente envía `since_event_id` y el servidor reenvía los eventos
  perdidos de sus temas (límite de ventana configurable; los clientes además re-sincronizan por
  REST los agregados críticos).
- **Latido:** ping/pong cada 15 s; conexiones muertas se recogen en < 45 s.
- **Reconexión:** backoff exponencial con jitter en clientes; mientras no hay WS, el TPV sigue
  operando por REST (el WS acelera, nunca bloquea).
- **Idempotencia en consumer:** los agentes marcan `PrintJob` como procesados; los duplicados de
  replay se descartan por `job_id`.

### 8.3 Escalado

Proceso único con pub/sub en memoria es suficiente para un local (decenas de conexiones). La
interfaz de publicación queda abstraída (`EventBus`), de modo que un futuro paso a Redis solo
afecta a la implementación del bus, no a los servicios.

---

## 9. Integración de hardware

> Principio: **el navegador no toca el hardware**. Dos rutas según el periférico.

### 9.1 Impresoras de red (ESC/POS, puerto 9100)

- El servidor imprime directamente por TCP — no requiere agente. `PrintJob` con estado
  (`queued → sent → printed | failed`) y reintentos. Detección de cola atascada → evento `system`.

### 9.2 Periféricos locales → **tpv-agent**

Impresoras USB/serie, cajón portamonedas, visor de cliente y **lector de tarjetas** usan el
**tpv-agent**: demonio ligero (Python) instalado en el terminal TPV.

- Se autentica como **terminal** (token de dispositivo) y se suscribe a `agent:{terminal}`.
- Recibe comandos (`print`, `open_drawer`, `card_payment {amount, method_ref}`) y responde con
  estados de ejecución.
- Expone además `http://localhost:9770` (solo loopback) para que el propio navegador del terminal
  dispare acciones sin red (útil con WS caído).
- El lector de tarjetas se integra tras `PaymentTerminalAdapter` (driver por modelo en fase 14);
  el flujo es siempre: servidor autoriza lógicamente → agente ejecuta en el pinpad → resultado
  conciliado con el `Payment`.

### 9.3 CashDro

- **Contexto histórico del legado; no se reconstruye por ahora** (igual que la fiscalidad).
- Se reserva la interfaz `CashDroAdapter` (reciclar/change giver; definida en fase 11, con
  sustituto simulado) en el dominio de pagos; su implementación llegará como plugin/fase posterior.

### 9.4 Matriz de fallbacks de impresión

| Situación | Comportamiento |
|---|---|
| Impresora de red caída | Reintento con backoff; si `N` fallos → evento `system.printer_down` y fallback a impresora secundaria configurada |
| tpv-agent caído | El terminal muestra aviso; impresión de red sigue; recupero de cola al reconectar (`PrintJob` persistente) |
| Sin impresoras | Reimpresión desde "Tickets emitidos" (estado en BD, siempre re-printable) |

---

## 10. Estrategia de backups

| Tipo | Mecanismo | Frecuencia | Retención |
|---|---|---|---|
| PITR local | WAL archiving a NAS/volumen segundo | continuo (≤ 5 min de pérdida objetivo) | 7 días |
| Dump lógico | `pg_dump` comprimido y cifrado (age/gpg) | diario | 7 diarios · 4 semanales · 12 mensuales |
| Remoto cifrado | subida del dump diario (rclone/S3/B2) — solo si hay Internet | diario | 30–90 días |
| Config/secrets | `.env` + CA + configuración de terminales, cifrado aparte | en cada cambio | 12 versiones |

- Restores **probados**: procedimiento documentado y ensayado (objetivo RTO ≤ 30 min); el backup
  sin restauración de prueba no cuenta como backup.
- El worker de backups expone estado en `/healthz` y avisa por `system` si falla.
- Los ficheros de impresión/archivos (logos, PDFs) viven en volumen con backup, no en la BD.

---

## 11. Observabilidad y logs

- **Logs estructurados JSON** (structlog) con `request_id` propagado (REST → servicios → eventos WS);
  rotación por tamaño/día en volumen dedicado.
- **Audit log en BD** (no en fichero) para acciones económicas y de configuración — consulta desde
  administración.
- **Métricas Prometheus** (`/metrics`, restringidas): latencias por endpoint, tasa de ventas/min,
  conexiones WS, profundidad de colas de impresión, fallos de agentes, tamaño/age de backups.
- **Healthchecks:** `/healthz` (proceso vivo) y `/readyz` (BD + colas ok) para el instalador/monitor
  local; el instalador muestra estado del sistema (fase 23).
- **Alertas mínimas:** BD sin WAL backup > X, impresora caída, agente desconectado, disco > 85 %.
  Notificación en panel y (opcional) por correo si hay Internet. Sin dependencia de SaaS externo.
- Trazas distribuidas (OpenTelemetry) **no** en el MVP; la correlación por `request_id` + logs
  estructurados es suficiente a esta escala.

---

## 12. Offline y recuperación ante caídas

### 12.1 Escenarios y respuesta

| Caída | Impacto | Estrategia |
|---|---|---|
| **Internet** | Ninguno en operación | Todo el camino crítico es LAN. Integraciones externas encolan y reintentan |
| **Servidor (proceso)** | Ventas nuevas bloqueadas | auto-restart del servicio; clientes muestran estado y retienen drafts; re-sync al volver (§12.2) |
| **Servidor (hardware)** | Sin ventas digitales | RTO ≤ 30 min con restauración en sustituto (dump+WAL en NAS); alternativa de bolsillo: ticket manual de papel como último recurso (compatibilidad con práctica del legado), regularización al volver |
| **Un TPV terminal** | Solo ese punto | Se usa otro terminal; numeración por terminal evita colisiones; drafts locales no perdidos (IndexedDB) |
| **Periférico (impresora/cajón/pinpad)** | Parcial | §9.4 (fallback de impresión); pinpad → cobro manual marcado, conciliación posterior |
| **PostgreSQL (corrupto/crimen)** | Crítico | PITR a momento anterior (RPO ≤ 5 min); runbook de restauración documentado |
| **WS** | Solo pierde "push" | Operación 100 % REST continúa; replay de eventos al reconectar |

### 12.2 Diseño para degradación (base de la fase 19 "Offline")

- **Cliente TPV:** el draft vive en Zustand + persistencia local del navegador
  (localStorage, fase 18); el catálogo se cachea (`stale-while-revalidate`, copia validada
  con Zod). Un TPV sin servidor **no inventa numeración ni cobra**: acumula
  drafts y los concilia al reconectar, o opera en modo degradado explícito según parámetro.
- **Idempotencia global:** cualquier reintento de cobro es seguro (misma key → mismo resultado).
- **Reconciliación:** al volver la conectividad, cola de operaciones pendientes → replay en orden
  → el servidor es la fuente de verdad: una operación rechazada (4xx) se descarta con aviso
  visible, sin resolución automática de conflictos (fase 18).
- **Servidor:** arranque tolerante (si WS falla, REST arranca); colas de impresión y eventos
  persistentes en BD para no perder nada entre reinicios.

### 12.3 Runbooks

Documentos operativos (fase 23/27): "servidor caído", "BD corrupta", "restauración completa en
equipo nuevo", "caída de impresora en servicio". Cada runbook con pasos verificados y tiempos.

---

## 13. Plan de pruebas

| Nivel | Alcance | Herramientas | Gate |
|---|---|---|---|
| Unitarias | Lógica pura: totales, IVA, descuentos, redondeos, arqueos, series/numeración | pytest | ≥ 90 % en `domain/` y `services/` |
| Integración | API contra PostgreSQL real (testcontainers); transacciones, idempotencia, permisos | pytest + httpx | Todos los endpoints; casos de error incluidos |
| E2E | Flujos completos: venta simple, mixta, factura, anulación, apertura/cierre con arqueo | Playwright sobre frontend servido en LAN | Suite por fase de frontend |
| WS | Conexión, suscripciones, replay, reconexión, latido | pytest + cliente WS | Sin pérdida de eventos en replay |
| Carga | N terminales concurrentes vendiendo; fan-out WS | Locust/k6 | P95 < 150 ms en cobro con 10 TPVs activos |
| Caos/offline | Matar servidor a mitad de cobro; cortar red; matar agente con cola de impresión | scripts + manuales | Cero duplicados; cero tickets perdidos; reconciliación correcta |
| Hardware | Matriz real: 2+ modelos impresora ESC/POS, cajón, pinpad, visor | banco físico en fase 14 | Certificado por modelo |
| Migración | Datos legado importados → informes de conciliación (totales por día/camarero contra sistema antiguo) | scripts fase 21 | Diferencias justificadas al 100 % |
| Seguridad | Recorrido OWASP ASVS nivel adecuado, revisión de permisos por rol | fase 24 | Sin hallazgos críticos |
| Regresión visual | Pantallas TPV en resoluciones objetivo (touch 1280×800…) | Playwright snapshots | Diferencias revisadas |

Ritual por fase: definición de hechos (done) + criterios de aceptación en `PROJECT_STATE.md`;
ninguna fase se cierra con suite roja.

---

## 14. Roadmap — 27 fases

> Cada fase cierra con: entregables en repo, suite verde, `PROJECT_STATE.md` y `memory.md`
> actualizados. Dependencias duras en negrita.

| # | Fase | Objetivo | Sale con |
|---|---|---|---|
| 0 | **Arquitectura** ✅ | Este documento | ARCHITECTURE.md, ADRs, estado |
| 1 | **Base de datos** | Esquema PostgreSQL completo + Alembic + seeds mínimos | Migraciones ejecutables desde cero |
| 2 | **Backend** | Esqueleto FastAPI: config, logging, errores RFC 9457, Docker/compose de desarrollo, CI | API "hola mundo" con healthchecks |
| 3 | **Autenticación** | Usuarios, roles, JWT+refresh, PIN, RBAC, alta de terminales | Login operativo end-to-end |
| 4 | **Análisis URY** | Estudio UX del repo URY: flujos, patrones, lo aprovechable (sin copiar código AGPL) | Documento de hallazgos → insumo Design System |
| 5 | **Design System** | Tokens Tailwind, componentes Radix base, iconografía Lucide, guía | Librería interna documentada |
| 6 | **Productos** | CRUD departamentos/categorías/productos, paneles y subpaneles (diseñador de rejillas) | Catálogo gestionable |
| 7 | **TPV visual** | Pantalla de venta: rejilla táctil, carrito, teclado numérico, cambio de camarero | Venta en draft funcional sin cobrar |
| 8 | **Motor de ventas** | Dominio: cobro, series, IVA, snapshot de precios, idempotencia, anulaciones | POST /sales/orders completo con tests |
| 9 | **Pagos** | Formas de pago, cobros parciales/mixtos, `PaymentTerminalAdapter` (interfaz) | Cierre de venta con cualquier combinación |
| 10 | **Caja** | Sesiones, movimientos, arqueo, X/Z, cierre de día | Flujo de caja completo |
| 11 | **Tickets/Facturas** | Modelos de ticket y factura, series legales, reimpresiones | Ticket fiscal-básico correcto |
| 12 | **Impresión** | Servicio de colas, ESC/POS por red, plantillas, estados y reintentos | Impresión real de tickets |
| 13 | **Hardware** | tpv-agent: cajón, impresoras locales, visor; certificación de matriz | Terminal con periféricos operativos |
| 14 | **WebSocket** | Hub, temas, replay, latido; integración de eventos en TPV/KDS | Tiempo real operativo |
| 15 | **Modo restaurante** | Zonas/mesas, drafts en servidor, traspasos, juntar/mesas separadas | Servicio de sala completo |
| 16 | **Móvil camarero** | PWA: carta, comandas por mesa, cobro | Camarero operativo en móvil |
| 17 | **KDS** | Cliente React independiente, estados de comanda | Cocina en tiempo real |
| 18 | **Offline** | Degradación cliente, colas de reconciliación, runbooks | Venta sobrevive a caídas según §12 |
| 19 | **Informes** | Listados X, Z, Cuadre, Tickets emitidos, Facturas detallado, Cierre pasado, Estadísticas (+ dashboard; Three.js solo si aporta) | Paridad con listados del legado |
| 20 | **Migración legado** | ETL desde SQL Server/DBF → conciliación | Datos históricos útiles en el nuevo sistema |
| 21 | **Administración** | Parámetros, formas de pago, usuarios, terminales, impresoras, auditoría visible | Gestión sin tocar BD |
| 22 | **Instalador** | Instalación servidor (SO servicio, CA, BD, frontend) + alta de terminales | Un técnico instala en < 1 h |
| 23 | **Seguridad** | Hardening, revisión OWASP, rotación de secretos, permisos | Informe de seguridad limpio |
| 24 | **Rendimiento** | Perfiles, índices, carga real (§13) | Objetivos de latencia cumplidos |
| 25 | **QA** | Suite E2E completa, caos, UAT en local piloto | Sign-off funcional |
| 26 | **Producción** | Runbooks, monitorización, plan de contingencia, puesta en marcha | Sistema en explotación |

Fuera de alcance explícito de todo el roadmap actual: los adaptadores legales concretos
Veri*Factu y TicketBAI (el plugin fiscal que los aloja ya existe: fase 34, ADR-010) y CashDro
real (solo interfaz `CashDroAdapter`).

---

## 15. Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Dependencia total del servidor local | §12: RTO/RPO, runbooks, UPS, hardware de repuesto, modo degradado |
| Impresión es el punto más frágil en producción | Colas persistentes, fallbacks §9.4, reimpresión siempre posible |
| Migración DBF/SQL Server con datos sucios | Fase 20 con conciliación obligatoria y simulacros previos |
| Alcance fiscal futuro obligue a rediseñar el dominio de ventas | Riesgo desactivado (ADR-010): el plugin consume el outbox `sale_events` y el motor no lo conoce |
| Copy de código URY (AGPL) | ADR-007: solo inspiración UX; revisión de licencia en cada adopción |

---

## Referencias

- ADRs: `docs/decisions/ADR-001 … ADR-010`; contexto y checklist fiscal: `docs/fiscal/README.md`
- Estado del proyecto: `PROJECT_STATE.md` · Memoria de fases: `memory.md`
- Prompt de la fase: `Fases/FASE_00.md` (fuera de este repo)

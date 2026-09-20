# Auditoría de rendimiento — 2026-09-12 (fase Rendimiento)

Método: **medir primero, optimizar solo lo medido**. Limitación honesta del entorno:
no hay PostgreSQL local, así que (1) las latencias se midieron contra el backend real
sin BD (rutas que no tocan datos), (2) la auditoría SQL es **estática** (cruce
índices del schema vs filtros de los repos, y recuento de consultas por operación
leyendo el código), y (3) el `EXPLAIN` real queda pendiente para el despliegue.

## 1 · Mediciones

### API (uvicorn en :8018, curl × 10–30 iteraciones)

| Ruta | Resultado | p50 | p95 |
|---|---|---|---|
| `GET /healthz` (200, sin BD) | 45 B | 1,9 ms | 2,8 ms |
| `GET /readyz` sin BD | 503 **fail-fast** (2,6 ms, no se cuelga) | 2,4 ms | 2,7 ms |
| `GET /auth/me` sin token | 401 | 2,8 ms | 16 ms (arranque) |
| `GET /catalog/products` Bearer basura | 401 **JWT antes de BD** (cero contacto con PG) | 2,8 ms | 83 ms (solo 1ª petición, warmup) |

El outlier de 83 ms es la primera petición tras arrancar (JIT del loop, TLS/ALPN no
aplica en LAN); el estado estacionario es ~3 ms. Sin BD no hay rutas 200 con datos:
las latencias con consultas se medirán en el despliegue.

### Compresión (payload representativo con el esquema real de `PosProduct`, gzip nivel 6)

| Payload | Bruto | gzip | Ratio | CPU |
|---|---|---|---|---|
| Catálogo POS, 250 productos | 93 765 B | 13 039 B | **7,2x (−86,1 %)** | 2,1 ms |
| Catálogo POS, 50 productos | 18 602 B | 3 139 B | 5,9x | 0,5 ms |
| Informe ventas 90 días + top 50 | 15 492 B | 3 889 B | 4,0x | 0,4 ms |

Nivel 9 no mejora el ratio (más CPU): se elige **6**.

### SQL (auditoría estática: 30 índices del schema vs rutas calientes)

Toda lookup caliente está cubierta — **no se añadió ningún índice a ciegas**:

- `idempotency_keys.key` es `PRIMARY KEY`; purga por `ix_idempotency_expiry`.
- `user_sessions.token_hash` tiene `UNIQUE` (lookup del refresh); sesiones por `ix_user_sessions_user`.
- Replay WS por `ix_event_log_topic_id (topic, id)`; cola de impresión por índice parcial de `ix_print_jobs_queue`.
- Orders: `status_created`, `terminal_created`, `user_created`; tickets `created` + único de documento; invoices `issue_date`; audit `time/entity/user_time`.
- Informes acotados por diseño a ≤ 366 días (fase de informes).

**N+1: ninguno encontrado.** Snapshot POS y árbol de paneles en 3 consultas cada uno
(documentado en `services/catalog.py`); `joinedload(User.role)` en autenticación.

**Paginación ya presente** en todos los listados: catálogo admin y órdenes
(`limit/offset`), sesiones de caja, cola de impresión (20/50/100), replay de eventos.
El snapshot POS viaja completo **por diseño**: es la caché offline de la terminal (§7.2).

### WebSocket / concurrencia / memoria

- WS acotado: outbox máx. 1024 mensajes, replay ≤ 500, heartbeat 15 s, timeout de auth 10 s.
- Concurrencia: las suites de pagos/caja/impresión ya ejercitan `FOR UPDATE` y
  `SKIP LOCKED` (cobros idempotentes, cierre de sesión de caja, claim de trabajos).
- Memoria: rate limiter en memoria por IP+ruta (escala LAN, docenas de IPs); engine
  con `pool_pre_ping` (y `NullPool` en tests); tablas sin purga ya documentadas en la
  fase Seguridad (`user_sessions` caducadas, `idempotency_keys` 24 h, crecimiento de
  `event_log`) → **worker de limpieza pendiente, fase propia**.

### Frontend

| Artefacto | Bruto | gzip |
|---|---|---|
| Mostrador JS | 287,10 kB | 86,09 kB |
| Mostrador CSS | 15,97 kB | 3,91 kB |
| Móvil JS | 250,38 kB | 72,74 kB |
| Móvil CSS | 18,08 kB | 3,88 kB |

En LAN, 86 kB gzip < 0,1 s a 10 Mbps. **Renderizado**: todos los consumos de stores
Zustand usan selectores (`state/cart.ts`, `state/catalog.ts`) → al vender solo
re-renderiza `TicketPanel`, nunca la rejilla; `ProductButton` sin memoizar es
correcto (medido por arquitectura, no hace falta memo). **Precarga**: Vite
`modulepreload` + catálogo cacheado en `localStorage` (`tpv-terminal-catalog`) y
borrador de ticket persistido (`tpv-ticket-draft`) → la terminal arranca operativa
sin esperar red.

## 2 · Mejora implementada (la única que la medición justifica)

**`GZipMiddleware` en `app/main.py`** (`minimum_size=1024`, `compresslevel=6`,
entre `RequestContext` y CORS). El catálogo POS baja de ~94 kB a ~13 kB (7,2x) por
~2 ms de CPU; las respuestas diminutas (healthz 45 B, problem+json ~135 B) quedan
sin comprimir porque gzip las engordaría. WS no pasa por el middleware (solo HTTP).

Verificación: 3 tests nuevos (`tests/test_gzip_middleware.py`) — cableado con los
parámetros medidos, respuesta pequeña sin `content-encoding`, respuesta grande
comprimida — y comprobación en vivo contra uvicorn (healthz sin `content-encoding`,
`x-request-id` intacto).

## 3 · Identificado y NO actuado (justificado)

- **`EXPLAIN` real**: requiere PostgreSQL; sin él, añadir índices sería adivinar.
  Recomendado: `pg_stat_statements` + revisión de planes en el despliegue, con
  volumen real de datos.
- **Purga de tablas acumulativas**: worker de limpieza (fase propia, ya documentado).
- **Secreto JWT ausente → 500 con stack trace** por petición (diseño perezoso para
  que `healthz` funcione sin env): validación fail-fast al arranque sería más limpia;
  se propone para la fase de Despliegue (hardening de configuración).
- **Outliers de primera petición** (~83 ms): warmup del proceso, irrelevante en LAN.
- **Bundle del mostrador sin code-splitting**: 86 kB gzip es poco para una LAN;
  dividir el bundle añadiría complejidad sin beneficio medible aquí.

## 4 · Estado de suites tras los cambios

- Backend: **147 passed / 133 skipped** (280 tests, 0 fallos — JUnit XML; 144+3 de gzip).
- Mostrador: **101 passed** (17 ficheros), sin cambios.

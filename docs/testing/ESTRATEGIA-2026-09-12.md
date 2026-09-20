# Estrategia de testing — 2026-09-12 (fase Testing)

Principio rector del proyecto: **una funcionalidad no se considera terminada hasta
que tenga tests adecuados**. Esta fase audita la cobertura área por área, tapa el
hueco real encontrado (muro de autenticación) y deja documentado cómo se prueba
cada cosa y qué queda pendiente.

## 1 · Pirámide y suites

| Nivel | Herramienta | Dónde | Estado |
|---|---|---|---|
| Unit (dominio, sin BD) | pytest | `backend/tests/test_*_domain.py`, `*_unit.py`, `*_schemas.py`, `security`, `events_bus`, `hardware_adapters` | 134 passed |
| Regresión HTTP (sin BD) | pytest | `backend/tests/test_api.py`, `test_gzip_middleware.py`, `test_auth_wall.py` | 103 passed |
| API / integración (con PG) | pytest | `backend/tests/test_*_api.py`, `test_integrity.py`, `test_auth_api.py` | 111 tests, **DB-gated** |
| Frontend mostrador | vitest (jsdom) | `frontend/src/**` — 17 ficheros | 101 passed |
| Frontend móvil (PWA) | vitest | `frontend/mobile/src/**` — 8 ficheros | 44 passed |

**Total: 515 tests — 370 backend (237 passed + 133 DB-gated en skip), 101 mostrador, 44 móvil.**

Cómo ejecutar:

```bash
# Backend (sin BD: los DB-gated se saltan solos)
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q
# Backend completo (requiere PostgreSQL de pruebas)
TPV_TEST_DATABASE_URL=postgresql+asyncpg://... .venv/Scripts/python.exe -m pytest tests/ -q
# Frontend
cd frontend && npx vitest run
cd frontend/mobile && npx vitest run
```

Convención DB-gated: las suites de API e integridad se activan solo cuando existe
`TPV_TEST_DATABASE_URL`; sin ella, cada test se marca `skip` con su motivo (nunca
falso verde). Sin BD, el muro de auth y las respuestas HTTP siguen verificándose
porque la dependencia de auth se resuelve **antes** que la sesión de datos.

## 2 · Mapa de las 13 áreas exigidas → tests concretos

| Área | Tests | Conteo |
|---|---|---|
| **Unit tests** | `sales_domain` (9), `payments_domain` (11), `cash_domain` (6), `documents_domain` (13), `printing_domain` (22), `reports_domain` (12), `catalog_schemas` (10), `idempotency_unit` (22), `security` (11), `events_bus` (9), `hardware_adapters` (9) | 134 |
| **Integration tests** | `*_api` con PG real: catálogo (14), ventas (7), pagos (13), caja (7), documentos (14), impresión (9), paneles (7), informes (12), hardware (3), idempotencia (8) | 94 (DB-gated) |
| **API tests** | Contrato HTTP sin BD: `test_api` (11), `test_gzip_middleware` (3), `test_auth_wall` (90 — nuevo) | 103 passed + 1 DB-gated |
| **Database tests** | `test_integrity` (18): unicidades (ticket, invoice), FK en cascada, tipos money, `idempotency_keys` PK, `token_hash` UNIQUE, replay `topic+id`; migraciones Alembic aplicadas en el PG de pruebas | 18 (DB-gated) |
| **Frontend tests** | Mostrador: `lib/` (money, schemas, barcode, search, reports, catalogCache, connectivity), `state/` (cart, session) — 17 ficheros, 101 tests | 101 |
| **End-to-end** | `test_api` recorre arranque→health→auth→negocio contra la app real; el flujo completo venta→cobro→documento→informe corre en las suites `*_api` con PG | 11 + 94 |
| **Concurrencia** | Cobros idempotentes en carrera (`payments_api`), cierre de caja concurrente (`cash_api`), claim `SKIP LOCKED` de impresión (`printing_api`), `FOR UPDATE` del motor de ventas (`sales_api`) | dentro de los 94 (DB-gated) |
| **Pagos** | `payments_domain` (11: asignación a línea, insuficiente/exceso) + `payments_api` (13) + idempotencia de cobro (22+8) | 54 |
| **Caja** | `cash_domain` (6: arqueo/movimientos) + `cash_api` (7: apertura, movimientos, sesiones) | 13 |
| **Cierres** | Cierre Z de sesión de caja (`cash_api`) y cierre de orden (`sales_api`); aritmética del cierre en `cash_domain` | dentro de los 13+7 |
| **Devoluciones** | Reembolsos y documentos rectificativos (`documents_domain` 13 + `documents_api` 14), efecto en informes (`reports_domain`) | 27+ |
| **Permisos** | **Nuevo: `test_auth_wall` (90)** — toda operación OpenAPI exige 401 sin credenciales; `security` (11: JWT firma/expiración, refresh único, rate limiter); rol a rol en `*_api`; frontend móvil `permissions.test.ts` | 101+ |
| **WebSocket** | `events_bus` (9: pub/sub, outbox 1024, replay ≤ 500) + `ws_api` (9: handshake, auth 10 s, heartbeat 15 s, replay) | 18 |

### El hueco tapado en esta fase

La fase Seguridad verificó el muro **estáticamente** (86/86 rutas con permiso
declarado) pero no existía ninguna prueba automatizada: ningún `assert 401` en la
suite. `tests/test_auth_wall.py` lo convierte en regresión: genera los casos desde
`app.openapi()` (74 paths, 95 operaciones) y llama **cada operación de negocio sin
credenciales** exigiendo `401` + `code` estable. Si mañana se publica una ruta sin
dependencia de auth, la suite falla sin mantenimiento manual del listado.

Exclusiones justificadas (públicas por diseño): `GET /healthz`, `GET /readyz`
(sondas sin secretos), `POST /auth/login`, `POST /auth/pin` (cómo se OBTIENEN las
credenciales) y `POST /auth/logout` (idempotente: sin sesión responde 200).
Particularidad documentada: `POST /auth/refresh` responde `401 TOKEN_INVALID` (lee
cookie de refresco ausente) en vez de `AUTH_REQUIRED` — el test lo fija como
comportamiento del contrato.

## 3 · Qué NO está automatizado (honestidad de cobertura)

1. **Los 133 tests DB-gated no se han ejecutado en este entorno** (sin PostgreSQL
   local): están escritos y en skip con motivo; deben correr en el equipo de
   pruebas con `TPV_TEST_DATABASE_URL` antes de cada release.
2. **E2E de navegador (Playwright)**: vender un ticket tocando la UI real, offline
   incluido. La lógica está testada en stores/lib (jsdom), no el canvas del DOM
   completo. Candidato a fase propia.
3. **Smoke en dispositivo** de la PWA móvil (instalación, cámara de código de
   barras, impresión por red) — requiere hardware físico.
4. **Carga/estrés WS** con muchas terminales simultáneas: medido el acotado
   (outbox, replay) pero no un benchmark de saturación; ligado a la fase de
   Despliegue.

Ninguna funcionalidad entregada hasta la fecha carece de tests: lo pendiente es
**entorno de ejecución** (PG, navegador, dispositivo), no cobertura de diseño.

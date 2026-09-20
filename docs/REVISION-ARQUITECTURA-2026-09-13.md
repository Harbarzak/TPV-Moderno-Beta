# Revisión final de arquitectura — 2026-09-13 (fase 25)

Revisión como **arquitecto externo**. Método: verificación dirigida sobre el código
real (no re-lectura del proyecto completo), contrastada con lo que las fases 03–24
ya demostraron en ejecución (QA final con pila Docker real y 425 tests DB-gated).
**Esta fase no implementa nada**: solo propone. Ninguna línea de código cambia aquí.

## 1 · Lo que está bien resuelto (verificado, no tocar)

- **Capas limpias**: `api → services → repos → models` sin saltos; errores de
  negocio como `AppError` mapeados a RFC 9457 con códigos estables. El acoplamiento
  real es el deseable (la API no toca ORM; los servicios no conocen HTTP).
- **Dependencias backend**: 9 dependencias de runtime, todas usadas (fastapi,
  uvicorn, pydantic-settings, structlog, sqlalchemy, alembic, psycopg, argon2-cffi,
  pyjwt). Sin dependencias innecesarias detectadas.
- **Concurrencia**: bloqueos `FOR UPDATE` con orden por id (anti-deadlock),
  `SKIP LOCKED` en el reclamo de la cola de impresión (`repos/printing.py`),
  idempotencia con PK + respuesta congelada, una sola caja abierta por terminal
  vía índice parcial único (`uq_cash_sessions_open_per_terminal`). La fase QA
  ejercitó carreras reales sin doble ticket.
- **Seguridad**: Argon2id, refresh opaco rotativo en cookie HttpOnly, rate limit
  de login/PIN por IP+ruta, token de dispositivo guardado solo como SHA-256,
  secretos nuevos y rotados solo por entorno (ADR-008), CORS cerrado por defecto.
- **Base de datos**: dinero en céntimos (BigInteger) con CHECKs de negocio;
  índices que cubren los accesos reales (orders/lines/payments/tickets/invoices/
  sale_events/audit_log/idempotency), incluidos compuestos por fecha.
- **Despliegue**: una imagen autocontenida, `workers 1` documentado (hub WS),
  healthchecks, migraciones en arranque, backups con retención.

Conclusión de fondo: el monolito modular con API única y tres SPA servidas por el
propio proceso es **la arquitectura correcta para la escala** (un local, 2–10
terminales). Ninguna propuesta de esta revisión la cuestiona.

## 2 · Propuestas priorizadas

### P1 — Fiabilidad / operatividad (rompen o limitan funcionalidad hoy)

| # | Hallazgo | Propuesta |
|---|---|---|
| 1 | **API de clientes inexistente**: los permisos `customers.view/edit` están definidos en el catálogo pero no hay endpoints; toda venta va sin cliente y la facturación responde siempre 409. Es el único hueco que deja inoperante una funcionalidad ya construida (facturas + rectificativas, QA E16). | Fase corta propia: CRUD mínimo de clientes + asignación en pedido/venta, reutilizando el patrón existente. |
| 2 | **Impresión sin agente real**: la cola es correcta y segura, pero sin `tpv-agent` el despacho/confirm depende de llamadas API manuales (`NullPrinterAdapter`). | `tpv-agent` como servicio del terminal que consuma el token de dispositivo (ya diseñado: solo SHA-256 en BD) y haga `dispatch`+`confirm` contra la cola. |

### P2 — Mantenimiento / fiabilidad (riesgo real de pérdida o deriva)

| # | Hallazgo | Propuesta |
|---|---|---|
| 3 | **Contratos triplicados en los frontends**: `lib/api.ts` + `lib/schemas.ts` (espejos Zod) existen ×3 (mostrador, móvil, admin) y pueden derivar de la API sin que nada avise. | Mínimo: test de contrato autogenerado del OpenAPI (patrón ya probado en `test_auth_wall.py`) por SPA. Mejor: paquete compartido npm workspace, solo si aparece un 4º cliente. |
| 4 | **Outbox PWA en localStorage** (decisión de fase Offline): cuota típica ~5 MB; una jornada larga sin red puede superar la cuota y perder ventas no sincronizadas. | Migrar el outbox a IndexedDB (misma lógica, almacenamiento con cuota grande). |
| 5 | **Backups sin cifrar** (§10 lo pide; pendiente desde fase Instalador): los dumps contienen toda la BD de negocio en claro. | Cifrar con clave por entorno en `install.ps1`/`backup.sh` (edad: una sola tarea). |

### P3 — Higiene acotada (baratas, sin riesgo, cuando toque tocar)

| # | Hallazgo (verificado) | Propuesta |
|---|---|---|
| 6 | `_validation/_conflict/_not_found` duplicados en 7 servicios (sales, cash, catalog, payments, printing, documents, infrastructure). | Módulo común `app/services/errors.py`; refactor mecánico. |
| 7 | `date.today()` local en facturas/rectificativas (`services/documents.py:492,610`) frente a UTC en el resto: cerca de medianoche (y en la frontera de año de la serie) la fecha de factura puede discrepar de los eventos. | Fecha de negocio única (UTC o TZ configurada) inyectada por settings. |
| 8 | N+1 en `_order_block`: 3 consultas por venta embebida (líneas, pagos, ticket). Acotado (facturas de pocas ventas). | Solo si el tamaño típico de factura crece: batch de líneas/pagos por `order_id`. |
| 9 | `repos.list_invoice_lines` sin `ORDER BY`: las líneas de la respuesta no tienen orden estable (los tests las comparan como conjunto; el defecto visible de la fase QA fue del payload, ya corregido). | Orden estable por la venta (`created_at, id` vía join o columna derivada). |
| 10 | Fábricas de tests `_mk_*` repetidas en varios ficheros E2E. | Subir a `conftest.py` como fixtures helper. |

### Deuda técnica ya documentada (recordatorio, sin nuevo análisis)

MODO B de HTTPS sin ensayar en red real; build del admin ausente de
`install.ps1` (hoy solo mostrador+móvil); E2E de navegador (Playwright) y smoke
PWA en dispositivo; E2E de administración contra PostgreSQL real.

## 3 · Lo que NO haría

- Repartir los servicios en microservicios, añadir mensajería o cache externa:
  injustificable a esta escala y contra el despliegue de una sola imagen.
- Reescribir los contratos de dinero (string §3) o el montaje estático por el
  api (§1.4): decisiones asentadas, verificadas en QA y despliegue.
- Tocar el esquema de índices: no hay evidencia de acceso sin cubrir.

## 4 · Resumen

**3 propuestas P1/P2 urgente-realistas** (clientes, tpv-agent, outbox→IndexedDB +
cifrado de backups), **5 de higiene** y una lista corta de deuda ya conocida.
Ninguna exige re-arquitectura; todas caben en el diseño actual.

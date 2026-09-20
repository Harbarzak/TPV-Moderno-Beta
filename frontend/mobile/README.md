# TPV Móvil (PWA camarero)

Cliente **móvil primero** de la plataforma TPV (fase 13). Es una PWA independiente
(`frontend/mobile/`) que consume **la misma API** que el TPV de mostrador: nada de
lógica de negocio vive aquí — totales, permisos, estados y cálculos los decide
siempre el backend; el móvil viaja y muestra.

## Arranque (desarrollo)

```bash
npm install
npm run dev        # http://localhost:5174  (proxy /api → http://localhost:8000)
```

El backend debe estar levantado en el puerto 8000. Build de producción:
`npm run build` (carpeta `dist/` lista para servir desde el propio servidor LAN —
instalación de la PWA «desde el servidor», sin tiendas de apps, §1/§2).

## Funcionalidad (según permisos del usuario)

| Sección | Permiso | Contenido |
|---|---|---|
| Venta / Pedidos | `sales.sell` | Catálogo `/catalog/pos`; cada toque crea línea en el servidor (auto-save); cobro con `payments.take`; anulación con `sales.void` |
| Mesas | — | Solo aparece si la sonda detecta `GET /api/v1/restaurant/tables` (módulo de la fase 16) |
| Productos | `products.view` | Consulta/búsqueda del catálogo (solo lectura) |
| Estadísticas | `cash.open` / `reports.view` | Informe X del turno / histórico de cuadres Z |
| Caja | `cash.open` | Apertura, X, movimientos (`cash.movements`), arqueo y cierre Z (`cash.close`) |

Sin permiso, la sección no se muestra; el 403 del backend sigue siendo la última
palabra (la visibilidad local es solo UX).

## Decisiones de diseño

- **Sin lógica de negocio en el cliente**: el total a cobrar SIEMPRE viene del
  `GET` del pedido; el cobro envía el total textual del servidor y el cambio lo
  calcula el backend (`change_total`). `formatMoney`/`parseAmount` son puro
  formato/entrada, nunca aritmética de dinero.
- **Contratos validados con Zod al recibir** (`src/lib/schemas.ts`), espejo de los
  pydantic del backend: dinero string «12.34», cantidades «1.000» (§3). Si el
  servidor enviara otra cosa, el fallo salta antes de pintar.
- **Terminal local**: no existe aún el módulo de terminales (fase 21); el UUID se
  configura en Ajustes (localStorage) y el backend lo valida en cada uso.
- **Service worker propio** (`public/sw.js`, solo en producción): la ruta `/api/`
  JAMÁS se cachea; shell con stale-while-revalidate y navegación con fallback
  offline al HTML cacheado.
- **Offline (fase 14)**: la conexión se sonda contra `GET /healthz` (no basta
  `navigator.onLine`; banner global + re-sondeo cada ~15 s, `lib/connectivity.ts`).
  El catálogo guarda copia validada en localStorage (`tpv-mobile-catalog`) y la
  venta sin red abre pedido y añade líneas en una cola local append-only
  (`tpv-mobile-outbox`) que se drena EN ORDEN al volver la conexión, con
  `Idempotency-Key` por operación: reenviar no duplica. Sin servidor, cobrar y
  anular están deshabilitados (dinero real = servidor) y el total del ticket en
  cola es un estimado etiquetado. Matriz completa en `../README.md`.
- **Fuera de alcance deliberado**: devoluciones, facturación y administración —
  siguen en el TPV de mostrador.

## Tests

```bash
npm run test       # vitest (jsdom): dinero, permisos, contratos, sonda de conexión,
                   # caché de catálogo, outbox, sesión
```

Los tests no requieren base de datos: la capa HTTP se mockea.

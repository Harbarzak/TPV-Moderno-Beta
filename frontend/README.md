# TPV Moderno — frontend

Este directorio alberga **dos clientes** sobre la misma API:

- **`/`** — TPV de mostrador (fase 05): React 18 + TypeScript + Vite + Tailwind.
  Interfaz 2D con componentes accesibles; **Three.js queda prohibido** para la
  venta (§8).
- **`/mobile`** — PWA móvil del camarero (fase 13): misma pila, diseño móvil
  primero, instalable desde el servidor LAN. Ver `mobile/README.md`.

## Puesta en marcha

```bash
npm install
npm run dev        # http://localhost:5173 — proxía /api a http://localhost:8000
npm test           # vitest (lógica: dinero, lector, búsqueda, contratos, carrito, sesión)
npm run build      # tsc -b && vite build → dist/
```

Requiere el backend arrancado en el puerto 8000 (el proxy de Vite lleva `/api`
allí en desarrollo; en producción el propio servidor sirve `dist/`).

## Cómo se usa

- **Acceso**: usuario + contraseña (`POST /auth/login`); el token vive en
  `localStorage` y un 401 en cualquier petición devuelve al acceso.
- **Venta**: tocar un botón añade 1 unidad; el panel izquierdo cambia de
  panel/subpanel (Categorías → Paneles → SubPaneles → Productos).
- **Búsqueda** (`F2`): local, sobre la caché de terminal; nunca espera al
  servidor. Enter añade el resultado marcado y la ventana sigue abierta.
- **Lector de códigos**: sin configurar nada — un pulso de teclas terminado en
  Enter (ráfaga ≤ 60 ms, ≥ 4 caracteres) añade el producto. Se ignora mientras
  se escribe en un campo de texto.
- **Ticket**: vive en el cliente y persiste entre reinicios (`tpv-ticket-draft`);
  `+`/`−` ajustan cantidades (1 ud o 100 g en pesables), Vaciar pide
  confirmación. El desglose de IVA se calcula en céntimos exactos (BigInt,
  §3) — nunca con coma flotante.
- **Cobrar**: deshabilitado a propósito; el cobro (última escritura en BD) llega
  con la fase de venta.

## Estructura

```
src/
  lib/        dinero BigInt/mili-unidades, lector wedge, búsqueda sin acentos,
              cliente HTTP + esquemas Zod de los contratos del backend
  state/      sesión (login), catálogo (caché de terminal §7.2), ticket (persist)
  components/ acceso, barra superior, navegación de paneles, rejilla, ticket,
              búsqueda (F2) y ayuda (F1)
  hooks/      atajos globales de teclado
```

Los datos del servidor se validan con Zod al recibir: si el backend deriva del
contrato (dinero como float, decimales fuera de rango…), la pantalla falla
visible en lugar de vender mal.

## Offline / reconexión (fase 14)

**Detección**: `navigator.onLine` no basta (una WiFi sin salida da falso
positivo). Ambos clientes sonda `GET /api/v1/healthz` (`src/lib/connectivity.ts`):
un `TypeError` (la red ni dejó pasar la petición) es «sin servidor»; cualquier
respuesta HTTP, incluso 5xx, es «el servidor escucha». Sin servidor se
re-sondea cada ~15 s y un banner ámbar avisa en pantalla.

**Matriz — qué puede ejecutarse offline y qué requiere servidor:**

| Operación | Mostrador | Móvil |
|---|---|---|
| Ver catálogo y buscar | ✅ memoria + copia guardada (`tpv-terminal-catalog`) | ✅ memoria + copia guardada (`tpv-mobile-catalog`) |
| Editar el ticket en borrador | ✅ local (`tpv-ticket-draft`) | ✅ cola local (`tpv-mobile-outbox`) |
| Abrir pedido / añadir líneas en el servidor | ❌ requiere servidor | ✅ se encola y se envía SOLO al volver la conexión, en orden y con `Idempotency-Key` por operación (reenviar no duplica) |
| Cobrar / anular / devolver | ❌ (el cobro aún no existe; cuando llegue, requerirá servidor) | ❌ deshabilitados sin conexión |
| Caja (apertura, movimientos, arqueo) | — | ❌ requiere servidor |

Al fallar la carga del catálogo, ambos clientes pintan la última copia buena
(validada con Zod: corrupta o fuera de contrato se descarta) y la refrescan
solo en cuanto la sonda vuelve a dar servidor. En el móvil, el total de un
ticket sin sincronizar es un ESTIMADO (suma de PVP de las líneas en cola) y se
etiqueta como tal: el total real lo calcula siempre el backend. No hay
resolución de conflictos: si el servidor rechaza una operación en cola (4xx),
se descarta con aviso visible junto a las líneas de su pedido; un fallo de red
para el envío y conserva la cola íntegra y en orden. El cobro móvil reutiliza
su `Idempotency-Key` solo con el cuerpo idéntico al intento anterior — cambiar
importe o pagos es un intento nuevo, nunca un reintento encubierto.

## Informes (fase 15)

El mostrador añade la vista «Informes» (botón en la barra superior →
`src/components/ReportsView.tsx`, contrato en `src/lib/reports.ts`): resumen del
periodo con desglose de IVA, ventas por producto/categoría/camarero/forma de
pago, series por periodo (`hour/day/week/month`), tickets emitidos, facturas con
su detalle (el mismo snapshot congelado que la API de documentos) y cierres Z
pasados. El backend es `/api/v1/reports`, de solo lectura y bajo `reports.view`.

Reglas que la vista hereda del backend (§13):

- Rango de fechas OBLIGATORIO; por defecto, los últimos 7 días. Rango invertido
  no llega a disparar la consulta.
- Paginación en servidor: páginas de 50 filas con «Anterior/Siguiente» — nunca
  se pide la tabla entera, por grande que sea el histórico.
- La entrada es visible para cualquier sesión; sin el permiso `reports.view` el
  backend responde 403 y la vista lo muestra claro (la última palabra la tiene
  siempre el servidor, igual que en el móvil).
- El dinero llega como string con 2 decimales (§3) y se pinta tal cual; los
  importes de devoluciones llegan con signo (negativo en el resumen, positivo
  en forma de pago — la convención del informe Z de caja).
- El lector de códigos solo actúa en la vista de venta: un escaneo en informes
  no toca el ticket local.

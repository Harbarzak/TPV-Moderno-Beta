# Análisis URY antes de diseñar nuestra UI — 2026-09-13 (fase 26)

Lead UX/UI + arquitecto frontend. Fuente: repo público
[`ury-erp/ury`](https://github.com/ury-erp/ury) (rama `develop`, AGPL-3.0),
analizado por estructura, `FEATURES.md`, `package.json` y READMEs. **No se
ejecutó su DEMO ni se copió código.** Nuestro backend es FastAPI + PostgreSQL:
de URY solo nos interesan **ideas de producto e interacción**, nunca su
infraestructura Frappe/ERPNext.

## 1 · Qué es URY

Sistema de gestión de restauración FOSS sobre **Frappe/ERPNext** (Tridz
Technologies + Frappe), en producción real en 10+ locales. Cuatro frontends:

| App | Carpeta | Pila | Papel |
|---|---|---|---|
| URY POS (v2) | `pos/` | React 19 + Vite + TS + Tailwind + Radix + zustand + lucide | Cobro en barra/escritorio: 5 pantallas (`POS`, `Table`, `Orders`, `Dashboard`, `Settings`) |
| URY POS (v1, legado) | `urypos/` | **Vue 3** + Vite | El que FEATURES señala para camareros/tablet (`/urypos/Table`) |
| URY Mosaic | `mosaic/` | web | KDS de cocina multi-cocina con KOT |
| Self-order | `self-order/` | React + Vite + Tailwind | Autopedido de cliente |

En `packages/` viven `@ury/core` y `@ury/ui` (kit compartido). Realtime por
`socket.io`; impresión por `qz-tray` (firmada con `jsrsasign`) + impresoras de
red + un websocket propio. **No hay ni PWA, ni service worker, ni IndexedDB,
ni cola offline en ninguna dependencia.**

## 2 · Análisis por eje pedido

- **Frontend / componentes visuales**: pila prácticamente idéntica a la nuestra
  (React+Vite+TS+Tailwind+zustand+lucide+Radix+CVA+`tailwind-merge`) —
  convergencia que valida nuestras elecciones. Su lección organizativa: un kit
  interno (`@ury/ui`) sobre el que montan todas las apps.
- **Navegación**: POS v2 con sidebar de cursos (entrantes/principantes/
  postres) dentro de la pantalla de venta; jerarquía salas → mesas → pedido;
  *Order Log* con estados (Draft, Unbilled, Recently Paid, Paid).
- **Flujo de venta**: mesa ocupada salta al menú; **un clic añade** al carrito,
  **doble clic abre ficha** de personalización; cantidades con +/− y diálogo
  preciso; **comentarios por línea y a nivel de pedido**; cliente con Nº de
  comensales y **top-3 de favoritos** de clientes recurrentes; "Update"
  persiste borrador (invoice draft); apertura y cierre diarios obligatorios.
- **Responsive / tablet / móvil**: FEATURES lo dice explícitamente — v2
  **solo está pensada para máquinas POS/escritorio**; tablet y camareros se
  resuelven en el v1 (otra pila, Vue). Es decir: URY **no tiene un frontend
  responsive único**; mantiene dos por perfil de usuario.
- **Estados**: en cocina, muy trabajados (códigos de color: blanco nuevo,
  azul take-away, naranja modificado, rojo anulado/parcial; timers «KOT
  Warning Time» con alertas y roles destinatarios). En POS, badges de mesa por
  color y **tiempo transcurrido** (Atención roja, Ocupada amarilla, Libre
  verde, Activa azul). Para loading/empty/error no hay documentación visible
  (usan `react-toastify` para feedback).
- **Interacción táctil**: el flujo de camarero vive en v1; v2 usa doble clic
  (patrón de ratón, fricción en táctil). El drag & drop aparece solo en el
  **editor visual de salas** (posicionar/redimensionar mesas por forma).
- **Offline**: **inexistente**. URY es 100 % online contra el servidor Frappe
  (socket.io solo da realtime, no resiliencia). Nuestra PWA con outbox
  (Fase Offline) es **más avanzada** que URY en este eje.
- **Impresión**: tres vías (QZ Tray con firma RSA, impresoras de red
  ERPNext, websocket propio `/app/websocket-print`); **enrutado de impresoras
  por sala y por unidad de producción**; ciclo de KOT diferenciado: nuevo,
  modificado, anulado, **parcialmente anulado**; reimpresión de KOT.
- **Pagos**: «Make Payment» con modos de pago; **split de cuenta por ítems o
  por comensales** (v2); apertura/cierre de caja diarios (POS Opening/Closing
  Entry) — equivalente a nuestra sesión de caja. El cobro parece **un modo por
  operación**: no hay pago mixto explícito; el nuestro sí.

## 3 · Tabla de decisión

| Elemento URY | Adoptar | Adaptar | No adoptar | Motivo |
|---|:---:|:---:|:---:|---|
| Pila UI React+Vite+TS+Tailwind+zustand+lucide+Radix | ✔ | | | Es la misma que la nuestra desde las fases de frontend: validación externa, nada que cambiar. |
| Kit interno compartido (`@ury/ui`) entre apps | | ✔ | | Nuestros contratos/`api.ts` están triplicados ×3 (revisión 25-P2): unificar cuando haya 4º cliente o vía test de contrato del OpenAPI. |
| Sidebar de cursos/categorías en la venta | ✔ | | | Menos clics en el mostrador; casa con nuestros paneles por departamento. |
| Badges de mesa con color + tiempo transcurrido (semáforo de atención) | | ✔ | | Excelente idea operativa; mapear a nuestros estados (libre/ocupada/cuenta) con nuestros colores. |
| Editor visual de salas con drag & drop (admin) | | ✔ | | Buena idea para el admin, post-MVP: exige librería dnd que hoy no dependemos. |
| Un clic añade / doble clic abre ficha de producto | | ✔ | | En táctil el doble clic es fricción: un clic añade y la ficha se abre con pulsación larga o botón explícito de editar. |
| Comentarios por línea y a nivel de pedido (llegan al KOT) | | ✔ | | Valor alto de restaurante; exige campo `notes` en línea/pedido + UI, como evolución del mostrador. |
| Cliente con comensales (pax) y top-3 favoritos | | ✔ | | Depende de la P1 «API de clientes» (revisión 25); favoritos = extra barato sobre histórico ya existente. |
| Order Log con filtro «Recently Paid» | | ✔ | | Nuestros históricos ya cubren los estados; el filtro «recién cobradas» es una mejora barata del listado. |
| Realtime socket.io entre puestos | | ✔ | | Ya tenemos hub WS: usarlo para refrescar mesas/cola de impresión en vivo en vez de polling. |
| Apertura/cierre diario obligatorios (Opening/Closing Entry) | ✔ | | | Ya lo implementamos como sesión de caja única por terminal (validado en QA E01/E13/E15). |
| Pago mixto (varios métodos en un cobro) | | ✔ | | No existe explícito en URY: **el nuestro es más completo**; nada que importar. |
| Split de cuenta por ítems o comensales | | ✔ | | Post-MVP: nuestro mixto por métodos cubre lo básico; el split exige modelo de reparto nuevo. |
| Impresión QZ Tray (app Java firmada en el host) | | | ✔ | Requiere software externo + firma RSA en cada terminal: nuestro agente propio de impresión (cola + confirm) es más simple y sin Java. |
| Enrutado de impresoras por sala/unidad de producción | | ✔ | | Muy bueno para restaurantes multi-cocina; se incorpora a nuestra cola como campo de destino por zona. |
| Ciclo de KOT diferenciado (nuevo/modificado/anulado/parcial) | | ✔ | | Nuestra cola solo distingue job/doc: añadir el motivo en el payload cuando haya cocina real. |
| KDS (Mosaic) con semáforo y timers de aviso | | ✔ | | No tenemos cocina en alcance (se descartó `/app/kds`); patrón de colores/timers a recordar si llega. |
| Offline (cola, service worker, IndexedDB) | | | ✔ | URY no lo tiene: no hay nada que adoptar; **nuestra PWA outbox ya es superior aquí** — no retroceder. |
| i18n + RTL (árabe) | | | ✔ | Alcance del proyecto: un local en español; coste permanente sin beneficio. |
| 4 frontends con pilas mezcladas (Vue v1 + React v2) | | | ✔ | Su mayor deuda técnica: lección en contrario — mantener nuestras 3 SPA en un único stack. |
| Frappe/ERPNext, DocTypes, `frappe-js-sdk` | | | ✔ | Backend ajeno: FastAPI + PostgreSQL propios. |

## 4 · Conclusiones

1. **Validación**: nuestra pila UI y nuestros invariantes (caja diaria, dinero
   como string, PWA offline) son iguales o superiores a los de un producto en
   producción con 10+ locales. En offline y pago mixto vamos por delante.
2. **Adoptar ya (barato, sin backend nuevo)**: sidebar de categorías,
   semáforo de mesas con tiempos, filtro «recién cobradas» en históricos.
3. **Adaptar en la siguiente iteración de producto** (requieren backend):
   comentarios por línea/pedido, cliente + favoritos (encaja con la P1 de la
   revisión 25), enrutado de impresoras por zona y motivo de KOT en la cola.
4. **No adoptar**: QZ Tray, i18n/RTL, y sobre todo el modelo de URY de un
   frontend distinto por dispositivo: nuestro mostrador táctil único
   (tablet 10–15″ y escritorio) con PWA offline sigue siendo la apuesta
   correcta para un local.

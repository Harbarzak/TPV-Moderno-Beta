# Design System del TPV — inspirado en URY (fase 27, 2026-09-14)

Documento de **diseño**. Define tokens, componentes y patrones de la UI del nuevo TPV.
**Esta fase no implementa nada**: ninguna línea de código cambia aquí. Los valores son
prescriptivos para las fases futuras (restaurante, KDS, evolución del mostrador) y para
cualquier pantalla nueva; lo ya construido y en verde (QA fase 24) no se reescribe, se
adopta de forma oportunista.

Inspiración: `docs/ury-analysis.md` ( ideas de producto de URY, **nunca su código**,
ADR-007). Insumos: `ARCHITECTURE.md`, `PROJECT_STATE.md`, `docs/ury-analysis.md`.

## 1 · Punto de partida (lo que ya existe y se respeta)

Tres SPA servidas por el api (`/app/tpv`, `/app/movil`, `/app/admin`), misma pila:
React 18 + TypeScript + Vite + Tailwind + Radix UI + Lucide + Zustand + Zod.
Hoy cada una tiene identidad propia con la paleta por defecto de Tailwind
(los `tailwind.config` no extienden nada):

| SPA | Tema | Acento | Perfil de uso |
|---|---|---|---|
| Mostrador (`frontend/`) | Oscuro (slate-900 base, slate-600/700/800 superficies) | Ámbar `amber-400/500` | Táctil + teclado, 1280×800, velocidad crítica |
| Móvil (`frontend/mobile/`) | Claro (slate-100) | Teal `teal-700` | PWA camarero, táctil puro, ≥44 px |
| Admin (`frontend/admin/`) | Claro (slate-100) | Índigo `indigo-600` | Ratón/teclado, formularios e informes |

Decisión: **conservar las tres identidades** (separación visual entre operación diaria
y administración, ya útil en QA) pero **unificar la semántica**: mismos nombres de
token, mismos tamaños táctiles, mismos patrones de estado y feedback en las tres.

## 2 · Prioridades (en orden, con reglas derivadas)

| # | Prioridad | Reglas operativas |
|---|---|---|
| 1 | **Velocidad** | Toda acción frecuente ≤ 2 toques desde la pantalla de venta. Cero animaciones en el camino crítico (máx. 100–150 ms, solo transiciones de estado, nunca de entrada). Feedback inmediato (≤ 100 ms) aunque la red tarde: el tile de producto responde al instante; el resultado del servidor llega después. Botón de cobro se deshabilita al primer toque (anti doble-submit) sin esperar respuesta. Teclado: F2 búsqueda, F1 ayuda, Enter confirma — nunca obligatorio usar ratón. |
| 2 | **Legibilidad** | Dinero y cantidades SIEMPRE `tabular-nums`, alineados a la derecha. Mínimo 14 px en información operativa (12 px solo metadatos). El total del ticket y el cambio son el elemento más grande de su pantalla (30–36 px bold). Contraste AA en todos los pares de la §3 (verificado por pares en §8). Cocina: 18–20 px mínimo (lectura a distancia). |
| 3 | **Táctil** | Target mínimo 44×44 px; preferible 48. Acción principal de cobro ≥ 56 px de alto. `touch-manipulation` y `select-none` en botones (ya en las SPA). Separación ≥ 8 px entre targets. 1 toque añade producto; la ficha se abre con pulsación larga (~500 ms) o botón explícito — **nunca doble clic** (adaptación táctil de la decisión URY, `ury-analysis` §3). |
| 4 | **Accesibilidad** | Radix UI como base de todo control compuesto (focus trap, roles y teclado gratis). Focus visible siempre (anillo ámbar/índigo de 2 px), incluso en táctil. Icono nunca solo: siempre con texto o `aria-label`. El estado nunca se comunica solo con color (badge con texto). |
| 5 | **Coherencia** | Un solo catálogo de tokens (§3) con nombres semánticos idénticos en las 3 SPA; los valores pueden diferir por tema, el nombre no. Prohibido hex suelto en componentes: solo tokens. Mismo patrón de toast/dialogo/estado vacío en las tres apps. |

## 3 · Tokens

### 3.1 Colores — nombres semánticos (los mismos en las 3 SPA)

| Token | Mostrador (oscuro) | Móvil (claro) | Admin (claro) | Uso |
|---|---|---|---|---|
| `surface` | `slate-900` | `slate-100` | `slate-100` | Fondo de pantalla |
| `surface-raised` | `slate-800` | `white` | `white` | Cards, paneles, tickets |
| `surface-sunken` | `slate-600/700` | `slate-200` | `slate-200` | Rejillas, cabeceras de tabla, inputs |
| `ink` | `slate-100` | `slate-800` | `slate-800` | Texto principal |
| `ink-muted` | `slate-400` | `slate-500` | `slate-500` | Metadatos, placeholders |
| `accent` | `amber-500` (hover `amber-400`) | `teal-700` | `indigo-600` (hover `indigo-700`) | Acción primaria |
| `accent-ink` | `slate-950` (texto sobre ámbar) | `white` | `white` | Texto sobre acento |
| `focus-ring` | `amber-400` | `teal-600` | `indigo-500` | Anillo de foco |
| `success` | `emerald-500` | `emerald-700` | `emerald-700` | Éxito, cambio a devolver, mesa libre |
| `warning` | `amber-500` | `amber-600` | `amber-600` | Offline, timer de aviso, mesa con comanda |
| `danger` | `red-600` (superficie) / `red-500` (texto) | `red-700` | `red-700` | Anular, descuadre, errores |
| `info` | `sky-500` | `sky-700` | `sky-700` | Mesa «cuenta pedida», comanda en curso |

Reglas de contraste ya resueltas por la tabla: sobre `accent` ámbar el texto es
`slate-950` (≈ 9,8:1); blanco sobre `teal/indigo/emerald/red` solo con los tonos
`-600/-700` indicados (≥ 4,5:1 o texto ≥ 18 px bold). Negativos (devoluciones,
rectificativas) siempre en `danger`; positivo/cambio en `success`.

### 3.2 Estados de mesa (semáforo adoptado de URY, mapeado a nuestros estados)

| Estado | Color | Chip | Nota |
|---|---|---|---|
| Libre | `success` (emerald) | texto `white` | Sin draft asociado |
| Con comanda (abierta) | `warning` (amber), texto `slate-950` | + tiempo transcurrido | Draft en servidor ligado a mesa |
| Cuenta pedida / cobrando | `info` (sky) | + importe | Pendiente de cobro |
| Requiere atención | `danger` (red) | pulsante suave (1×, no loop) | Llamada/KOT anulado (futuro) |

El **tiempo transcurrido** es obligatorio en la card (tabular, mm/h) — lección central
de URY (`ury-analysis` §2). El estado nunca va solo por color: el chip lleva texto.

### 3.3 Estados de cocina (KDS, patrón URY adaptado a nuestros 3 estados)

| Estado comanda/línea | Color | Uso |
|---|---|---|
| Pendiente (nueva) | superficie neutra + borde `info` | Recién llegada |
| En curso | `info` (sky) | Cocinando |
| Lista | `success` (emerald) | Para servir |
| Anulada / parcial | `danger` (red), tachada | Nunca se borra (inmutabilidad §3) |
| Modificada | `warning` (amber) | Notas añadidas tras emitir (futuro: motivo KOT) |
| Timer de antigüedad | `ink-muted` → `warning` → `danger` | Umbrales por parámetro (p. ej. 5/10 min), chip tabular |

### 3.4 Tipografía

- Pila del sistema, **sin webfont** (LAN-first, cero coste de carga):
  `ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif`.
- Escala cerrada: 12 · 14 · 16 · 18 · 20 · 24 · 30 · 36 px. Base 14 (mostrador/admin)
  y 16 (móvil). Mínimos: 14 en info operativa, 12 solo metadatos, 18 en KDS.
- **Dinero, cantidades, tiempos y números de documento**: `tabular-nums` obligatorio.
  Total de ticket/pago: 30–36 px bold. Nº de ticket en KDS: 24 px bold.
- Peso: 400 texto, 500-600 etiquetas y botones, 700 solo cifras y títulos.

### 3.5 Espaciado, radios, capas, motion

- Escala de 4 px (Tailwind por defecto): gap de rejillas 8–12, padding de card 12–16,
  padding de pantalla 16–24. Radio: `rounded-lg` (8) controles, `rounded-xl` (12)
  cards táctiles, `rounded-full` chips/badges.
- Rejilla de producto: tile mínimo 120×96 px (4–5 columnas a 1280×800 con sidebar),
  gap 8 px. En tablet grande puede crecer, nunca encoger de ahí.
- Z-index por capas Radix: overlay 40, dialog/toast 50, banner offline 60 (siempre visible).
- Motion: `transition 100–150 ms ease-out`; `active:scale-[0.98]` en botones táctiles
  (ya existe en móvil, generalizar al mostrador). Prohibidos: animaciones de entrada,
  efectos decorativos, Three.js en venta (§1.3 de ARCHITECTURE).
- Iconos Lucide: 16 px (inline), 20 px (botones), 24 px (navegación), stroke 2.

## 4 · Componentes base

### 4.1 Botones

| Variante | Aspecto | Uso |
|---|---|---|
| `primary` | fondo `accent`, texto `accent-ink` | La acción principal por pantalla (una sola): Cobrar, Abrir caja, Guardar |
| `secondary` | `surface-sunken`, texto `ink` | Acciones secundarias: descuento, X, imprimir |
| `ghost` | transparente, hover `surface-sunken` | Iconos de barra superior, cerrar |
| `danger` | `danger` sólido, texto blanco | Anular, salir sin guardar — siempre con confirmación (§4.4) |

Tamaños: `sm` 36 px (admin/ratón) · `md` 44 px · `lg` 48 px · `touch` 56 px (solo
cobro y keypad). Disabled: opacidad 40 % + `cursor-not-allowed`; nunca deshabilitar
sin motivo visible (title o texto). Loading: texto «…» o spinner de 16 px, manteniendo
el ancho (no saltos de layout).

### 4.2 Inputs

- Texto/número: alto ≥ 44 px en táctil, 36 en admin; `focus-ring`; error con borde
  `danger` + texto bajo el campo (nunca solo borde).
- **Dinero**: el dato viaja como string (§3/§7.1); la máscara es presentación con
  `Intl.NumberFormat('es-ES')`. En mostrador y móvil NUNCA se abre el teclado del SO:
  **keypad propio** (0-9, ⌫, 00, coma) en botones `touch` ≥ 56 px — imprescindible
  para tendered/arqueo/movimientos. En admin, input normal validado con Zod.
- Cantidades: stepper +/− grande junto al valor editable (patrón ticket §5.2).
- Selección única de opciones frecuentes (forma de pago, motivo): **chips/botones**,
  no `<select>`, en táctil. `<select>` solo en admin.

### 4.3 Cards

- Contenido: `surface-raised`, radio `xl`, padding 12–16, borde 1 px `surface-sunken`
  (en tema claro, sombra suave en vez de borde).
- Variantes: `clickable` (cursor + `active:scale` — tiles de producto y mesa) y
  estática (resúmenes, informes).
- Cabecera con icono Lucide 20 + título 14-16 semibold; cuerpo 14; pie con acciones
  alineadas a la derecha.

### 4.4 Dialogs (Radix `Dialog` / `AlertDialog`)

- Overlay `slate-950/60`; panel centrado, máx: 480 px confirmación · 560 px formulario ·
  720 px cobro (§5.3). Foco inicial en la acción sugerida (trampa de foco de Radix).
- Confirmación de operaciones de dinero o destructivas: **siempre `AlertDialog` con la
  consecuencia en el título** («Se anulará el ticket A-000012»), botón destructivo
  separado a la izquierda del primario, `Esc` cierra sin actuar, `Enter` confirma solo
  con el formulario válido.
- En operaciones de cobro el cierre por `Esc`/overlay está **deshabilitado**: solo sale
  por botón explícito (evita cobros a medias).

### 4.5 Toast

- Posición: arriba-centro en mostrador, abajo-centro (sobre la barra) en móvil,
  abajo-derecha en admin. Nunca modal: no bloquean el input.
- Tipos con icono + color + texto (nunca solo color): `success` 4 s, `info` 6 s,
  `warning` 8 s, `error` persistente con acción (Reintentar / Ver cola).
- Casos de obligado uso: cambio a devolver, venta en outbox pendiente, operación
  descartada tras 4xx al reconciliar, impresora caída (§9.4).
- Máximo 3 apilados; se reemplazan por clave (no duplicar «guardado» por cada línea).

### 4.6 Tablas (admin, informes, históricos)

- Cabecera sticky con fondo `surface-sunken`; filas 40 px (admin) / 44 px (listas
  táctiles); zebra opcional en temas claros.
- Columnas numéricas: alineadas a la derecha, `tabular-nums`; dinero formateado es-ES;
  negativos en `danger` con signo. Columna de estado como chip con texto (§3.2/§3.3).
- Paginación en servidor (ya implementada): barra inferior con «Anterior/Siguiente» y
  contador «n–m de total». Orden pulsando cabecera solo client-side en páginas.
- Fila: acción principal al final (Ver/Reimprimir); en táctil la fila entera es target.
- Filtro «Recién cobradas» como chip rápido en históricos de tickets (adoptado de URY).

## 5 · Patrones de dominio

### 5.1 Producto (tile)

- Tile 120×96 mín.: nombre (2 líneas máx., truncado), **precio siempre visible**
  abajo-derecha (16-18 px, tabular), iconitos de estado (pesable, cocina, bloqueado).
- Interacción: **1 toque = añade** con feedback inmediato (escala 0.98 + eco en el
  ticket); pulsación larga ~500 ms o botón ⋯ = ficha (editar cantidad/notas — futuro).
- Variantes: compacta (búsqueda F2, lista de 44 px) y rejilla (paneles).

### 5.2 Ticket (panel derecho del mostrador y detalle en móvil)

- Líneas: `qty ×` nombre …… importe (tabular, derecha); notas debajo en 12 px;
  tocar línea = editar/cantidad/anular (con permiso, `AlertDialog`).
- Pie SIEMPRE visible: base/IVA/total (total 30-36 px bold) + botones:
  **Cobrar** (`primary touch` ≥ 56 px) · Descuento · Anular ticket.
- Draft local persistente (mostrador) o en servidor (restaurante/móvil): la UI es
  idéntica; solo cambia el origen del total (estimado etiquetado si viene de outbox).

### 5.3 Pagos (pantalla de cobro, dialog 720 px)

- Total gigante arriba (36 px); **métodos como botones grandes** (≥ 56 px) con icono
  Lucide + nombre; pago **mixto** por defecto: lista de pagos parciales con
  «pendiente» recalculado tras cada añadir.
- Efectivo: keypad propio (§4.2) → `tendered` → **cambio en `success` 30 px**
  (calculado por el backend, nunca en cliente). Tarjeta/otro: sin `tendered`.
- Tras confirmar: botón deshabilitado al primer toque, spinner corto, éxito = cierre
  del dialog + toast de cambio + apertura de cajón si procede. Errores del servidor
  (`PAYMENT_*`, 409) como toast `error` con el problema exacto y el dialog intacto.

### 5.4 Mesas (modo restaurante)

- Vista por salas → rejilla de cards de mesa: nombre grande (20 px), **chip de estado
  del semáforo (§3.2) + tiempo transcurrido tabular**, importe abierto si lo hay, pax.
- 1 toque abre la mesa (saltar a su ticket, como URY); menú contextual (⋯) para
  juntar/separar/traspasar. Búsqueda de mesa por nombre en salas grandes.
- Refresco en vivo por WS (`sales`, futuro tema de mesas); fallback polling al reconectar.

### 5.5 Cocina (KDS)

- Tile de comanda: nº de ticket 24 px bold, líneas 18-20 px, **timer tabular con
  semáforo de antigüedad** (§3.3); fondo del tile = estado (§3.3); anulada tachada,
  nunca desaparece hasta confirmarse.
- Acciones grandes de una sola fila: «Empezar» / «Lista» / «Anular» (≥ 48 px).
- Orden: antigüedad descendente; aviso sonoro opcional al cruzar umbral (patrón URY
  «KOT Warning Time»); sin drag & drop en el MVP.

## 6 · Navegación

| SPA | Patrón |
|---|---|
| Mostrador | **Topbar**: estado de caja/sesión, camarero activo (cambio con PIN), Informes, banner offline si caído. **Izquierda: sidebar de categorías/paneles** (adoptado de URY) con el panel activo en `accent`; centro: rejilla; derecha: ticket (§5.2). Atajos F1/F2 persistentes en pie. |
| Móvil | **Bottom bar** de 4-5 destinos, 56 px de alto, target ≥ 48: Inicio, Venta, Mesas* (solo si el módulo existe — ya condicionado por sonda), Caja, Ajustes. Nada de hamburguesa: todo visible. |
| Admin | **Sidebar izquierda** con los módulos filtrados por permisos (`sectionsFor`, ya implementado), colapsable; contenido máx. ~1200 px centrado; migas de pan en pantallas de detalle. Identidad índigo para separar de la operación. |

Regla común: el destino actual se marca con `accent` + icono relleno; el badge de
pendientes (outbox, cola de impresión) es un contador numérico sobre el icono, nunca
solo un punto de color.

## 7 · Estados transversales (URY no los documenta; los definimos)

| Estado | Patrón |
|---|---|
| Loading > 300 ms | Skeleton (`animate-pulse`) con la silueta real; nunca bloquear toda la pantalla. Acciones locales: optimistas (§2). |
| Vacío | Icono Lucide 24 + una línea de qué falta + acción directa («No hay productos en este panel → Editar paneles»). |
| Error | Toast `error` persistente (§4.5) + estado inline en el módulo; el código estable del problem+json se muestra en pequeño (soporte). |
| Offline | Banner `warning` fijo (z-60): «Sin conexión con el servidor — las ventas se guardan y se enviarán al reconectar». Sonda ya existente (15 s). En cobro/caja (requieren servidor) el botón se deshabilita con explicación. |

## 8 · Accesibilidad (verificación por pares)

- Contraste AA mínimo (4,5:1 texto normal, 3:1 ≥ 18 px bold) — pares ya elegidos en §3:
  ámbar `amber-500` + `slate-950` ≈ 9,8:1; `indigo-600`/blanco ≈ 6,3:1; `teal-700`/blanco
  ≈ 5,4:1; `red-700`/blanco ≈ 5,9:1; `slate-400` sobre `slate-900` ≈ 7:1 (metadatos).
- Foco visible con `focus-ring` en TODO control, también táctil; orden de tabulado =
  orden visual; Radix provee trampa de foco y `aria-*` en dialog/toast/tabs.
- Estado nunca solo por color (chips con texto, §3.2/§3.3); iconos con `aria-label`.
- Targets ≥ 44 px y separación ≥ 8 px (§2) — también ayuda en ratón.

## 9 · Gobernanza y adopción

1. **Dónde viven los tokens**: `theme.extend` de cada `tailwind.config` + variables CSS
   por tema (`--tpv-accent`…) cuando toque dinamismo (p. ej. color por sala). Nombres
   semánticos idénticos en las 3 SPA; valores según §3.1.
2. **Adopción sin romper**: las fases cerradas (QA verde) no se reescriben. Se aplica
   obligatoriamente en: fase **Modo restaurante** (mesas §5.4), fase **KDS** (§5.5) y
   toda pantalla nueva; en las existentes, refactor oportunista al tocarlas.
3. **Kit compartido**: los contratos ya triplicados ×3 (revisión 25-P2) y estos tokens
   apuntan a un futuro paquete interno tipo `@tpv/ui` — **solo cuando aparezca el 4º
   cliente** (criterio ya fijado en la revisión 25). Hoy: disciplina + test de contrato.
4. **Verificación**: checklist DS en cada PR de UI (solo tokens, targets táctiles,
   foco visible, dinero tabular, estados vacío/error) + regresión visual Playwright
   con snapshots en 1280×800 y 1920×1080 (§13 de ARCHITECTURE).
5. **Licencia**: todas las decisiones derivan de ideas de producto de URY analizadas en
   `ury-analysis.md`; nada de su código (AGPL) ni sus valores visuales se copian (ADR-007).

## 10 · Fuera de alcance de este diseño

- i18n/RTL (descartado en `ury-analysis` §3): un solo idioma, es-ES.
- Temas de color configurables por cliente (los 3 temas son de fábrica).
- Editor visual de salas drag & drop (URY): propuesta P-post-MVP, exige librería dnd;
  las salas se gestiona por formularios de admin hasta entonces.
- Componentes de facturación específicos: el render fiscal es del servidor (payload
  congelado); la UI solo muestra/elige (§5.2/§5.3) — no hay diseñador de tickets.
- Split de cuenta por ítems/comensales: post-MVP si llega (modelo de reparto nuevo).

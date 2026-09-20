# Revisión AGPL / reutilización URY — 2026-09-14

Fase: Revisión AGPL / reutilización URY. Verificación dirigida ANTES de incorporar
cualquier código de URY (no es una re-auditoría del proyecto). Decisión registrada en
**ADR-009**; la política de fondo sigue siendo **ADR-007** (URY solo referencia UX).

## 1 · Objeto

El proyecto usa `github.com/ury-erp/ury` como acelerador de UX/producto (fase 26,
`docs/ury-analysis.md`). Esta fase responde, pieza a pieza, las 7 preguntas del prompt:
fichero, licencia, dependencias, copiar/modificar/estudiar, procedencia, obligaciones
AGPL y registro en ADR.

## 2 · Verificación ejecutada sobre el repo (2026-09-14)

| Comprobación | Resultado |
|---|---|
| Búsqueda de ficheros/marcas de URY y su stack (`ury`, `@ury/`, `socket.io`, `qz-tray`, `jsrsasign`, `frappe`, `erpnext`) en `frontend/**` (4 SPA) y `backend/**`, excluyendo `node_modules`, `dist`, `.venv` y artefactos | **Sin código de URY.** Coincidencias examinadas una a una: todas falsos positivos (subcadenas `qz`/`ury` al azar dentro de hashes `sha512-…` de `package-lock.json`, bundle minificado y XML de pytest) **salvo una cita textual legítima**: la descripción del parámetro `restaurant.enabled` en `docs/database/seed.sql:116` — «Habilita el módulo de restaurante (mesas/URY-style)». Es atribución de inspiración en texto legible por humanos, no código de terceros. |
| Dependencias exactas del stack URY en los 4 `package-lock.json` (`node_modules/socket.io`, `qz-tray`, `jsrsasign`, `@ury/*`) | **Cero coincidencias.** El realtime propio es `WebSocket` nativo contra nuestro hub; la impresión es cola propia + adaptadores propios. |
| Dependencias del backend (`pyproject.toml`) | **Cero coincidencias** con URY/Frappe. Stack runtime permisivo (MIT/BSD/Apache/ISC; `psycopg` LGPL-3.0 con excepción de enlazado). |
| UI propia frente a patrones de URY | Los patrones adoptados (§3) están reimplementados desde cero con nuestros tokens y contratos: sin diff posible contra su código, ni dependencia transitiva suya. |

## 3 · Inventario por pieza (los 7 puntos)

| Pieza de URY | Fichero origen (rama `develop`) | Licencia | Dependencias suyas implicadas | Decisión | Procedencia en nuestro repo |
|---|---|---|---|---|---|
| Sidebar de categorías en la venta | `pos/` (React 19) | AGPL-3.0 | ninguna (idea de layout) | **Solo estudiada** → versión propia sobre nuestros paneles/subpaneles (fase 28) | `docs/ury-analysis.md` §3; `docs/design-system.md` |
| Semáforo de mesas color + tiempo | `pos/` badges de mesa | AGPL-3.0 | ninguna | **Solo estudiada** → propio: mapa libre/ocupada/cuenta/atención con nuestros tokens (fase 27 §3.2), Sala (fase 30) y Mesas móvil (fase 31) | `docs/design-system.md`, `docs/ury-analysis.md` |
| Filtro «recién cobradas» en históricos | `pos/` Order Log | AGPL-3.0 | ninguna | **Solo estudiada** → filtro propio en nuestros informes (fase Informes) | `docs/ury-analysis.md` §3 |
| KDS: semáforo y timers de aviso | `mosaic/` | AGPL-3.0 | socket.io (SU transporte, no adoptado) | **Solo estudiada** → KDS propio completo (fase 32): columnas propias, semáforo 10/20 min propio, hub WS propio (tema `kds`), KOT en nuestra cola | `frontend/kds/**`, `docs/design-system.md` §5.5 |
| Comentarios por línea/pedido hacia cocina | `pos/` | AGPL-3.0 | ninguna | **Adaptado con diseño propio** anterior y posterior al análisis: `notes` de `order_lines` (fase Motor de ventas) y rectificación de comanda (fase 32) | esquema BD propio; sin código suyo |
| Enrutado de impresoras por sala/unidad + motivo de KOT en payload | `mosaic/`, `pos/` | AGPL-3.0 | ninguna | **Pendiente de adaptar** (cuando la impresión real llegue con tpv-agent): será implementación propia como campo de destino/motivo en NUESTRA cola | `docs/ury-analysis.md` §3 (anotado) |
| Cliente + pax + favoritos | `pos/` | AGPL-3.0 | ninguna | **Pendiente de adaptar**, bloqueado por la P1 «API de clientes» (revisión 25); será propio | `docs/ury-analysis.md` §3 (anotado) |
| QZ Tray (impresión Java firmada), i18n/RTL, 4 frontends con pilas mezcladas, Frappe/ERPNext, offline (no lo tienen) | varios | AGPL-3.0 | qz-tray, jsrsasign, Frappe | **No adoptar** (explícito) | `docs/ury-analysis.md` §3 |

## 4 · Obligaciones AGPL evaluadas

- AGPL-3.0 activa sus obligaciones de copyleft (incluida la §13 por red) cuando se
  **distribuye o comunica código derivado**. Ideas de producto, layouts y patrones de
  interacción **no son titularidad de copyright**: reimplementarlos desde cero no crea
  obra derivada.
- **Resultado: cero código AGPL incorporado hasta hoy** → el proyecto no contrae
  ninguna obligación AGPL; conserva licencia y distribución propias.
- Además, el análisis de fase 26 se hizo **sin ejecutar su DEMO y sin clonar su código
  en el repo** (solo estructura, FEATURES.md, package.json y READMEs).
- Si en el futuro se quisiera copiar código real: ADR-007 ya lo exige — decisión formal
  nueva (adopción de licencia compatible o separación estricta), nunca adopción
  silenciosa. El checklist del §5 es el camino obligatorio.

## 5 · Decisión y regla de futuro (→ ADR-009)

1. **Se ratifica ADR-007.** No se incorpora (ni se ha incorporado) código de URY;
   todo lo adoptado es versión propia inspirada en su comportamiento.
2. **Checklist obligatorio** antes de incorporar CUALQUIER código de terceros (no solo
   URY): 1) fichero origen · 2) licencia · 3) dependencias que arrastra · 4) decisión
   copiar/modificar/estudiar · 5) procedencia documentada · 6) obligaciones evaluadas ·
   7) registro en ADR. Con dudas → versión propia inspirada en su comportamiento.
3. Cualquier pieza nueva de URY pasa por este mismo documento (añadir fila en §3).

## 6 · Anexo — coincidencias examinadas y descartadas

- `frontend/*/package-lock.json`, `frontend/dist/assets/*.js`,
  `backend/.pytest-out/*`: subcadenas aleatorias dentro de hashes `sha512-…` y
  minificado (`qz`, `Qz`, `ury`) — falsos positivos, sin ninguna referencia real.
- `docs/database/seed.sql:116`: «(mesas/URY-style)» — cita de inspiración en la
  descripción de un parámetro. Se conserva como atribución honesta (no es código).

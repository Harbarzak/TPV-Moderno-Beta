# ADR-003 — Fiscalidad fuera de alcance; punto de extensión `FiscalAdapter`

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

Veri*Factu y TicketBAI existen en el legado como integraciones, pero **no se reconstruyen en este
proyecto por ahora**. La activación fiscal real se decidirá más adelante como plugin
independiente.

## Decisión

- No se diseña ni implementa ningún sistema fiscal.
- Se define **solo la interfaz vacía `FiscalAdapter`** con puntos de notificación para:
  venta cerrada, anulación y devolución. Sin implementación, sin configuración, sin UI.
- El motor de ventas la invoca tras el commit de la venta (post-commit, no bloqueante para la
  validez de la venta); las future implementaciones gestionarán sus propias colas/reintentos.

## Consecuencias

- El dominio de ventas queda preparado (inmutabilidad económica + eventos post-commit) para que
  el plugin fiscal futuro no exija rediseño.
- Cualquier requisito fiscal (numeración registral, huella, encadenamiento) vivirá en el plugin,
  no en el core.

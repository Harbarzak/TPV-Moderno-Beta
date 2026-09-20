# ADR-010 — Fiscalidad como plugin desacoplado (outbox de `sale_events`)

- **Estado:** Aceptada
- **Fecha:** 2026-09-14
- **Fase:** 34 · Fiscalidad (Veri*Factu / TicketBAI)
- **Sustituye parcialmente a:** [ADR-003](ADR-003-fiscal-adapter-fuera-de-alcance.md) (la interfaz vacía pasa a arquitectura real; se mantiene su principio: nada legal en el core)

## Contexto

ADR-003 dejó `FiscalAdapter` como interfaz vacía. La fase 34 la convierte en plugin real, con
una decisión de alcance del usuario (**2026-09-14: "ninguno aún"**): se construye la
arquitectura — consumo, documentos, traza, auditoría — pero **ningún adaptador legal se
desarrolla**. Veri*Factu y TicketBAI quedan como esqueletos seleccionables que rechazan con
`FISCAL_NOT_IMPLEMENTED`.

Marco legal verificado en fuentes oficiales a 2026-09-14 (detalle en
[`docs/fiscal/README.md`](../fiscal/README.md)):

- **Veri*Factu** (RD 1007/2023 + Orden HAC/1177/2024): aplazada por el RDL 15/2025 — SIF
  adaptado antes del **1-1-2027** (Impuesto sobre Sociedades) y **1-7-2027** (resto).
- **TicketBAI**: en vigor en Euskadi desde el **1-1-2026** (Bizkaia, Norma Foral 8/2023);
  tres regímenes hermanos, uno por Diputación.

## Decisión

1. **El motor de ventas no cambia.** La integración es por el evento genérico de venta ya
   emitido: el outbox `sale_events` (fase 06, `dispatched_at` como marcador de consumo).
   El plugin nunca invoca al motor ni recalcula importes (dinero siempre string, §3).
2. **Transformación idempotente evento→documento.** Un `sale_event` produce como máximo un
   `FiscalDocument` (`UNIQUE(sale_event_id)`). Reintentar o repetir la descarga nunca duplica.
3. **Traza append-only.** Cada paso del documento deja un `FiscalEvent`
   (queued/dispatched/accepted/rejected/cancelled). Los documentos no tienen `updated_at`:
   el histórico ES `fiscal_events`. Cada pasada de descarga deja además un resumen en
   `audit_log` (`action = 'fiscal.dispatch'`).
4. **Máquina de estados acotada.** `pending → sent → accepted | rejected`;
   `rejected → pending | cancelled`; `accepted` y `cancelled` terminales. Un fallo del
   adaptador deja el documento `pending` con `attempts+1` y el diagnóstico: el reintento
   vive a nivel de DOCUMENTO y jamás revierte la venta ya cobrada.
5. **Selección por configuración, fail-fast.** `TPV_FISCAL_PROVIDER = none | verifactu |
   ticketbai` resuelto en el arranque (`build_fiscal_adapter`); un valor erróneo tumba el
   despliegue, no la primera descarga. `none` consume eventos SIN certificar nada y no
   refiscaliza histórico.
6. **Disparo externo.** `POST /api/v1/fiscal/dispatch` (permiso `fiscal.dispatch`), idóneo
   para cron/panel; por ser outbox transaccional, ejecutarlo tarde o dos veces es seguro.
7. **Adaptadores reales futuros** vivirán en `app/adapters/fiscal/` sin tocar motor, dominio
   ni contrato: solo implementar `FiscalAdapter.submit`.

## Consecuencias

- Activar un régimen = cambiar una variable de entorno + desarrollar su adaptador; el resto
  (cola, idempotencia, traza, auditoría, API) ya existe.
- El checklist de activación (fuentes oficiales AEAT / Diputaciones) queda documentado en
  `docs/fiscal/README.md`; no se implementó nada legal sin verificar documentación vigente.
- Migración `0007_fiscal_plugin` (tablas `fiscal_documents`, `fiscal_events`; permisos
  `fiscal.view` / `fiscal.dispatch`), aditiva e inspector-guarded como las anteriores.

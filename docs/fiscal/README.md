# Fiscalidad — contexto legal verificado y checklist de activación

Este documento acompaña al plugin fiscal (fase 34, [ADR-010](../decisions/ADR-010-fiscal-plugin.md)).
**Regla de la fase 34:** no se implementa ningún requisito legal sin verificar la
documentación oficial vigente. Lo que sigue es el estado verificado a **2026-09-14** y las
fuentes donde re-verificarlo antes de desarrollar cualquier adaptador.

## Estado actual (decisión del usuario, 2026-09-14)

**Ningún régimen activo.** `TPV_FISCAL_PROVIDER=none`: el plugin consume los `sale_events`
sin certificar nada. Los adaptadores `verifactu` y `ticketbai` son esqueletos
seleccionables y auditables que rechazan con `FISCAL_NOT_IMPLEMENTED`.

## Veri*Factu (Estado / AEAT)

- Base: **RD 1007/2023** (requisitos de los SIF) y **Orden HAC/1177/2024**
  (especificaciones técnicas: encadenamiento de huella SHA-256, envío a AEAT, QR).
- **Plazos vigentes** (aplazados por el **RDL 15/2025**, de 2 de diciembre de 2025, según la
  nota informativa de AEAT): obligatorio para SIF adaptados antes del **1-1-2027**
  (contribuyentes del Impuesto sobre Sociedades) y del **1-7-2027** (resto). Voluntaria
  desde su aprobación. Sistema SIF obligatorio en 2028.
- Al reactivar: consultar la página oficial de Veri*Factu en `sede.agenciatributaria.gob.es`
  (especificaciones técnicas + entorno de pruebas/homologación) — nunca documentación de
  terceros como fuente única.

## TicketBAI (Euskadi / Diputaciones)

- En vigor en todo el territorio desde el **1-1-2026** (Bizkaia fue la última: **Norma Foral
  8/2023**, con el margen hasta esa fecha).
- **Tres regímenes hermanos, no uno**: decretos/normas propias de cada Diputación
  (Álava, Gipuzkoa, Bizkaia) con especificaciones técnicas distintas. Elementos comunes:
  código **TBAI** + **QR** en el ticket, ficheros batch **firmados XAdES** y depósito en la
  plataforma de la Diputación correspondiente.
- Al reactivar: escoger territorio (o los tres), y usar el decreto foral y la documentación
  técnica de ESA Diputación como fuente — no son intercambiables.

## Checklist de activación de cualquier régimen

1. Re-verificar vigencia y plazos en la fuente oficial (AEAT o Diputación), no en resúmenes.
2. Elegir `TPV_FISCAL_PROVIDER` y desplegar: el valor erróneo falla en el arranque.
3. Desarrollar SOLO el adaptador (`app/adapters/fiscal/`, implementar `FiscalAdapter.submit`):
   motor, dominio, outbox, idempotencia, traza y auditoría ya existen y no se tocan.
4. Definir numeración registral / encadenamiento que exija el régimen DENTRO del adaptador.
5. Probar contra el entorno de homologación oficial antes de producción.
6. Formar al personal en el flujo de rechazos: un documento `rejected` queda
   reencolable (`POST /fiscal/dispatch`) y trazado; `accepted` es terminal.

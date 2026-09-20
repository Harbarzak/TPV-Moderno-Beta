# ADR-006 — Offline por degradación explícita e idempotencia global

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

Un TPV debe sobrevivir a: caída de Internet (trivial: LAN-first), caída del servidor, de un
terminal, de un periférico o de la propia BD. El legado operaba 100 % local por puesto, pero a
coste de divergencia e inconsistencia.

## Decisión

- **Servidor = fuente de verdad.** El cliente no inventa numeración ni registra cobros cuando el
  servidor no responde.
- Drafts de venta viven en el cliente (Zustand + IndexedDB) con catálogo cacheado
  (`stale-while-revalidate`); al recuperar conectividad se reconcilian en orden contra el
  servidor.
- **Toda** operación de dinero lleva `Idempotency-Key`: reintentos jamás duplican tickets ni
  pagos.
- Numeración de tickets **por terminal** (series independientes) para evitar colisiones entre
  puestos.
- Modo degradado configurable: bloquear cobro sin servidor o permitir ticket manual de papel con
  regularización posterior (último recurso, herencia de práctica del legado).
- Postgres protegido con WAL archiving (RPO ≤ 5 min) y runbook de restauración (RTO ≤ 30 min),
  con restauraciones **probadas**.

## Consecuencias

- La fase 19 (Offline) construye sobre mecanismos ya presentes desde el MVP (idempotencia,
  series por terminal, eventos persistentes), no sobre un parche tardío.
- En caída de servidor no hay ventas digitales: se acepta explícitamente como política de
  producto, con alternativa de papel regulable.

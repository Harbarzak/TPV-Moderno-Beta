# ADR-001 — Stack tecnológico definitivo

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

Plataforma TPV cliente-servidor para operar en LAN, con clientes web (TPV, PWA camarero, KDS,
administración) y servidor propio en el local. Se partía de un legado Windows (SQL Server/Express
+ DBF) que no se continúa.

## Decisión

- Backend: **Python 3.12 + FastAPI + SQLAlchemy 2.x (async) + Alembic**.
- Base de datos: **PostgreSQL 16** (accesible solo desde el servidor).
- Frontend: **React 18 + TypeScript + Vite + Tailwind + Radix UI + Lucide + Zustand + Zod**,
  compartido por TPV, PWA y panel; **KDS como cliente React independiente**.
- Three.js prohibido en la pantalla de venta; solo dashboard/estadísticas si aporta valor real.

## Consecuencias

- Un único stack de UI para todo cliente menos KDS (despliegue y ciclo de vida separados).
- Dinero siempre `numeric(12,2)`/`Decimal`/string JSON; prohibido `float`.
- No se evalúan alternativas: decisión cerrada por el cliente para todo el proyecto.

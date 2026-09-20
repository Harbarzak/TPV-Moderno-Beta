# ADR-002 — Los clientes nunca acceden directamente a la base de datos

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

El legado exponía la BD a los puestos (drives DBF / SQL directo), con problemas de seguridad,
corrupción y acoplamiento. La nueva plataforma debe soportar LANs poco controladas y futuras
PWA móviles.

## Decisión

PostgreSQL queda **restringido al proceso servidor** (usuario exclusivo, sin escucha pública,
firewall de host). Todo cliente consume la API HTTPS/WebSocket del servidor. No hay vistas SQL en
clientes ni dobles caminos de escritura.

## Consecuencias

- Toda validación y regla de negocio es única y server-side.
- El cliente offline solo puede degradarse a drafts locales + reconciliación (ADR-006).
- Migraciones y cambios de esquema no rompen clientes desplegados (contrato = API versionada).

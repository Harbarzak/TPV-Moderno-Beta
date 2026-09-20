# ADR-005 — WebSocket como bus de eventos con replay, nunca como camino obligatorio

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

Varios consumidores necesitan tiempo real: KDS, estadísticas vivas, colas de impresión por
terminal, avisos de sistema. A la vez, un TPV no puede bloquearse si el WS falla.

## Decisión

- Un único endpoint `wss://…/api/v1/ws` sobre FastAPI; autenticación en primer mensaje.
- Suscripción explícita a **temas** (`terminal:{id}`, `sales`, `kds`, `cash`, `agent:{terminal}`,
  `system`).
- Cada evento se persiste en `EventLog` con `event_id` monotónico; al reconectar, el cliente pide
  replay con `since_event_id` y re-sincroniza agregados críticos por REST.
- Latido ping/pong 15 s; reconexión con backoff y jitter; consumidores idempotentes
  (duplicados de replay descartados por `job_id`/`event_id`).
- Publicación tras el commit de BD, vía `EventBus` abstraída (in-memory ahora, Redis posible
  después sin tocar servicios). Proceso único asumido a esta escala.

## Consecuencias

- El WS **acelera, nunca bloquea**: toda operación crítica es posible por REST alone.
- Sin pérdida de eventos ante caídas cortas (ventana de replay configurable).
- Escalado a múltiples procesos/servidores exigiría solo cambiar la implementación del bus.

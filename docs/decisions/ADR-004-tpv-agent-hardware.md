# ADR-004 — Periféricos locales mediante agente `tpv-agent`; impresión de red directa

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

Los navegadores no pueden hablar con USB/serie (impresoras locales, cajón, visor, pinpad).
El TPV legado era una app nativa con acceso directo; la nueva UI es web.

## Decisión

Dos rutas de hardware:

1. **Impresoras de red ESC/POS (TCP 9100):** el servidor imprime directamente; cola `PrintJob`
   persistente con estados y reintentos, y fallback a impresora secundaria.
2. **Periféricos locales:** demonio ligero **`tpv-agent`** (Python) en cada terminal TPV,
   autenticado como terminal (token de dispositivo), conectado por WSS al canal
   `agent:{terminal}` y con endpoint local de loopback (`localhost:9770`) como alternativa sin red.
   Ejecuta: imprimir, abrir cajón, y dialogar con el pinpad vía `PaymentTerminalAdapter`.
3. **CashDro:** solo interfaz `CashDropAdapter` reservada; **no se implementa ahora** (contexto
   histórico del legado).

## Consecuencias

- La UI web mantiene el modelo de seguridad del navegador; el hardware es responsabilidad del
  agente y del servidor, nunca del frontend.
- El agente es un componente versionado e instalable (fase 13) con actualización independiente.
- Los `PrintJob` son persistentes: una caída del agente no pierde impresiones (recupero de cola).

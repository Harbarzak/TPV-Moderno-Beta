# ADR-007 — URY solo como referencia de UX/producto (cumplimiento AGPL)

- **Estado:** Aceptada
- **Fecha:** 2026-09-11
- **Fase:** 00 · Arquitectura

## Contexto

El repositorio URY (github.com/ury-erp/ury) se usa como acelerador de UX/producto. Su licencia es
**AGPL**: copiar código contaminaría el proyecto con obligaciones de licencia indeseadas.

## Decisión

- URY se utiliza únicamente como **referencia de flujos, jerarquías de UI y patrones de
  producto** (fase 4 "Análisis URY").
- **Prohibido copiar código, estructuras de archivos ni textos** de URY al repositorio propio.
- Toda idea adoptada se reescribe desde cero con la documentación de esta arquitectura como
  fuente; las decisiones tomadas "por influencia de URY" se citan como referencia visual/UX, no
  como derivado de código.
- Si en el futuro se quisiera reutilizar código real de URY, requeriría una decisión formal nueva
  (cambio de licencia del proyecto o aislamiento por separación estricta), no una adopción
  silenciosa.

## Consecuencias

- Fase 4 produce un documento de hallazgos (capturas, flujos, decisiones de UX), nunca diffs de
  código.
- El proyecto conserva licencia y distribución propias.

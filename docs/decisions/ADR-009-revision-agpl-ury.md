# ADR-009 — Revisión AGPL de URY: cero código incorporado y checklist obligatorio para terceros

- **Estado:** Aceptada
- **Fecha:** 2026-09-14
- **Fase:** Revisión AGPL / reutilización URY

## Contexto

El proyecto usa URY (`ury-erp/ury`, AGPL-3.0) como acelerador de UX/producto desde la
fase 00 (ADR-007). Han pasado por influencia de URY la sidebar de categorías, el
semáforo de mesas, el filtro «recién cobradas», el KDS y las notas hacia cocina
(fases 26–32). Antes de incorporar cualquier código de URY procede verificar, con
evidencias, que no se ha copiado nada y fijar el procedimiento para el futuro.

## Decisión

1. **Se ratifica ADR-007**: URY es solo referencia de UX/producto. La verificación del
   2026-09-14 (`docs/agpl/REVISION-AGPL-2026-09-14.md`) confirma **cero código AGPL
   incorporado**: ninguna dependencia de su stack (`socket.io`, `qz-tray`, `jsrsasign`,
   `@ury/*`, Frappe) en las 4 SPA ni en el backend; todos los patrones adoptados están
   reimplementados desde cero. El proyecto no contrae obligaciones AGPL.
2. **Checklist obligatorio** antes de incorporar cualquier código de terceros:
   1) fichero origen · 2) licencia · 3) dependencias que arrastra · 4) decisión
   copiar/modificar/estudiar · 5) procedencia documentada · 6) obligaciones evaluadas ·
   7) registro en ADR. Si hay dudas sobre la licencia o la procedencia, se implementa
   **una versión propia inspirada en su comportamiento**, nunca se copia.
3. Las piezas futuras de URY (enrutado de impresoras por sala/unidad, motivo de KOT en
   el payload, cliente+pax+favoritos) se incorporarán como **implementación propia** y
   se añadirán como fila al inventario del documento de revisión.

## Consecuencias

- Licencia y distribución del proyecto siguen siendo propias; ningún fichero del repo
  es derivado de código AGPL.
- La adopción de ideas UX no requiere ADR nuevo por pieza; sí lo requiere copiar
  **código** de terceros, que queda sujeto al checklist y a decisión formal.
- El documento de revisión es vivo: cada nueva adopción estudiada añade su fila con
  fecha y evidencia.

/**
 * Visibilidad de secciones: los permisos solo filtran el menú; la
 * autoridad real sigue siendo el 403 del backend.
 */

import { describe, expect, it } from 'vitest';
import { SECTIONS, can, sectionsFor } from './permissions';

describe('sectionsFor', () => {
  it('con todos los permisos muestra las 14 secciones', () => {
    const all = SECTIONS.map((section) => section.permission);
    expect(sectionsFor(all)).toHaveLength(SECTIONS.length);
  });

  it('solo products.view habilita el bloque de catálogo', () => {
    const ids = sectionsFor(['products.view']).map((section) => section.id);
    expect(ids).toEqual(['productos', 'categorias', 'departamentos', 'paneles']);
  });

  it('sin permisos no se ve ninguna sección', () => {
    expect(sectionsFor([])).toEqual([]);
  });

  it('un permiso compartido habilita varias secciones', () => {
    const ids = sectionsFor(['admin.terminals']).map((section) => section.id);
    expect(ids).toEqual(['terminales', 'dispositivos']);
  });
});

describe('can', () => {
  it('exige coincidencia exacta, sin prefijos', () => {
    expect(can(['products.view'], 'products.view')).toBe(true);
    expect(can(['products.view'], 'products.edit')).toBe(false);
    expect(can(['products.view'], 'products.view.extra')).toBe(false);
    expect(can([], 'admin.audit')).toBe(false);
  });
});

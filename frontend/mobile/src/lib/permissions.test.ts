import { describe, expect, it } from 'vitest';
import { can, canCloseCash, canMoveCash, sectionsFor, sellingActionsFor } from './permissions';

const WAITER = ['sales.sell', 'products.view', 'cash.open'];
const MANAGER = [...WAITER, 'payments.take', 'sales.void', 'cash.movements', 'cash.close', 'reports.view'];

describe('can', () => {
  it('comprueba pertenencia exacta', () => {
    expect(can(WAITER, 'sales.sell')).toBe(true);
    expect(can(WAITER, 'sales.void')).toBe(false);
  });
});

describe('sectionsFor — visibilidad por permisos', () => {
  it('camarero: venta y caja, sin histórico', () => {
    const sections = sectionsFor(WAITER);
    expect(sections).toEqual({
      selling: true,
      products: true,
      cash: true,
      statsShift: true,
      statsHistory: false,
    });
  });

  it('sin permisos, nada visible', () => {
    expect(Object.values(sectionsFor([])).every((value) => value === false)).toBe(true);
  });
});

describe('acciones finas', () => {
  it('cobrar/anular/movimientos solo con su permiso', () => {
    expect(sellingActionsFor(WAITER)).toEqual({ addLines: true, charge: false, voidOrder: false });
    expect(sellingActionsFor(MANAGER).charge).toBe(true);
    expect(canMoveCash(WAITER)).toBe(false);
    expect(canMoveCash(MANAGER)).toBe(true);
    expect(canCloseCash(WAITER)).toBe(false);
    expect(canCloseCash(MANAGER)).toBe(true);
  });
});

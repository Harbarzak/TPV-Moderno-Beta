import { describe, expect, it } from 'vitest';
import {
  UNIT_MILLI,
  formatMoney,
  formatQty,
  lineTotal,
  parseMoney,
  parseQty,
  ticketTotals,
} from './money';

describe('dinero en céntimos exactos (§3)', () => {
  it('convierte string del contrato a BigInt y viceversa', () => {
    expect(parseMoney('1.50')).toBe(150n);
    expect(parseMoney('0.05')).toBe(5n);
    expect(parseMoney('123456789.99')).toBe(12_345_678_999n);
    expect(formatMoney(150n)).toBe('1.50');
    expect(formatMoney(5n)).toBe('0.05');
    expect(formatMoney(0n)).toBe('0.00');
  });

  it('rechaza todo lo que no sea string con 2 decimales', () => {
    expect(() => parseMoney('1.5')).toThrow();
    expect(() => parseMoney('1,50')).toThrow();
    expect(() => parseMoney('-1.50')).toThrow();
    expect(() => parseMoney('abc')).toThrow();
    // un float JS jamás entra: la firma solo acepta string
  });

  it('lineaTotal redondea half-up al céntimo (pesables)', () => {
    // 0.01 € × 1.5 unidades = 0.015 → 0.02
    expect(lineTotal(parseMoney('0.01'), 1_500)).toBe(2n);
    // 2.30 € × 3 unidades = 6.90 exacto
    expect(lineTotal(parseMoney('2.30'), 3 * UNIT_MILLI)).toBe(690n);
  });
});

describe('cantidades en mili-unidades', () => {
  it('parsea con coma o punto y hasta 3 decimales', () => {
    expect(parseQty('1.25')).toBe(1_250);
    expect(parseQty('1,25')).toBe(1_250);
    expect(parseQty('0.125')).toBe(125);
    expect(parseQty('2')).toBe(2_000);
  });

  it('rechaza cantidades imposibles', () => {
    expect(() => parseQty('1.2345')).toThrow();
    expect(() => parseQty('-1')).toThrow();
    expect(() => parseQty('abc')).toThrow();
  });

  it('formatQty quita los ceros sobrantes', () => {
    expect(formatQty(1_000)).toBe('1');
    expect(formatQty(1_250)).toBe('1.25');
    expect(formatQty(125)).toBe('0.125');
  });
});

describe('totales del ticket con PVP con IVA incluido', () => {
  it('desglosa el tramo general 21 %', () => {
    const totals = ticketTotals([
      { price: '1.21', taxCode: 'general', taxRate: '21.00', qtyMilli: 1_000 },
    ]);
    expect(totals.total).toBe(121n);
    expect(totals.base).toBe(100n);
    expect(totals.tax).toBe(21n);
    expect(totals.slices).toHaveLength(1);
    expect(totals.slices[0]).toMatchObject({ taxCode: 'general', taxRate: '21.00' });
  });

  it('suma líneas y agrupa por tipo de IVA sin flotar un céntimo', () => {
    const totals = ticketTotals([
      { price: '1.10', taxCode: 'reducido', taxRate: '10.00', qtyMilli: 1_000 },
      { price: '1.10', taxCode: 'reducido', taxRate: '10.00', qtyMilli: 1_000 },
      { price: '2.00', taxCode: 'superreducido', taxRate: '4.00', qtyMilli: 2_000 },
    ]);
    expect(totals.total).toBe(620n);
    // base+tax === total tramo a tramo y en el agregado
    expect(totals.slices.map((s) => s.base + s.tax)).toEqual(totals.slices.map((s) => s.total));
    expect(totals.base + totals.tax).toBe(totals.total);
  });

  it('ticket vacío', () => {
    expect(ticketTotals([])).toMatchObject({ total: 0n, base: 0n, tax: 0n, slices: [] });
  });
});

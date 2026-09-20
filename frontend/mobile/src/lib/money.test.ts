import { describe, expect, it } from 'vitest';
import { formatMoney, isMoney, parseAmount } from './money';

describe('isMoney (contrato §3)', () => {
  it('acepta strings «12.34»', () => {
    expect(isMoney('0.00')).toBe(true);
    expect(isMoney('1234567890.99')).toBe(true);
  });

  it('rechaza floats, negativos, comas y excesos', () => {
    expect(isMoney(12.34)).toBe(false);
    expect(isMoney('-1.00')).toBe(false);
    expect(isMoney('12,34')).toBe(false);
    expect(isMoney('12.3')).toBe(false);
    expect(isMoney('12345678901.00')).toBe(false);
  });
});

describe('formatMoney', () => {
  it('formatea a es-ES', () => {
    expect(formatMoney('12.34')).toBe('12,34 €');
    expect(formatMoney('1234.00')).toBe('1.234,00 €');
  });

  it('marca el contrato roto en vez de esconderlo', () => {
    expect(formatMoney('12.345')).toMatch(/^⚠/);
  });
});

describe('parseAmount (solo sintaxis)', () => {
  it('normaliza coma y decimales parciales', () => {
    expect(parseAmount('1,5')).toBe('1.50');
    expect(parseAmount(' 2 ')).toBe('2.00');
    expect(parseAmount('3.25')).toBe('3.25');
    expect(parseAmount('0,05')).toBe('0.05');
  });

  it('rechaza lo que no es importe', () => {
    expect(parseAmount('-1')).toBeNull();
    expect(parseAmount('1.234')).toBeNull();
    expect(parseAmount('abc')).toBeNull();
    expect(parseAmount('')).toBeNull();
  });
});

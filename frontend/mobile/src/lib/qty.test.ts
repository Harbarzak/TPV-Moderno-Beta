import { describe, expect, it } from 'vitest';
import { formatQty, toQty } from './qty';

describe('toQty', () => {
  it('acepta enteros y decimales con coma', () => {
    expect(toQty('1')).toBe('1.000');
    expect(toQty('0,850')).toBe('0.850');
    expect(toQty('12,5')).toBe('12.500');
  });

  it('rechaza lo que aún no es una cantidad positiva válida', () => {
    expect(toQty('')).toBeNull();
    expect(toQty('0')).toBeNull();
    expect(toQty('0,000')).toBeNull();
    expect(toQty('1,2345')).toBeNull(); // más de 3 decimales
    expect(toQty('abc')).toBeNull();
  });
});

describe('formatQty', () => {
  it('recorta los decimales vanos y usa coma', () => {
    expect(formatQty('1.000')).toBe('1');
    expect(formatQty('0.500')).toBe('0,5');
    expect(formatQty('2.250')).toBe('2,25');
  });
});

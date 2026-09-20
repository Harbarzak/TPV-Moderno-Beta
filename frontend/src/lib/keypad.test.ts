import { describe, expect, it } from 'vitest';
import { amountToCents, centsToBuffer, pressAmount } from './keypad';

describe('teclado numérico: edición del búfer (§4)', () => {
  it('clear vacía y back borra la última cifra (sin pasarse en vacío)', () => {
    expect(pressAmount('12,4', 'clear')).toBe('');
    expect(pressAmount('12,4', 'back')).toBe('12,');
    expect(pressAmount('12,', 'back')).toBe('12');
    expect(pressAmount('', 'back')).toBe('');
  });

  it('la coma es única y el cero inicial se sustituye por la primera cifra', () => {
    expect(pressAmount('', ',')).toBe(',');
    expect(pressAmount('12', ',')).toBe('12,');
    expect(pressAmount('12,', ',')).toBe('12,');
    expect(pressAmount('0', '5')).toBe('5');
    expect(pressAmount('0', '0')).toBe('0');
  });

  it('solo caben 2 decimales: el resto de teclas no añade nada', () => {
    expect(pressAmount('12,3', '5')).toBe('12,35');
    expect(pressAmount('12,3', '00')).toBe('12,30');
    expect(pressAmount('12,34', '5')).toBe('12,34');
    expect(pressAmount('12,34', '00')).toBe('12,34');
  });

  it('la parte entera respeta el máximo del contrato de dinero (7 cifras)', () => {
    expect(pressAmount('1234567', '5')).toBe('1234567');
    expect(pressAmount('123456', '7')).toBe('1234567');
    // La coma sigue colando: los decimales tienen su propio hueco.
    expect(pressAmount('1234567', ',')).toBe('1234567,');
  });
});

describe('teclado numérico: búfer ⇄ céntimos (bigint, sin floats)', () => {
  it('amountToCents convierte con exactidad; vacío o malformado es null', () => {
    expect(amountToCents('')).toBeNull();
    expect(amountToCents(',')).toBeNull();
    expect(amountToCents('5')).toBe(500n);
    expect(amountToCents('12,')).toBe(1_200n);
    expect(amountToCents('12,4')).toBe(1_240n);
    expect(amountToCents('12,40')).toBe(1_240n);
    expect(amountToCents(',5')).toBe(50n);
    expect(amountToCents('9999999,99')).toBe(9_999_999_99n);
  });

  it('centsToBuffer pinta el búfer que verá el operador', () => {
    expect(centsToBuffer(1_240n)).toBe('12,40');
    expect(centsToBuffer(5n)).toBe('0,05');
    expect(centsToBuffer(0n)).toBe('0,00');
    expect(centsToBuffer(1_000_000n)).toBe('10000,00');
  });
});

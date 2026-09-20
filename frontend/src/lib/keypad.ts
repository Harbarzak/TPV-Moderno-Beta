/**
 * Lógica del teclado numérico propio (design-system.md §4: en táctil NUNCA el
 * teclado del SO). El búfer es la cadena que ve el operador («12,4») y los
 * céntimos se derivan de ella con exactidad bigint — nada de parseFloat.
 *
 * Reglas: coma única, 2 decimales como mucho, parte entera limitada al máximo
 * del contrato de dinero (9999999.99, ARCHITECTURE.md §3). El «0» inicial se
 * sustituye por la primera cifra; «00» respeta ambos topes.
 */

export type KeypadKey =
  | '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9'
  | '00' | ',' | 'back' | 'clear';

/** Máximo del contrato: 9999999.99 → 7 cifras enteras. */
const MAX_INTEGER_DIGITS = 7;
const MAX_DECIMALS = 2;

export function pressAmount(current: string, key: KeypadKey): string {
  if (key === 'clear') return '';
  if (key === 'back') return current.slice(0, -1);

  if (key === ',') {
    return current.includes(',') ? current : `${current},`;
  }

  // Cifras ('5') y doble cero ('00').
  if (current === '0' && key !== '00') return key; // «0» + «5» → «5»
  const [integer, decimals] = splitBuffer(current);
  if (decimals !== null) {
    // Ya hay coma: solo caben los decimales que falten.
    const room = Math.max(0, MAX_DECIMALS - decimals.length);
    if (room === 0) return current;
    return current + key.slice(0, room);
  }
  const room = Math.max(0, MAX_INTEGER_DIGITS - integer.length);
  if (room === 0) return current;
  return current + key.slice(0, room);
}

/** Parte entera y decimales (null si aún no hay coma) del búfer. */
function splitBuffer(buffer: string): [string, string | null] {
  const index = buffer.indexOf(',');
  if (index === -1) return [buffer, null];
  return [buffer.slice(0, index), buffer.slice(index + 1)];
}

/** Búfer → céntimos; cadena vacía o malformada → null (el caller decide). */
export function amountToCents(buffer: string): bigint | null {
  if (buffer === '' || buffer === ',') return null;
  const [integer, decimals] = splitBuffer(buffer);
  if (integer === '' && (decimals === null || decimals === '')) return null;
  if (!/^\d*$/.test(integer) || (decimals !== null && !/^\d*$/.test(decimals))) return null;
  const padded = (decimals ?? '').padEnd(2, '0').slice(0, 2);
  return BigInt(`${integer === '' ? '0' : integer}${padded}`);
}

/** Céntimos → búfer (para prefijar el importe pendiente, el exacto…). */
export function centsToBuffer(cents: bigint): string {
  const negative = cents < 0n;
  const absolute = negative ? -cents : cents;
  const integer = absolute / 100n;
  const decimals = (absolute % 100n).toString().padStart(2, '0');
  return `${negative ? '-' : ''}${integer},${decimals}`;
}

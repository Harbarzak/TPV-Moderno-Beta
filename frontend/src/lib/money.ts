/**
 * Dinero y cantidades del TPV (ARCHITECTURE.md §3 y §4.1).
 *
 * El dinero viaja SIEMPRE como string con 2 decimales (nunca float, ni en el
 * JSON ni en el cliente). Aquí se convierte a céntimos exactos con BigInt y no
 * se vuelve a tocar con coma flotante. Las cantidades van en mili-unidades
 * enteras: los productos pesables admiten 3 decimales (kg) y los unitarios son
 * múltiplos de 1.000.
 */

export type Cents = bigint;

/** 1 unidad = 1.000 mili-unidades. */
export const UNIT_MILLI = 1_000;

const MONEY_RE = /^\d{1,10}(\.\d{2})?$/;
const QTY_RE = /^\d{1,6}([.,]\d{1,3})?$/;

/** "1.50" → 150n. Rechaza floats y formatos fuera de contrato. */
export function parseMoney(value: string): Cents {
  if (!MONEY_RE.test(value)) {
    throw new Error(`Importe fuera de contrato: ${JSON.stringify(value)}`);
  }
  const [whole, dec = ""] = value.split(".");
  return BigInt(whole + dec.padEnd(2, "0"));
}

/** 150n → "1.50". */
export function formatMoney(cents: Cents): string {
  const whole = cents / 100n;
  const dec = (cents % 100n).toString().padStart(2, "0");
  return `${whole}.${dec}`;
}

/** "1.25" (o "1,25" de teclado numérico) → 1250 mili-unidades. */
export function parseQty(text: string): number {
  const normalized = text.replace(",", ".");
  if (!QTY_RE.test(normalized)) {
    throw new Error(`Cantidad no válida: ${JSON.stringify(text)}`);
  }
  const [whole, dec = ""] = normalized.split(".");
  return Number(whole + dec.padEnd(3, "0"));
}

/** 1250 → "1.25"; 1000 → "1". */
export function formatQty(milli: number): string {
  const whole = Math.floor(milli / UNIT_MILLI);
  const dec = String(milli % UNIT_MILLI).padStart(3, "0").replace(/0+$/, "");
  return dec ? `${whole}.${dec}` : `${whole}`;
}

/** Importe de línea: precio × cantidad con redondeo half-up al céntimo. */
export function lineTotal(price: Cents, qtyMilli: number): Cents {
  const raw = price * BigInt(qtyMilli); // céntimos × mili-unidades
  return (raw + 500n) / 1_000n;
}

/** Porcentaje de contrato ("10.00", "10,5") → puntos básicos exactos. */
export function pctToBp(pct: string): bigint {
  const normalized = pct.replace(",", ".");
  if (!/^\d{1,3}(\.\d{1,2})?$/.test(normalized)) {
    throw new Error(`Porcentaje fuera de contrato: ${JSON.stringify(pct)}`);
  }
  const [whole, dec = ""] = normalized.split(".");
  const bp = BigInt(whole) * 100n + BigInt(dec.padEnd(2, "0").slice(0, 2));
  if (bp > 10_000n) throw new Error(`Porcentaje fuera de rango: ${JSON.stringify(pct)}`);
  return bp;
}

/**
 * Importe de línea CON descuento, fórmula idéntica al backend
 * (app/domain/sales.py: gross → round_half_up(gross·(1e4−bp), 1e4)). El cobro
 * exige suma EXACTA al total del servidor: aquí no cabe una discrepancia de
 * redondeo ni de un céntimo.
 */
export function lineTotalDiscounted(price: Cents, qtyMilli: number, discountPct: string): Cents {
  const gross = lineTotal(price, qtyMilli);
  const bp = pctToBp(discountPct === "" ? "0.00" : discountPct);
  if (bp === 0n) return gross;
  return (gross * (10_000n - bp) + 5_000n) / 10_000n;
}

/**
 * Totales del ticket. El PVP incluye IVA (precio de venta al público): el total
 * es la suma de líneas y la base se obtiene por cociente exacto con BigInt;
 * el IVA de cada tramo es total − base (el desglose definitivo lo hace el
 * backend al cobrar, fase de venta).
 */
export interface TaxSlice {
  taxCode: string;
  taxRate: string; // "21.00"
  base: Cents;
  tax: Cents;
  total: Cents;
}

export interface TicketLine {
  price: string;
  taxCode: string;
  taxRate: string;
  qtyMilli: number;
  /** "10.00" = 10 % de descuento; ausente o "0.00" = sin descuento. */
  discountPct?: string;
}

export interface TicketTotals {
  total: Cents;
  base: Cents;
  tax: Cents;
  slices: TaxSlice[];
}

export function ticketTotals(lines: readonly TicketLine[]): TicketTotals {
  let total = 0n;
  const byRate = new Map<string, TaxSlice>();
  for (const line of lines) {
    const amount = lineTotalDiscounted(
      parseMoney(line.price),
      line.qtyMilli,
      line.discountPct ?? "0.00",
    );
    total += amount;
    let slice = byRate.get(line.taxRate);
    if (!slice) {
      slice = { taxCode: line.taxCode, taxRate: line.taxRate, base: 0n, tax: 0n, total: 0n };
      byRate.set(line.taxRate, slice);
    }
    slice.total += amount;
  }
  const slices = [...byRate.values()].sort((a, b) => a.taxRate.localeCompare(b.taxRate));
  let base = 0n;
  for (const slice of slices) {
    // base = total · 1e6 / (1e6 + ppm) con redondeo half-up (ppm = partes por millón).
    const ppm = parseMoney(slice.taxRate) * 100n;
    const denom = 1_000_000n + ppm;
    slice.base = (slice.total * 1_000_000n + denom / 2n) / denom;
    slice.tax = slice.total - slice.base;
    base += slice.base;
  }
  return { total, base, tax: total - base, slices };
}

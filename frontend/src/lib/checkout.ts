/**
 * Lógica pura del cobro (fase TPV visual): reparto de pagos mixtos, validación
 * de entregado en efectivo y construcción de los payloads de contrato para
 * /sales (OrderCreate → líneas → close). El componente (CheckoutDialog) solo
 * orquesta; las reglas viven aquí, probadas sin render.
 *
 * Dinero SIEMPRE en céntimos bigint (ARCHITECTURE.md §3): el backend exige la
 * suma de pagos EXACTA al total del pedido.
 */

import { formatMoney, pctToBp } from './money';

export type PaymentKindApi = 'cash' | 'card' | 'voucher' | 'credit' | 'other';

export interface PaymentMethodInfo {
  id: string;
  code: string;
  name: string;
  kind: PaymentKindApi;
  opens_drawer: boolean;
}

/** Pago ya apuntado en el diálogo, en céntimos. */
export interface StagedPayment {
  methodId: string;
  methodName: string;
  methodKind: PaymentKindApi;
  amountCents: bigint;
  /** Solo efectivo: lo entregado de verdad (≥ importe); el cambio lo calcula el backend. */
  tenderedCents: bigint | null;
}

export function pendingCents(totalCents: bigint, payments: StagedPayment[]): bigint {
  const paid = payments.reduce((sum, payment) => sum + payment.amountCents, 0n);
  return totalCents - paid;
}

export type AddPaymentVerdict = { ok: true } | { ok: false; reason: string };

/**
 * Reglas antes de apuntar un pago: importe positivo, sin pasarse del
 * pendiente y, en efectivo, entregado ≥ importe (el cambio es del backend).
 */
export function validatePayment(
  totalCents: bigint,
  payments: StagedPayment[],
  candidate: StagedPayment,
): AddPaymentVerdict {
  // Un importe cero solo vale si el ticket vale cero (descuento del 100 %):
  // el close exige la suma EXACTA y ahí el pago simbólico es necesario.
  if (candidate.amountCents <= 0n && totalCents !== 0n) {
    return { ok: false, reason: 'El importe del pago debe ser mayor que cero' };
  }
  const pending = pendingCents(totalCents, payments);
  if (candidate.amountCents > pending) {
    return {
      ok: false,
      reason: `El importe supera lo pendiente (${formatMoney(pending)} €)`,
    };
  }
  if (
    candidate.methodKind === 'cash' &&
    candidate.tenderedCents !== null &&
    candidate.tenderedCents < candidate.amountCents
  ) {
    return { ok: false, reason: 'Lo entregado no puede ser menor que el importe' };
  }
  return { ok: true };
}

/** Línea del carrito lista para POST /sales/orders/{id}/lines (contrato dp3/dp2). */
export interface LinePayload {
  product_id: string;
  quantity: string;
  discount_pct: string;
}

/** Quantity del contrato: exactamente 3 decimales desde milis («1500 → "1.500"»). */
export function qtyToContract(qtyMilli: number): string {
  const whole = Math.trunc(qtyMilli / 1000);
  const milli = qtyMilli - whole * 1000;
  return `${whole}.${Math.abs(milli).toString().padStart(3, '0')}`;
}

/** RatePercent del contrato: exactamente 2 decimales («10,5» → "10.50"). */
export function rateToContract(pct: string): string {
  const bp = pctToBp(pct === '' ? '0.00' : pct);
  const whole = bp / 100n;
  const rest = (bp % 100n).toString().padStart(2, '0');
  return `${whole}.${rest}`;
}

export function buildLinePayloads(lines: { id: string; qtyMilli: number; discountPct: string }[]): LinePayload[] {
  return lines.map((line) => ({
    product_id: line.id,
    quantity: qtyToContract(line.qtyMilli),
    discount_pct: rateToContract(line.discountPct),
  }));
}

/** Pagos apuntados → cuerpo del close (tendered SOLO en efectivo, §3 string). */
export function buildClosePayments(payments: StagedPayment[]): {
  payment_method_id: string;
  amount: string;
  tendered?: string;
}[] {
  return payments.map((payment) => ({
    payment_method_id: payment.methodId,
    amount: formatMoney(payment.amountCents),
    ...(payment.methodKind === 'cash' && payment.tenderedCents !== null
      ? { tendered: formatMoney(payment.tenderedCents) }
      : {}),
  }));
}

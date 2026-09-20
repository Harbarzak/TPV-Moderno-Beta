import { describe, expect, it } from 'vitest';
import {
  buildClosePayments,
  buildLinePayloads,
  pendingCents,
  qtyToContract,
  rateToContract,
  validatePayment,
  type StagedPayment,
} from './checkout';

const CASH: Omit<StagedPayment, 'amountCents' | 'tenderedCents'> = {
  methodId: 'm-cash',
  methodName: 'Efectivo',
  methodKind: 'cash',
};
const CARD: Omit<StagedPayment, 'amountCents' | 'tenderedCents'> = {
  methodId: 'm-card',
  methodName: 'Tarjeta',
  methodKind: 'card',
};

const cash = (amountCents: bigint, tenderedCents: bigint | null = null): StagedPayment => ({
  ...CASH,
  amountCents,
  tenderedCents,
});

describe('reparto de pagos mixtos', () => {
  it('pendingCents resta lo ya apuntado del total', () => {
    expect(pendingCents(1_000n, [])).toBe(1_000n);
    expect(pendingCents(1_000n, [cash(400n), { ...CARD, amountCents: 600n, tenderedCents: null }])).toBe(0n);
    // La sobre-pasada es posible a nivel de función pura: la valida validatePayment.
    expect(pendingCents(1_000n, [cash(1_200n)])).toBe(-200n);
  });
});

describe('validatePayment: reglas antes de apuntar un pago', () => {
  it('acepta un pago parcial en tarjeta', () => {
    expect(
      validatePayment(1_000n, [], { ...CARD, amountCents: 400n, tenderedCents: null }),
    ).toEqual({ ok: true });
  });

  it('rechaza importe cero o negativo… salvo cuando el ticket vale cero (100 % dto.)', () => {
    expect(validatePayment(1_000n, [], { ...CARD, amountCents: 0n, tenderedCents: null })).toEqual({
      ok: false,
      reason: 'El importe del pago debe ser mayor que cero',
    });
    expect(validatePayment(0n, [], { ...CARD, amountCents: 0n, tenderedCents: null })).toEqual({
      ok: true,
    });
  });

  it('rechaza pasarse del pendiente, con el importe en el motivo', () => {
    const verdict = validatePayment(1_000n, [cash(300n)], { ...CARD, amountCents: 800n, tenderedCents: null });
    expect(verdict).toEqual({ ok: false, reason: 'El importe supera lo pendiente (7.00 €)' });
  });

  it('en efectivo, lo entregado no puede ser menor que el importe', () => {
    expect(validatePayment(1_000n, [], cash(1_000n, 500n))).toEqual({
      ok: false,
      reason: 'Lo entregado no puede ser menor que el importe',
    });
    expect(validatePayment(1_000n, [], cash(1_000n, 5_000n))).toEqual({ ok: true });
  });
});

describe('contratos del backend (§3: dp3 en cantidad, dp2 en porcentaje y dinero)', () => {
  it('qtyToContract: mili-unidades → string con 3 decimales exactos', () => {
    expect(qtyToContract(1_000)).toBe('1.000');
    expect(qtyToContract(350)).toBe('0.350');
    expect(qtyToContract(1_250)).toBe('1.250');
    expect(qtyToContract(62)).toBe('0.062');
  });

  it('rateToContract: porcentaje del teclado → contrato dp2 vía puntos básicos', () => {
    expect(rateToContract('')).toBe('0.00');
    expect(rateToContract('0')).toBe('0.00');
    expect(rateToContract('10')).toBe('10.00');
    expect(rateToContract('7,5')).toBe('7.50');
    expect(rateToContract('12,34')).toBe('12.34');
  });

  it('buildLinePayloads serializa las líneas del carrito', () => {
    expect(
      buildLinePayloads([
        { id: 'p-cafe', qtyMilli: 2_000, discountPct: '0.00' },
        { id: 'p-jamon', qtyMilli: 350, discountPct: '10.00' },
      ]),
    ).toEqual([
      { product_id: 'p-cafe', quantity: '2.000', discount_pct: '0.00' },
      { product_id: 'p-jamon', quantity: '0.350', discount_pct: '10.00' },
    ]);
  });

  it('buildClosePayments: tendered SOLO en efectivo y dinero como string contrato', () => {
    const payments = buildClosePayments([
      cash(1_000n, 5_000n),
      { ...CARD, amountCents: 500n, tenderedCents: null },
    ]);
    expect(payments).toEqual([
      { payment_method_id: 'm-cash', amount: '10.00', tendered: '50.00' },
      { payment_method_id: 'm-card', amount: '5.00' },
    ]);
    expect('tendered' in payments[1]).toBe(false);
  });
});

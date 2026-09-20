import { describe, expect, it } from 'vitest';
import { orderSchema, posProductSchema, cashReportSchema } from './schemas';

// Muestras calcadas de las respuestas pydantic del backend (format «f»).
const UUID = '0b6e4a5e-8a3e-4d0a-9a5e-1c2b3a4d5e6f';

describe('orderSchema', () => {
  it('acepta el contrato real del backend', () => {
    expect(() =>
      orderSchema.parse({
        id: UUID,
        terminal_id: UUID,
        user_id: UUID,
        cash_session_id: null,
        customer_id: null,
        dining_table_id: null,
        status: 'draft',
        order_type: 'bar',
        guest_count: null,
        note: null,
        total_base: '10.00',
        total_tax: '2.10',
        total_amount: '12.10',
        tax_summary: null,
        paid_at: null,
        voided_at: null,
        void_reason: null,
        created_at: '2026-09-12T10:00:00Z',
      }),
    ).not.toThrow();
  });

  it('rechaza el total como float (§3)', () => {
    expect(() =>
      orderSchema.parse({
        id: UUID,
        terminal_id: UUID,
        user_id: UUID,
        cash_session_id: null,
        customer_id: null,
        dining_table_id: null,
        status: 'draft',
        order_type: 'bar',
        guest_count: null,
        note: null,
        total_base: null,
        total_tax: null,
        total_amount: 12.1,
        tax_summary: null,
        paid_at: null,
        voided_at: null,
        void_reason: null,
        created_at: '2026-09-12T10:00:00Z',
      }),
    ).toThrow();
  });
});

describe('posProductSchema', () => {
  it('acepta la fila POS con precio string y cantidades de catálogo', () => {
    expect(() =>
      posProductSchema.parse({
        id: UUID,
        name: 'Café',
        short_name: 'CAF',
        sku: 'CAF-1',
        category_id: null,
        department_id: null,
        tax_code: 'GEN',
        tax_rate: '10.00',
        price: '1.50',
        weighable: false,
        kitchen: false,
        sort_order: 0,
        barcodes: ['8400000000017'],
        tier_prices: [],
      }),
    ).not.toThrow();
  });

  it('rechaza tax_rate fuera de contrato', () => {
    expect(() =>
      posProductSchema.parse({
        id: UUID,
        name: 'Café',
        short_name: null,
        sku: null,
        category_id: null,
        department_id: null,
        tax_code: 'GEN',
        tax_rate: '10',
        price: '1.50',
        weighable: false,
        kitchen: false,
        sort_order: 0,
        barcodes: [],
        tier_prices: [],
      }),
    ).toThrow();
  });
});

describe('cashReportSchema', () => {
  it('acepta el informe X completo', () => {
    expect(() =>
      cashReportSchema.parse({
        session: {
          id: UUID,
          terminal_id: UUID,
          opened_by: UUID,
          closed_by: null,
          opening_amount: '50.00',
          expected_amount: null,
          counted_amount: null,
          difference: null,
          status: 'open',
          opened_at: '2026-09-12T08:00:00Z',
          closed_at: null,
        },
        opening_amount: '50.00',
        cash_in: '0.00',
        cash_out: '0.00',
        cash_sales: '12.10',
        cash_refunds: '0.00',
        expected_cash: '62.10',
        difference: null,
        method_totals: [
          {
            code: 'CASH',
            kind: 'cash',
            sales_total: '12.10',
            sales_count: 1,
            refunds_total: '0.00',
            refunds_count: 0,
          },
        ],
        movements: [],
        counts: [],
      }),
    ).not.toThrow();
  });
});

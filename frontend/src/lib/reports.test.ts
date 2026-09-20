/**
 * Tests del contrato de informes (fase 15): Zod contra payloads de ejemplo del
 * backend y construcción de la query (from/to obligatorios, paginación).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fetchByPeriod,
  fetchTickets,
  invoiceParams,
  rangeParams,
  summarySchema,
  ticketPageSchema,
  closurePageSchema,
} from './reports';

const UUID = '0b6c1a34-7c4f-4a3e-9f0b-2f1d3a5b6c7d';
const UUID2 = '1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('esquema del resumen', () => {
  it('valida el payload completo con devolución en negativo (§3)', () => {
    const parsed = summarySchema.parse({
      sales_count: 2,
      sales_amount: '4.40',
      refunds_count: 1,
      refunds_amount: '-1.10',
      voided_count: 1,
      net_amount: '3.30',
      average_ticket: '2.20',
      tax_breakdown: [{ tax_rate: '10.00', base: '3.00', total: '3.30' }],
    });
    expect(parsed.net_amount).toBe('3.30');
    expect(parsed.tax_breakdown[0].tax_rate).toBe('10.00');
  });

  it('rechaza un importe float (deriva del §3)', () => {
    expect(() =>
      summarySchema.parse({
        sales_count: 1,
        sales_amount: 4.4,
        refunds_count: 0,
        refunds_amount: '0.00',
        voided_count: 0,
        net_amount: '4.40',
        average_ticket: '4.40',
        tax_breakdown: [],
      }),
    ).toThrow();
  });
});

describe('esquema de páginas', () => {
  it('valida una página de tickets con doc_number y total string', () => {
    const parsed = ticketPageSchema.parse({
      items: [
        {
          id: UUID,
          order_id: UUID2,
          terminal_id: UUID,
          series: 'T',
          number: 1,
          doc_number: 'T-0000001',
          printed_at: '2026-09-12T10:00:00Z',
          reprint_count: 0,
          created_at: '2026-09-12T10:00:00Z',
          total_amount: '2.20',
          user_id: null,
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    expect(parsed.items[0].total_amount).toBe('2.20');
    expect(parsed.items[0].user_id).toBeNull();
  });

  it('valida una página de cierres Z con importes anulables', () => {
    const parsed = closurePageSchema.parse({
      items: [
        {
          id: UUID,
          terminal_id: UUID2,
          opened_by: UUID,
          closed_by: UUID,
          opening_amount: '50.00',
          expected_amount: '52.20',
          counted_amount: '52.20',
          difference: '0.00',
          status: 'closed',
          opened_at: '2026-09-12T08:00:00Z',
          closed_at: '2026-09-12T17:00:00Z',
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    expect(parsed.items[0].status).toBe('closed');
    expect(parsed.items[0].difference).toBe('0.00');
  });

  it('rechaza una página sin total (contrato de paginación)', () => {
    expect(() => ticketPageSchema.parse({ items: [], limit: 50, offset: 0 })).toThrow();
  });
});

describe('query string', () => {
  const from = new Date('2026-09-01T00:00:00Z');
  const to = new Date('2026-09-08T00:00:00Z');

  it('incluye from/to ISO siempre (el backend rechaza consultas abiertas)', () => {
    const params = rangeParams({ from, to });
    expect(params.get('from')).toBe('2026-09-01T00:00:00.000Z');
    expect(params.get('to')).toBe('2026-09-08T00:00:00.000Z');
    expect(params.has('limit')).toBe(false);
  });

  it('añade terminal_id y paginación solo si se piden', () => {
    const params = rangeParams({ from, to, terminalId: UUID }, { limit: 50, offset: 100 });
    expect(params.get('terminal_id')).toBe(UUID);
    expect(params.get('limit')).toBe('50');
    expect(params.get('offset')).toBe('100');
  });

  it('facturas: filtros opcionales omitidos cuando quedan vacíos', () => {
    const params = invoiceParams({ from: '2026-09-01', to: '2026-09-08' }, { limit: 50, offset: 0 });
    expect(params.get('from')).toBe('2026-09-01');
    expect(params.has('status')).toBe(false);
    expect(params.has('series')).toBe(false);
    expect(params.get('limit')).toBe('50');
  });
});

describe('fetchers', () => {
  beforeEach(() => {
    localStorage.setItem('tpv-access-token', 'tok');
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('pide /reports/tickets con rango y valida la respuesta', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const page = await fetchTickets(
      { from: new Date('2026-09-01T00:00:00Z'), to: new Date('2026-09-08T00:00:00Z') },
      { limit: 50, offset: 0 },
    );

    expect(page.total).toBe(0);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/reports/tickets?from=2026-09-01');
    expect(url).toContain('limit=50');
    expect((init.headers as Headers).get('Authorization')).toContain('Bearer');
  });

  it('by-period añade el intervalo pedido', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              bucket: '2026-09-12T00:00:00Z',
              sales_count: 2,
              sales_amount: '4.40',
              refunds_count: 1,
              refunds_amount: '-1.10',
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    const page = await fetchByPeriod(
      { from: new Date('2026-09-01T00:00:00Z'), to: new Date('2026-09-08T00:00:00Z') },
      'hour',
      { limit: 50, offset: 0 },
    );

    expect(page.items[0].sales_amount).toBe('4.40');
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toContain('/api/v1/reports/stats/by-period');
    expect(url).toContain('interval=hour');
  });
});

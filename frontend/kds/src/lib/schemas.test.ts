import { describe, expect, it } from 'vitest';
import { boardLineSchema, boardSchema, stationListSchema } from './schemas';

const TICKET = {
  id: '0f0e0d0c-0b0a-4000-8000-000000000001',
  order_id: '0f0e0d0c-0b0a-4000-8000-000000000002',
  status: 'pending',
  priority: 1,
  created_at: '2026-09-14T12:00:00Z',
  ready_at: null,
  served_at: null,
  order_type: 'restaurant',
  table_name: 'M1',
  lines: [
    {
      id: '0f0e0d0c-0b0a-4000-8000-000000000003',
      order_line_id: '0f0e0d0c-0b0a-4000-8000-000000000004',
      name: 'Tortilla de patatas',
      quantity: '2.000',
      notes: 'Sin cebolla',
      status: 'pending',
      station_id: null,
    },
  ],
};

describe('schemas: contrato del tablero', () => {
  it('acepta una respuesta de tablero bien formada', () => {
    const parsed = boardSchema.parse({ items: [TICKET] });
    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0].lines[0].quantity).toBe('2.000');
  });

  it('rechaza cantidades fuera de contrato (dinero/decimal de 3)', () => {
    const bad = {
      ...TICKET,
      lines: [{ ...TICKET.lines[0], quantity: '2' }],
    };
    expect(boardSchema.safeParse({ items: [bad] }).success).toBe(false);
  });

  it('rechaza estados de línea desconocidos', () => {
    const bad = {
      ...TICKET,
      lines: [{ ...TICKET.lines[0], status: 'cocinando' }],
    };
    expect(boardLineSchema.safeParse(bad.lines[0]).success).toBe(false);
  });

  it('lista de estaciones con items', () => {
    expect(
      stationListSchema.parse({
        items: [
          {
            id: '0f0e0d0c-0b0a-4000-8000-000000000010',
            name: 'Plancha',
            sort_order: 1,
            active: true,
            created_at: '2026-09-14T12:00:00Z',
          },
        ],
      }).items,
    ).toHaveLength(1);
  });
});

import { describe, expect, it } from 'vitest';
import {
  TABLE_STATUS_LABEL,
  elapsedMinutes,
  floorTableListSchema,
  floorTableSchema,
  formatElapsed,
  openTotalCents,
  statusChipClass,
} from './floor';

/** Mesa de muestra con los campos del TableStateResponse del backend. */
function sampleTable(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: '6f1c2b3a-0000-4000-8000-000000000001',
    zone_id: '6f1c2b3a-0000-4000-8000-000000000002',
    zone_name: 'Sala principal',
    name: 'M1',
    seats: 4,
    sort_order: 10,
    pos_x: '25.50',
    pos_y: '40.00',
    active: true,
    status: 'open',
    order_id: '6f1c2b3a-0000-4000-8000-000000000003',
    guest_count: 2,
    note: 'Ventana',
    waiter: 'ana',
    opened_at: '2026-09-14T12:00:00+00:00',
    bill_requested_at: null,
    open_total: '25.90',
    ...overrides,
  };
}

describe('floor: contrato del plano', () => {
  it('valida la lista de mesas del servidor', () => {
    const parsed = floorTableListSchema.parse({ items: [sampleTable()] });
    expect(parsed.items).toHaveLength(1);
    expect(parsed.items[0].status).toBe('open');
  });

  it('acepta mesa libre: sin comanda ni importes', () => {
    const mesa = floorTableSchema.parse(
      sampleTable({
        status: 'free',
        order_id: null,
        guest_count: null,
        note: null,
        waiter: null,
        opened_at: null,
        open_total: null,
      }),
    );
    expect(mesa.order_id).toBeNull();
    expect(mesa.open_total).toBeNull();
  });
});

describe('floor: semáforo de estados (§3.2)', () => {
  it('cada estado tiene etiqueta de texto (nunca solo color)', () => {
    expect(TABLE_STATUS_LABEL.free).toBe('Libre');
    expect(TABLE_STATUS_LABEL.open).toBe('Abierta');
    expect(TABLE_STATUS_LABEL.bill).toBe('Cuenta pedida');
  });

  it('los chips distinguen libre, abierta y cuenta pedida', () => {
    expect(statusChipClass('free')).not.toBe(statusChipClass('open'));
    expect(statusChipClass('open')).not.toBe(statusChipClass('bill'));
    expect(statusChipClass('bill')).not.toBe(statusChipClass('free'));
  });
});

describe('floor: tiempo transcurrido tabular', () => {
  const inicio = Date.parse('2026-09-14T12:00:00Z');

  it('cuenta minutos por debajo de la hora', () => {
    expect(formatElapsed('2026-09-14T12:00:00Z', inicio + 5 * 60_000)).toBe('5 min');
    expect(formatElapsed('2026-09-14T12:00:00Z', inicio + 48 * 60_000)).toBe('48 min');
  });

  it('pasa a horas con minutos a dos dígitos', () => {
    expect(formatElapsed('2026-09-14T12:00:00Z', inicio + 60 * 60_000)).toBe('1 h 00');
    expect(formatElapsed('2026-09-14T12:00:00Z', inicio + 65 * 60_000)).toBe('1 h 05');
    expect(formatElapsed('2026-09-14T12:00:00Z', inicio + 192 * 60_000)).toBe('3 h 12');
  });

  it('no devuelve tiempos negativos si el reloj va atrás', () => {
    expect(elapsedMinutes('2026-09-14T12:00:00Z', inicio - 30_000)).toBe(0);
  });
});

describe('floor: importe abierto', () => {
  it('convierte el total del servidor a céntimos', () => {
    const mesa = floorTableSchema.parse(sampleTable());
    expect(openTotalCents(mesa)).toBe(2590n);
  });

  it('devuelve null para una mesa libre', () => {
    const mesa = floorTableSchema.parse(sampleTable({ open_total: null }));
    expect(openTotalCents(mesa)).toBeNull();
  });
});

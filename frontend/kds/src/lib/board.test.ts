import { describe, expect, it } from 'vitest';
import type { BoardTicket } from './schemas';
import {
  alertLevel,
  allLinesIn,
  countLines,
  elapsedMinutes,
  filterByStation,
  formatQuantity,
  nextLineStatus,
  splitByStatus,
  ticketDestination,
} from './board';

const NOW = new Date('2026-09-14T12:00:00Z');

let seq = 0;
function ticket(overrides: Partial<BoardTicket> = {}): BoardTicket {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    order_id: `10000000-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    status: 'pending',
    priority: 0,
    created_at: NOW.toISOString(),
    ready_at: null,
    served_at: null,
    order_type: 'bar',
    table_name: null,
    lines: [],
    ...overrides,
  };
}

describe('board: columnas', () => {
  it('reparte por el estado derivado de la cabecera', () => {
    const board = splitByStatus([
      ticket({ status: 'pending' }),
      ticket({ status: 'preparing' }),
      ticket({ status: 'ready' }),
      ticket({ status: 'served' }),
      ticket({ status: 'cancelled' }), // las anuladas no se pintan
    ]);
    expect(board.pending).toHaveLength(1);
    expect(board.preparing).toHaveLength(1);
    expect(board.ready).toHaveLength(1);
    expect(board.served).toHaveLength(1);
  });

  it('tablero vacío: cuatro columnas vacías, nunca undefined', () => {
    expect(splitByStatus([])).toEqual({ pending: [], preparing: [], ready: [], served: [] });
  });
});

describe('board: filtro por estación', () => {
  const ZONA = '30000000-0000-4000-8000-000000000001';
  const OTRO = '30000000-0000-4000-8000-000000000002';

  it('recorta líneas y oculta comandas que quedan vacías', () => {
    const conEstacion = ticket({
      lines: [
        { id: 'a', order_line_id: 'a', name: 'Tortilla', quantity: '1.000', notes: null, status: 'pending', station_id: ZONA },
        { id: 'b', order_line_id: 'b', name: 'Pan', quantity: '1.000', notes: null, status: 'pending', station_id: OTRO },
      ],
    });
    const sinEstacion = ticket({
      lines: [{ id: 'c', order_line_id: 'c', name: 'Café', quantity: '1.000', notes: null, status: 'pending', station_id: null }],
    });

    expect(filterByStation([conEstacion, sinEstacion], ZONA)).toEqual([
      { ...conEstacion, lines: [conEstacion.lines[0]] },
    ]);
    // Sin filtro: todo pasa intacto (mismas referencias).
    expect(filterByStation([conEstacion, sinEstacion], null)).toEqual([conEstacion, sinEstacion]);
  });
});

describe('board: tiempos y alertas', () => {
  it('minutos transcurridos, con reloj futuro clampado a 0', () => {
    expect(elapsedMinutes('2026-09-14T11:45:00Z', NOW)).toBe(15);
    expect(elapsedMinutes('2026-09-14T12:00:30Z', NOW)).toBe(0);
    expect(elapsedMinutes('2026-09-14T12:10:00Z', NOW)).toBe(0);
  });

  it('semáforo de alerta por umbrales', () => {
    expect(alertLevel(0)).toBe('ok');
    expect(alertLevel(9)).toBe('ok');
    expect(alertLevel(10)).toBe('warn');
    expect(alertLevel(19)).toBe('warn');
    expect(alertLevel(20)).toBe('late');
  });
});

describe('board: avance de líneas', () => {
  it('avanza un paso y no deja salir de terminales', () => {
    expect(nextLineStatus('pending')).toBe('preparing');
    expect(nextLineStatus('preparing')).toBe('ready');
    expect(nextLineStatus('ready')).toBe('served');
    expect(nextLineStatus('served')).toBeNull();
    expect(nextLineStatus('cancelled')).toBeNull();
  });
});

describe('board: utilidades de render', () => {
  it('cuenta líneas de la columna', () => {
    const tickets = [
      ticket({ lines: [
        { id: 'a', order_line_id: 'a', name: 'X', quantity: '1.000', notes: null, status: 'pending', station_id: null },
        { id: 'b', order_line_id: 'b', name: 'Y', quantity: '2.000', notes: null, status: 'ready', station_id: null },
      ] }),
      ticket({ lines: [
        { id: 'c', order_line_id: 'c', name: 'Z', quantity: '1.000', notes: null, status: 'preparing', station_id: null },
      ] }),
    ];
    expect(countLines(tickets)).toBe(3);
  });

  it('destino: mesa, barra, recoger o venta genérica', () => {
    expect(ticketDestination(ticket({ table_name: 'M1' }))).toBe('M1');
    expect(ticketDestination(ticket({ order_type: 'bar' }))).toBe('BARRA');
    expect(ticketDestination(ticket({ order_type: 'takeaway' }))).toBe('RECOGER');
    expect(ticketDestination(ticket({ order_type: 'restaurant', table_name: null }))).toBe('VENTA');
  });

  it('cantidades sin ceros de más (1.000 → 1, 0.500 → 0.5)', () => {
    expect(formatQuantity('1.000')).toBe('1');
    expect(formatQuantity('0.500')).toBe('0.5');
    expect(formatQuantity('2.250')).toBe('2.25');
  });

  it('todo-en-estado para los botones masivos (sin líneas: false)', () => {
    const line = (status: 'pending' | 'ready') => ({
      id: 'x', order_line_id: 'x', name: 'X', quantity: '1.000', notes: null, status, station_id: null,
    });
    expect(allLinesIn([line('ready'), line('ready')], 'ready')).toBe(true);
    expect(allLinesIn([line('ready'), line('pending')], 'ready')).toBe(false);
    expect(allLinesIn([], 'ready')).toBe(false);
  });
});

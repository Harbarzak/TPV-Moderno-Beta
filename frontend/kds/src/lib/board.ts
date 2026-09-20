/**
 * Lógica PURA del tablero: reparto en columnas, filtro por estación, tiempos
 * y umbrales de alerta. Nada de red ni React: todo esto lo prueban los tests
 * y la pantalla solo lo consume.
 *
 * Las columnas son por COMANDA (el estado de la cabecera se deriva en el
 * servidor de sus líneas): una comanda con mezcla de líneas cae en la columna
 * de su estado derivado. Cada línea pinta su propio estado.
 */

import type { BoardLine, BoardTicket, LineStatus } from './schemas';

export const COLUMNS = [
  { key: 'pending', label: 'NUEVO' },
  { key: 'preparing', label: 'PREPARANDO' },
  { key: 'ready', label: 'LISTO' },
  { key: 'served', label: 'SERVIDO' },
] as const satisfies readonly { key: LineStatus; label: string }[];

export type ColumnKey = (typeof COLUMNS)[number]['key'];

export type Board = Record<ColumnKey, BoardTicket[]>;

/** Reparte las comandas del tablero en las cuatro columnas. */
export function splitByStatus(tickets: BoardTicket[]): Board {
  const board: Board = { pending: [], preparing: [], ready: [], served: [] };
  for (const ticket of tickets) {
    if (ticket.status === 'pending' || ticket.status === 'preparing' || ticket.status === 'ready' || ticket.status === 'served') {
      board[ticket.status].push(ticket);
    }
    // «cancelled» no se pinta: las comandas anuladas desaparecen del tablero.
  }
  return board;
}

/**
 * Filtro por estación: recorta las líneas que no son de la estación pedida y
 * oculta las comandas que quedan vacías. ``null`` = todas las estaciones.
 */
export function filterByStation(tickets: BoardTicket[], stationId: string | null): BoardTicket[] {
  if (stationId === null) return tickets;
  const visible: BoardTicket[] = [];
  for (const ticket of tickets) {
    const lines = ticket.lines.filter((line) => line.station_id === stationId);
    if (lines.length > 0) visible.push({ ...ticket, lines });
  }
  return visible;
}

/** Minutos enteros transcurridos desde una fecha ISO (reloj futuro → 0). */
export function elapsedMinutes(iso: string, now: Date): number {
  const minutes = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  return minutes > 0 ? minutes : 0;
}

// Umbrales de alerta (minutos desde que entró la comanda): cocina normal.
export const WARN_MINUTES = 10;
export const LATE_MINUTES = 20;

export type AlertLevel = 'ok' | 'warn' | 'late';

/** Semáforo del tiempo: ok → warn → late (borde rojo + aviso sonoro). */
export function alertLevel(minutes: number): AlertLevel {
  if (minutes >= LATE_MINUTES) return 'late';
  if (minutes >= WARN_MINUTES) return 'warn';
  return 'ok';
}

/** Siguiente estado al tocar una línea (el servidor valida de verdad). */
export function nextLineStatus(status: LineStatus): LineStatus | null {
  switch (status) {
    case 'pending':
      return 'preparing';
    case 'preparing':
      return 'ready';
    case 'ready':
      return 'served';
    default:
      return null; // servidas/canceladas: sin avance
  }
}

/** Cuenta líneas por estado (badges de la cabecera de cada columna). */
export function countLines(tickets: BoardTicket[]): number {
  return tickets.reduce((total, ticket) => total + ticket.lines.length, 0);
}

/** Etiqueta de destino de la comanda (mesa, barra, recoger…). */
export function ticketDestination(ticket: BoardTicket): string {
  if (ticket.table_name !== null) return ticket.table_name;
  switch (ticket.order_type) {
    case 'bar':
      return 'BARRA';
    case 'takeaway':
      return 'RECOGER';
    default:
      return 'VENTA';
  }
}

/** Utilidad de render: «2» o «1.5» con cantidad recortada (1.000 → 1). */
export function formatQuantity(quantity: string): string {
  return quantity.replace(/\.?0+$/, '') || '0';
}

/** ¿Hay alguna línea que no esté en el estado pedido? (botones masivos). */
export function allLinesIn(lines: BoardLine[], status: LineStatus): boolean {
  return lines.length > 0 && lines.every((line) => line.status === status);
}

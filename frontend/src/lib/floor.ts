/**
 * Contrato del plano de mesas (fase 30 · Modo restaurante) y lógica pura
 * del semáforo de estados (§3.2 del sistema de diseño).
 *
 * - Las respuestas de ``/api/v1/restaurant`` viajan validadas por zod.
 * - El dinero es string (§3); las posiciones del mapa son porcentajes
 *   0-100 como string Decimal (NULL = mesa sin colocar).
 * - Nunca solo color: cada chip de estado lleva su TEXTO y, en mesas
 *   abiertas, el tiempo transcurrido en tabular (§3.4).
 */

import { z } from 'zod';
import { parseMoney, type Cents } from './money';

const uuid = z.string().uuid();
const money = z.string();

export const floorZoneSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type FloorZone = z.infer<typeof floorZoneSchema>;

export const floorTableSchema = z.object({
  id: uuid,
  zone_id: uuid,
  zone_name: z.string(),
  name: z.string(),
  seats: z.number().int(),
  sort_order: z.number().int(),
  pos_x: z.string().nullable(),
  pos_y: z.string().nullable(),
  active: z.boolean(),
  status: z.enum(['free', 'open', 'bill']),
  order_id: uuid.nullable(),
  guest_count: z.number().int().nullable(),
  note: z.string().nullable(),
  waiter: z.string().nullable(),
  opened_at: z.string().nullable(),
  bill_requested_at: z.string().nullable(),
  open_total: money.nullable(),
});
export type FloorTable = z.infer<typeof floorTableSchema>;

export const floorTableListSchema = z.object({
  items: z.array(floorTableSchema),
});

export const floorZoneListSchema = z.object({
  items: z.array(floorZoneSchema),
});

/** Línea de una comanda de mesa (mismo contrato que /sales). */
export const floorLineSchema = z.object({
  id: uuid,
  order_id: uuid,
  product_id: uuid.nullable(),
  name: z.string(),
  unit_price: money,
  tax_rate: money,
  quantity: money,
  discount_pct: money,
  base: money,
  total: money,
  notes: z.string().nullable(),
  sort_order: z.number().int(),
});
export type FloorLine = z.infer<typeof floorLineSchema>;

/** Detalle del pedido de mesa: OrderResponse + líneas. */
export const floorOrderDetailSchema = z.object({
  id: uuid,
  terminal_id: uuid,
  user_id: uuid,
  cash_session_id: uuid.nullable(),
  customer_id: uuid.nullable(),
  dining_table_id: uuid.nullable(),
  status: z.string(),
  order_type: z.string(),
  guest_count: z.number().int().nullable(),
  note: z.string().nullable(),
  total_base: money.nullable(),
  total_tax: money.nullable(),
  total_amount: money.nullable(),
  paid_at: z.string().nullable(),
  created_at: z.string(),
  lines: z.array(floorLineSchema),
});
export type FloorOrderDetail = z.infer<typeof floorOrderDetailSchema>;

// ---------------------------------------------------------------------------
// Semáforo (§3.2): Libre / Abierta (+tiempo) / Cuenta pedida (+importe).
// El rojo queda reservado para «requiere atención» (futuro).
// ---------------------------------------------------------------------------
export const TABLE_STATUS_LABEL: Record<FloorTable['status'], string> = {
  free: 'Libre',
  open: 'Abierta',
  bill: 'Cuenta pedida',
};

export function statusChipClass(status: FloorTable['status']): string {
  switch (status) {
    case 'free':
      return 'bg-emerald-500/15 text-emerald-400';
    case 'open':
      return 'bg-amber-500/20 text-amber-300';
    case 'bill':
      return 'bg-sky-500/15 text-sky-400';
  }
}

/** Minutos transcurridos desde una marca ISO (para ordenar/filtrar). */
export function elapsedMinutes(fromIso: string, nowMs: number): number {
  const start = new Date(fromIso).getTime();
  if (Number.isNaN(start)) return 0;
  return Math.max(0, Math.floor((nowMs - start) / 60_000));
}

/**
 * Tiempo transcurrido en formato compacto y tabular: «5 min», «48 min»,
 * «1 h 05». Para el chip de mesa abierta (§3.2: siempre visible).
 */
export function formatElapsed(fromIso: string, nowMs: number): string {
  const minutes = elapsedMinutes(fromIso, nowMs);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, '0')}`;
}

/** Importe abierto de la mesa en céntimos (null si está libre). */
export function openTotalCents(table: FloorTable): Cents | null {
  return table.open_total === null ? null : parseMoney(table.open_total);
}

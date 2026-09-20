/**
 * Contrato de la API del tablero validado al recibir con Zod, espejo EXACTO
 * de las respuestas pydantic de kds.py y auth.py. Las cantidades viajan como
 * decimal «1.000» (string): si el servidor enviara otra cosa, el fallo salta
 * AQUÍ y no corrompe la pantalla de cocina.
 */

import { z } from 'zod';

const uuid = z.string().uuid();
const qty = z.string().regex(/^\d{1,7}\.\d{3}$/, 'cantidad fuera de contrato');
const isoDate = z.string();

// -- sesión (auth.py) ---------------------------------------------------------
export const meSchema = z.object({
  id: uuid,
  username: z.string(),
  full_name: z.string(),
  role: z.string(),
  permissions: z.array(z.string()),
  scope: z.string(),
});
export type Me = z.infer<typeof meSchema>;

export const tokenSchema = z.object({ access_token: z.string() });

// -- tablero KDS (kds.py · GET /kds/board) ------------------------------------
export const lineStatusSchema = z.enum(['pending', 'preparing', 'ready', 'served', 'cancelled']);
export type LineStatus = z.infer<typeof lineStatusSchema>;

export const boardLineSchema = z.object({
  id: uuid,
  order_line_id: uuid,
  name: z.string(),
  quantity: qty,
  notes: z.string().nullable(),
  status: lineStatusSchema,
  station_id: uuid.nullable(),
});
export type BoardLine = z.infer<typeof boardLineSchema>;

export const boardTicketSchema = z.object({
  id: uuid,
  order_id: uuid,
  status: lineStatusSchema,
  priority: z.number().int().min(0).max(1),
  created_at: isoDate,
  ready_at: isoDate.nullable(),
  served_at: isoDate.nullable(),
  order_type: z.string(),
  table_name: z.string().nullable(),
  lines: z.array(boardLineSchema),
});
export type BoardTicket = z.infer<typeof boardTicketSchema>;

export const boardSchema = z.object({ items: z.array(boardTicketSchema) });

// -- estaciones (kds.py · GET /kds/stations) ----------------------------------
export const stationSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  active: z.boolean(),
  created_at: isoDate,
});
export type Station = z.infer<typeof stationSchema>;

export const stationListSchema = z.object({ items: z.array(stationSchema) });

// -- trabajo de impresión (printing.py · POST /kds/tickets/{id}/print) --------
export const printJobSchema = z.object({
  id: uuid,
  printer_id: uuid.nullable(),
  kind: z.string(),
  status: z.string(),
  attempts: z.number().int(),
  last_error: z.string().nullable(),
  dedupe_key: z.string().nullable(),
  // El payload no lo pinta el KDS: solo viaja a la cola de impresión.
  payload: z.unknown(),
  created_at: isoDate,
  sent_at: isoDate.nullable(),
  printed_at: isoDate.nullable(),
});

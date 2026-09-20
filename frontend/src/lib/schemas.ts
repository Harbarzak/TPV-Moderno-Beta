/**
 * Contrato de la API validado en el cliente con Zod (ARCHITECTURE.md §6).
 *
 * Espeja las respuestas de /api/v1/catalog: el dinero es string con 2 decimales
 * (§3) — si el servidor enviara un float, el fallo salta AQUÍ y no corrompe el
 * ticket. Cualquier deriva del contrato se detecta al recibir, no al cobrar.
 */

import { z } from 'zod';

const uuid = z.string().uuid();
const money = z.string().regex(/^\d{1,10}\.\d{2}$/, 'importe sin formato de contrato');
const ratePercent = z.string().regex(/^\d{1,3}\.\d{2}$/, 'tipo de IVA sin formato de contrato');

export const posProductSchema = z.object({
  id: uuid,
  name: z.string(),
  short_name: z.string().nullable(),
  sku: z.string().nullable(),
  category_id: uuid.nullable(),
  department_id: uuid.nullable(),
  tax_code: z.string(),
  tax_rate: ratePercent,
  price: money,
  weighable: z.boolean(),
  kitchen: z.boolean(),
  sort_order: z.number().int(),
  barcodes: z.array(z.string()),
  tier_prices: z.array(z.object({ code: z.string(), price: money })),
});

export const posCatalogSchema = z.object({
  products: z.array(posProductSchema),
});

const panelProductSchema = z.object({
  id: uuid,
  name: z.string(),
  short_name: z.string().nullable(),
  sku: z.string().nullable(),
  price: money,
  tax_code: z.string(),
  tax_rate: ratePercent,
  weighable: z.boolean(),
  kitchen: z.boolean(),
});

const panelItemSchema = z.object({
  id: uuid,
  label: z.string().nullable(),
  color: z.string().nullable(),
  grid_row: z.number().int().min(0).max(19),
  grid_col: z.number().int().min(0).max(19),
  sort_order: z.number().int(),
  product: panelProductSchema,
});

const subPanelSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  items: z.array(panelItemSchema),
});

const panelSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  subpanels: z.array(subPanelSchema),
  items: z.array(panelItemSchema),
});

export const panelTreeSchema = z.object({
  panels: z.array(panelSchema),
});

export type PosProduct = z.infer<typeof posProductSchema>;
export type PanelProduct = z.infer<typeof panelProductSchema>;
export type PanelItem = z.infer<typeof panelItemSchema>;
export type SubPanel = z.infer<typeof subPanelSchema>;
export type Panel = z.infer<typeof panelSchema>;

// ---------------------------------------------------------------------------
// Ventas, caja y formas de pago (fase TPV visual). Mismo espejo Zod: el dinero
// llega como string con 2 decimales y cualquier deriva salta al recibir.
// ---------------------------------------------------------------------------

export const paymentMethodSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  kind: z.enum(['cash', 'card', 'voucher', 'credit', 'other']),
  opens_drawer: z.boolean(),
  sort_order: z.number().int(),
  active: z.boolean(),
});

export const paymentMethodListSchema = z.object({
  items: z.array(paymentMethodSchema),
});

export type PaymentMethodApi = z.infer<typeof paymentMethodSchema>;

export const cashSessionSchema = z.object({
  id: uuid,
  terminal_id: uuid,
  opened_by: uuid,
  closed_by: uuid.nullable(),
  opening_amount: money,
  expected_amount: money.nullable(),
  counted_amount: money.nullable(),
  difference: money.nullable(),
  status: z.enum(['open', 'closed']),
  opened_at: z.string(),
  closed_at: z.string().nullable(),
});

export type CashSessionApi = z.infer<typeof cashSessionSchema>;

export const orderSchema = z.object({
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
});

export type OrderApi = z.infer<typeof orderSchema>;

/** Respuesta del close: pedido + pagos + cambio + ticket emitido. */
export const orderCloseSchema = z.object({
  order: orderSchema,
  payments: z.array(
    z.object({
      id: uuid,
      order_id: uuid,
      payment_method_id: uuid,
      code: z.string(),
      kind: z.string(),
      amount: money,
      status: z.string(),
      external_ref: z.string().nullable(),
      confirmed_at: z.string().nullable(),
    }),
  ),
  change_total: money,
  ticket: z.object({ id: uuid, doc_number: z.string() }),
});

export type OrderCloseApi = z.infer<typeof orderCloseSchema>;

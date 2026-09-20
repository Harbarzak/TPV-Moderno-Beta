/**
 * Contrato de la API validado al recibir con Zod, espejo EXACTO de las
 * respuestas pydantic del backend (sales.py, cash.py, auth.py, payments.py,
 * catalog.py). El dinero viaja SIEMPRE como string «12.34» (§3) y las
 * cantidades como decimal de 3 («1.000»): si el servidor enviara otra cosa,
 * el fallo salta AQUÍ y no corrompe la pantalla de cobro. Aquí no se
 * interpreta negocio, solo se muestra.
 */

import { z } from 'zod';

const uuid = z.string().uuid();
/** Importe monetario: Numeric(12,2) serializado «0.00». */
const money = z.string().regex(/^\d{1,10}\.\d{2}$/, 'importe fuera de contrato');
/** Decimal de cantidad: Numeric(10,3) serializado «1.000». */
const qty = z.string().regex(/^\d{1,7}\.\d{3}$/, 'cantidad fuera de contrato');
/** Porcentaje/tipo: «21.00». */
const rate = z.string().regex(/^\d{1,3}\.\d{2}$/, 'porcentaje fuera de contrato');
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

// -- catálogo POS (catalog.py · GET /catalog/pos) ------------------------------
export const posProductSchema = z.object({
  id: uuid,
  name: z.string(),
  short_name: z.string().nullable(),
  sku: z.string().nullable(),
  category_id: uuid.nullable(),
  department_id: uuid.nullable(),
  tax_code: z.string(),
  tax_rate: rate,
  price: money,
  weighable: z.boolean(),
  kitchen: z.boolean(),
  sort_order: z.number().int(),
  barcodes: z.array(z.string()),
  tier_prices: z.array(z.object({ tier_code: z.string(), price: money })),
});
export type PosProduct = z.infer<typeof posProductSchema>;

export const posCatalogSchema = z.object({ products: z.array(posProductSchema) });

// -- pedidos (sales.py) --------------------------------------------------------
export const orderLineSchema = z.object({
  id: uuid,
  order_id: uuid,
  product_id: uuid.nullable(),
  name: z.string(),
  unit_price: money,
  tax_rate: rate,
  quantity: qty,
  discount_pct: rate,
  base: money,
  total: money,
  notes: z.string().nullable(),
  sort_order: z.number().int(),
});
export type OrderLine = z.infer<typeof orderLineSchema>;

export const taxSliceSchema = z.object({
  rate_bp: z.number().int(),
  base: money,
  tax: money,
  total: money,
});

export const taxSummarySchema = z.object({
  base: money,
  tax: money,
  total: money,
  slices: z.array(taxSliceSchema),
});

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
  tax_summary: taxSummarySchema.nullable(),
  paid_at: isoDate.nullable(),
  voided_at: isoDate.nullable(),
  void_reason: z.string().nullable(),
  created_at: isoDate,
});
export type Order = z.infer<typeof orderSchema>;

export const paymentSchema = z.object({
  id: uuid,
  order_id: uuid,
  payment_method_id: uuid,
  code: z.string(),
  kind: z.string(),
  amount: money,
  status: z.string(),
  external_ref: z.string().nullable(),
  confirmed_at: isoDate.nullable(),
});

export const orderDetailSchema = orderSchema.extend({
  lines: z.array(orderLineSchema),
  payments: z.array(paymentSchema),
});
export type OrderDetail = z.infer<typeof orderDetailSchema>;

export const orderCloseSchema = orderSchema.extend({
  lines: z.array(orderLineSchema),
  payments: z.array(paymentSchema),
  /** Cambio a devolver: lo calcula el backend, nunca el cliente. */
  change_total: money,
  ticket: z.object({ id: uuid, doc_number: z.string() }),
});
export type OrderClose = z.infer<typeof orderCloseSchema>;

export const orderListSchema = z.object({
  items: z.array(orderSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

// -- formas de pago (payments.py · GET /admin/payment-methods) -----------------
export const paymentMethodSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  kind: z.string(), // cash | card | other (lo interpreta el backend)
  opens_drawer: z.boolean(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type PaymentMethod = z.infer<typeof paymentMethodSchema>;

export const paymentMethodListSchema = z.object({ items: z.array(paymentMethodSchema) });

// -- caja (cash.py) ------------------------------------------------------------
export const cashSessionSchema = z.object({
  id: uuid,
  terminal_id: uuid,
  opened_by: uuid,
  closed_by: uuid.nullable(),
  opening_amount: money,
  expected_amount: money.nullable(),
  counted_amount: money.nullable(),
  difference: money.nullable(),
  status: z.string(), // open | closed
  opened_at: isoDate,
  closed_at: isoDate.nullable(),
});
export type CashSession = z.infer<typeof cashSessionSchema>;

export const movementSchema = z.object({
  id: uuid,
  cash_session_id: uuid,
  kind: z.string(), // in | out
  amount: money,
  reason: z.string(),
  user_id: uuid,
  created_at: isoDate,
});
export type Movement = z.infer<typeof movementSchema>;

export const cashCountSchema = z.object({
  id: uuid,
  cash_session_id: uuid,
  counted_by: uuid,
  counted_amount: money,
  expected_amount: money.nullable(),
  difference: money.nullable(),
  created_at: isoDate,
  lines: z.array(z.object({ denomination: money, quantity: z.number().int() })),
});

export const methodTotalSchema = z.object({
  code: z.string(),
  kind: z.string(),
  sales_total: money,
  sales_count: z.number().int(),
  refunds_total: money,
  refunds_count: z.number().int(),
});

export const cashReportSchema = z.object({
  session: cashSessionSchema,
  opening_amount: money,
  cash_in: money,
  cash_out: money,
  cash_sales: money,
  cash_refunds: money,
  expected_cash: money,
  difference: money.nullable(),
  method_totals: z.array(methodTotalSchema),
  movements: z.array(movementSchema),
  counts: z.array(cashCountSchema),
});
export type CashReport = z.infer<typeof cashReportSchema>;

export const sessionListSchema = z.object({
  items: z.array(cashSessionSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

// -- consulta de productos (catalog.py · GET /catalog/products) ----------------
export const productSchema = z.object({
  id: uuid,
  sku: z.string().nullable(),
  name: z.string(),
  short_name: z.string().nullable(),
  category_id: uuid.nullable(),
  tax_rate_id: uuid,
  price: money,
  weighable: z.boolean(),
  kitchen: z.boolean(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type Product = z.infer<typeof productSchema>;

export const productListSchema = z.object({
  items: z.array(productSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

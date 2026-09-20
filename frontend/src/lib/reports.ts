/**
 * Contrato de los informes (fase 15 — ARCHITECTURE.md §13).
 *
 * Espeja /api/v1/reports: consultas de SOLO LECTURA bajo ``reports.view``,
 * con rango temporal OBLIGATORIO (``from``/``to``, techo de 366 días en el
 * servidor), agregaciones en SQL y páginas de hasta 200 filas — el mostrador
 * nunca descarga la tabla entera. El dinero viaja como string con 2 decimales
 * (§3); los importes de devoluciones llegan con signo (negativo en el
 * resumen, positivo en forma de pago, igual que el informe Z de caja).
 *
 * Como en schemas.ts, cualquier deriva del contrato falla AQUÍ (Zod) y no
 * pinta cifras corruptas.
 */

import { z } from 'zod';
import { apiFetch } from './api';

const uuid = z.string().uuid();
const money = z.string().regex(/^-?\d{1,10}\.\d{2}$/, 'importe sin formato de contrato');
const qty = z.string().regex(/^-?\d{1,6}(\.\d{1,3})?$/, 'cantidad sin formato de contrato');
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
// Los timestamps llegan ISO-8601; el formato exacto lo fija el servidor.
const isoDateTime = z.string().min(10);

export const REPORTS_INTERVALS = ['hour', 'day', 'week', 'month'] as const;
export type ReportInterval = (typeof REPORTS_INTERVALS)[number];

export const REPORTS_BASE = '/api/v1/reports';

/** Rango temporal obligatorio de toda consulta de informes (§13). */
export interface ReportRange {
  from: Date;
  to: Date;
  terminalId?: string;
}

/** Página pedida al servidor (techo del backend: limit ≤ 200). */
export interface ReportPage {
  limit: number;
  offset: number;
}

export interface Paged<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Esquemas de respuesta
// ---------------------------------------------------------------------------
export const ticketRowSchema = z.object({
  id: uuid,
  order_id: uuid,
  terminal_id: uuid,
  series: z.string(),
  number: z.number().int(),
  doc_number: z.string().nullable(),
  printed_at: isoDateTime.nullable(),
  reprint_count: z.number().int(),
  created_at: isoDateTime,
  total_amount: money,
  user_id: uuid.nullable(),
});
export const ticketPageSchema = paged(ticketRowSchema);

export const invoiceRowSchema = z.object({
  id: uuid,
  customer_id: uuid,
  series: z.string(),
  year: z.number().int(),
  number: z.number().int(),
  status: z.string(),
  issue_date: isoDate,
  total_base: money,
  total_tax: money,
  total_amount: money,
  rectified_invoice_id: uuid.nullable(),
  voided_at: isoDateTime.nullable(),
  void_reason: z.string().nullable(),
  created_at: isoDateTime,
  doc_number: z.string().nullable(),
});
export const invoicePageSchema = paged(invoiceRowSchema);

/** Factura detallada: mismo render que /documents, aquí bajo reports.view. */
export const invoiceDetailSchema = z.object({
  id: uuid,
  customer_id: uuid,
  series: z.string(),
  year: z.number().int(),
  number: z.number().int(),
  doc_number: z.string(),
  status: z.string(),
  issue_date: isoDate,
  total_base: money,
  total_tax: money,
  total_amount: money,
  rectified_invoice_id: uuid.nullable(),
  voided_at: isoDateTime.nullable(),
  void_reason: z.string().nullable(),
  created_at: isoDateTime,
  payload: z.record(z.unknown()),
  lines: z.array(z.object({ order_id: uuid })),
});

export const cashSessionSchema = z.object({
  id: uuid,
  terminal_id: uuid,
  opened_by: uuid.nullable(),
  closed_by: uuid.nullable(),
  opening_amount: money,
  expected_amount: money.nullable(),
  counted_amount: money.nullable(),
  difference: money.nullable(),
  status: z.enum(['open', 'closed']),
  opened_at: isoDateTime,
  closed_at: isoDateTime.nullable(),
});
export const closurePageSchema = paged(cashSessionSchema);

export const taxLineSchema = z.object({ tax_rate: money, base: money, total: money });

export const summarySchema = z.object({
  sales_count: z.number().int(),
  sales_amount: money,
  refunds_count: z.number().int(),
  refunds_amount: money, // negativo
  voided_count: z.number().int(),
  net_amount: money,
  average_ticket: money,
  tax_breakdown: z.array(taxLineSchema),
});

export const productRowSchema = z.object({
  product_id: uuid.nullable(),
  name: z.string(),
  orders: z.number().int(),
  quantity: qty.nullable(),
  base: money,
  total: money,
});
export const productPageSchema = paged(productRowSchema);

export const categoryRowSchema = z.object({
  category_id: uuid.nullable(),
  name: z.string(),
  orders: z.number().int(),
  quantity: qty.nullable(),
  base: money,
  total: money,
});
export const categoryPageSchema = paged(categoryRowSchema);

export const waiterRowSchema = z.object({
  user_id: uuid,
  username: z.string(),
  full_name: z.string().nullable(),
  sales_count: z.number().int(),
  refunds_count: z.number().int(),
  total: money,
});
export const waiterPageSchema = paged(waiterRowSchema);

export const paymentMethodRowSchema = z.object({
  code: z.string(),
  kind: z.string(),
  sales_count: z.number().int(),
  sales_amount: money,
  refunds_count: z.number().int(),
  refunds_amount: money, // positivo, convención del informe Z
});
export const paymentMethodPageSchema = paged(paymentMethodRowSchema);

export const periodRowSchema = z.object({
  bucket: isoDateTime,
  sales_count: z.number().int(),
  sales_amount: money,
  refunds_count: z.number().int(),
  refunds_amount: money,
});
export const periodPageSchema = paged(periodRowSchema);

function paged<T extends z.ZodTypeAny>(item: T) {
  return z.object({
    items: z.array(item),
    total: z.number().int(),
    limit: z.number().int(),
    offset: z.number().int(),
  });
}

export type TicketReportRow = z.infer<typeof ticketRowSchema>;
export type TicketReportPage = z.infer<typeof ticketPageSchema>;
export type InvoiceReportRow = z.infer<typeof invoiceRowSchema>;
export type InvoiceReportPage = z.infer<typeof invoicePageSchema>;
export type InvoiceDetail = z.infer<typeof invoiceDetailSchema>;
export type CashClosure = z.infer<typeof cashSessionSchema>;
export type ClosurePage = z.infer<typeof closurePageSchema>;
export type SummaryReport = z.infer<typeof summarySchema>;
export type ProductReportRow = z.infer<typeof productRowSchema>;
export type ProductReportPage = z.infer<typeof productPageSchema>;
export type CategoryReportRow = z.infer<typeof categoryRowSchema>;
export type CategoryReportPage = z.infer<typeof categoryPageSchema>;
export type WaiterReportRow = z.infer<typeof waiterRowSchema>;
export type WaiterReportPage = z.infer<typeof waiterPageSchema>;
export type PaymentMethodReportRow = z.infer<typeof paymentMethodRowSchema>;
export type PaymentMethodReportPage = z.infer<typeof paymentMethodPageSchema>;
export type PeriodReportRow = z.infer<typeof periodRowSchema>;
export type PeriodReportPage = z.infer<typeof periodPageSchema>;

// ---------------------------------------------------------------------------
// Query string y fetchers
// ---------------------------------------------------------------------------
/** ``from``/``to`` son obligatorios: el backend rechaza consultas sin rango. */
export function rangeParams(range: ReportRange, page?: ReportPage): URLSearchParams {
  const params = new URLSearchParams();
  params.set('from', range.from.toISOString());
  params.set('to', range.to.toISOString());
  if (range.terminalId) params.set('terminal_id', range.terminalId);
  if (page) {
    params.set('limit', String(page.limit));
    params.set('offset', String(page.offset));
  }
  return params;
}

async function getPage<T>(
  path: string,
  params: URLSearchParams,
  schema: z.ZodType<T>,
): Promise<T> {
  const raw = await apiFetch<unknown>(`${path}?${params.toString()}`);
  return schema.parse(raw);
}

export function fetchTickets(range: ReportRange, page: ReportPage) {
  return getPage(`${REPORTS_BASE}/tickets`, rangeParams(range, page), ticketPageSchema);
}

/** Facturas: filtros por fecha de emisión (día, "YYYY-MM-DD") + estado y serie. */
export function invoiceParams(
  opts: { from: string; to: string; status?: string; series?: string },
  page: ReportPage,
): URLSearchParams {
  const params = new URLSearchParams();
  params.set('from', opts.from);
  params.set('to', opts.to);
  if (opts.status) params.set('status', opts.status);
  if (opts.series) params.set('series', opts.series);
  params.set('limit', String(page.limit));
  params.set('offset', String(page.offset));
  return params;
}

export function fetchInvoices(
  opts: { from: string; to: string; status?: string; series?: string },
  page: ReportPage,
) {
  return getPage(`${REPORTS_BASE}/invoices`, invoiceParams(opts, page), invoicePageSchema);
}

export async function fetchInvoiceDetail(invoiceId: string): Promise<InvoiceDetail> {
  const raw = await apiFetch<unknown>(`${REPORTS_BASE}/invoices/${invoiceId}`);
  return invoiceDetailSchema.parse(raw);
}

export function fetchCashClosures(range: ReportRange, page: ReportPage) {
  return getPage(`${REPORTS_BASE}/cash-closures`, rangeParams(range, page), closurePageSchema);
}

export function fetchSummary(range: ReportRange) {
  return getPage(`${REPORTS_BASE}/stats/summary`, rangeParams(range), summarySchema);
}

export function fetchByProduct(range: ReportRange, page: ReportPage) {
  return getPage(`${REPORTS_BASE}/stats/by-product`, rangeParams(range, page), productPageSchema);
}

export function fetchByCategory(range: ReportRange, page: ReportPage) {
  return getPage(`${REPORTS_BASE}/stats/by-category`, rangeParams(range, page), categoryPageSchema);
}

export function fetchByWaiter(range: ReportRange, page: ReportPage) {
  return getPage(`${REPORTS_BASE}/stats/by-waiter`, rangeParams(range, page), waiterPageSchema);
}

export function fetchByPaymentMethod(range: ReportRange, page: ReportPage) {
  return getPage(
    `${REPORTS_BASE}/stats/by-payment-method`,
    rangeParams(range, page),
    paymentMethodPageSchema,
  );
}

export function fetchByPeriod(range: ReportRange, interval: ReportInterval, page: ReportPage) {
  const params = rangeParams(range, page);
  params.set('interval', interval);
  return getPage(`${REPORTS_BASE}/stats/by-period`, params, periodPageSchema);
}

/**
 * Informes del mostrador (fase 15): consultas de SOLO LECTURA contra
 * /api/v1/reports. Reglas de la casa reflejadas aquí:
 *
 * - Rango de fechas OBLIGATORIO (por defecto, los últimos 7 días): el
 *   servidor rechaza consultas abiertas (§13) y la UI lo pide antes.
 * - Paginación en servidor (50 filas por página, «Anterior/Siguiente»):
 *   nunca se pide la tabla entera, por grande que sea el histórico.
 * - La entrada es visible para cualquier sesión, pero sin el permiso
 *   ``reports.view`` el backend responde 403 y aquí se muestra claro:
 *   la última palabra la tiene siempre el servidor.
 */

import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Loader2,
} from 'lucide-react';
import { ApiError } from '../lib/api';
import {
  REPORTS_INTERVALS,
  fetchByCategory,
  fetchByPaymentMethod,
  fetchByPeriod,
  fetchByProduct,
  fetchByWaiter,
  fetchCashClosures,
  fetchInvoiceDetail,
  fetchInvoices,
  fetchSummary,
  fetchTickets,
} from '../lib/reports';
import type {
  CategoryReportPage,
  ClosurePage,
  InvoiceDetail,
  InvoiceReportPage,
  PaymentMethodReportPage,
  PeriodReportPage,
  Paged,
  ProductReportPage,
  ReportInterval,
  SummaryReport,
  TicketReportPage,
  WaiterReportPage,
} from '../lib/reports';

const PAGE_SIZE = 50;

type TabKey =
  | 'summary'
  | 'product'
  | 'category'
  | 'waiter'
  | 'payment'
  | 'period'
  | 'tickets'
  | 'invoices'
  | 'closures';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'summary', label: 'Resumen' },
  { key: 'product', label: 'Por producto' },
  { key: 'category', label: 'Por categoría' },
  { key: 'waiter', label: 'Por camarero' },
  { key: 'payment', label: 'Forma de pago' },
  { key: 'period', label: 'Por periodo' },
  { key: 'tickets', label: 'Tickets' },
  { key: 'invoices', label: 'Facturas' },
  { key: 'closures', label: 'Cierres Z' },
];

type ReportData =
  | { tab: 'summary'; summary: SummaryReport }
  | { tab: 'product'; page: ProductReportPage }
  | { tab: 'category'; page: CategoryReportPage }
  | { tab: 'waiter'; page: WaiterReportPage }
  | { tab: 'payment'; page: PaymentMethodReportPage }
  | { tab: 'period'; page: PeriodReportPage }
  | { tab: 'tickets'; page: TicketReportPage }
  | { tab: 'invoices'; page: InvoiceReportPage }
  | { tab: 'closures'; page: ClosurePage };

function toISODate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function daysAgo(days: number): Date {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date;
}

function describeError(exc: unknown): string {
  if (exc instanceof ApiError) {
    if (exc.status === 403) {
      return 'Sin permiso para consultar informes (reports.view). La sesión actual no lo tiene asignado.';
    }
    if (exc.status === 422) {
      return `Parámetros fuera de rango: ${exc.message}`;
    }
    return exc.message;
  }
  return 'Sin conexión con el servidor.';
}

interface ReportsViewProps {
  onBack: () => void;
}

export default function ReportsView({ onBack }: ReportsViewProps) {
  const [tab, setTab] = useState<TabKey>('summary');
  const [fromDate, setFromDate] = useState(() => toISODate(daysAgo(7)));
  const [toDate, setToDate] = useState(() => toISODate(new Date()));
  const [bucketInterval, setBucketInterval] = useState<ReportInterval>('day');
  const [invoiceStatus, setInvoiceStatus] = useState('');
  const [invoiceSeries, setInvoiceSeries] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const requestId = useRef(0);

  const rangeInvalid = fromDate > toDate;

  useEffect(() => {
    if (rangeInvalid) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    const from = new Date(`${fromDate}T00:00:00`);
    const to = new Date(`${toDate}T23:59:59`);
    const page = { limit: PAGE_SIZE, offset };
    const run = async (): Promise<ReportData> => {
      switch (tab) {
        case 'summary':
          return { tab, summary: await fetchSummary({ from, to }) };
        case 'product':
          return { tab, page: await fetchByProduct({ from, to }, page) };
        case 'category':
          return { tab, page: await fetchByCategory({ from, to }, page) };
        case 'waiter':
          return { tab, page: await fetchByWaiter({ from, to }, page) };
        case 'payment':
          return { tab, page: await fetchByPaymentMethod({ from, to }, page) };
        case 'period':
          return { tab, page: await fetchByPeriod({ from, to }, bucketInterval, page) };
        case 'tickets':
          return { tab, page: await fetchTickets({ from, to }, page) };
        case 'invoices':
          return {
            tab,
            page: await fetchInvoices(
              {
                from: fromDate,
                to: toDate,
                status: invoiceStatus || undefined,
                series: invoiceSeries || undefined,
              },
              page,
            ),
          };
        case 'closures':
          return { tab, page: await fetchCashClosures({ from, to }, page) };
      }
    };
    void run().then(
      (next) => {
        if (requestId.current === id) {
          setData(next);
          setLoading(false);
        }
      },
      (exc: unknown) => {
        if (requestId.current !== id) return;
        setError(describeError(exc));
        setLoading(false);
      },
    );
  }, [tab, fromDate, toDate, bucketInterval, invoiceStatus, invoiceSeries, offset, rangeInvalid]);

  const selectTab = (key: TabKey) => {
    setTab(key);
    setOffset(0);
  };

  const changeRange = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setOffset(0);
  };

  const openDetail = async (invoiceId: string) => {
    setDetailError(null);
    try {
      setDetail(await fetchInvoiceDetail(invoiceId));
    } catch (exc) {
      setDetailError(describeError(exc));
    }
  };

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden bg-slate-900 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-2 rounded-lg bg-slate-700 px-3 py-2 text-sm font-medium text-slate-100 hover:bg-slate-600"
        >
          <ArrowLeft className="size-4" aria-hidden />
          Volver a la venta
        </button>
        <h2 className="text-lg font-semibold text-amber-400">Informes</h2>

        <div className="ml-auto flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1 text-slate-300">
            Desde
            <input
              type="date"
              value={fromDate}
              onChange={(e) => changeRange(setFromDate)(e.target.value)}
              className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
            />
          </label>
          <label className="flex items-center gap-1 text-slate-300">
            Hasta
            <input
              type="date"
              value={toDate}
              onChange={(e) => changeRange(setToDate)(e.target.value)}
              className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
            />
          </label>
        </div>
      </div>

      <nav className="flex flex-wrap gap-1" aria-label="Tipos de informe">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            onClick={() => selectTab(entry.key)}
            className={
              entry.key === tab
                ? 'rounded-t-lg bg-slate-700 px-3 py-1.5 text-sm font-semibold text-amber-300'
                : 'rounded-t-lg px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === 'period' && (
        <label className="flex items-center gap-2 text-sm text-slate-300">
          Agrupar por
          <select
            value={bucketInterval}
            onChange={(e) => {
              setBucketInterval(e.target.value as ReportInterval);
              setOffset(0);
            }}
            className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
          >
            {REPORTS_INTERVALS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      )}

      {tab === 'invoices' && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-1 text-slate-300">
            Estado
            <select
              value={invoiceStatus}
              onChange={(e) => changeRange(setInvoiceStatus)(e.target.value)}
              className="rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100"
            >
              <option value="">Todos</option>
              <option value="issued">Emitidas</option>
              <option value="rectified">Rectificadas</option>
              <option value="voided">Anuladas</option>
            </select>
          </label>
          <label className="flex items-center gap-1 text-slate-300">
            Serie
            <input
              value={invoiceSeries}
              onChange={(e) => changeRange(setInvoiceSeries)(e.target.value)}
              placeholder="p. ej. F2026"
              className="w-28 rounded border border-slate-600 bg-slate-800 px-2 py-1 text-slate-100 placeholder:text-slate-500"
            />
          </label>
        </div>
      )}

      {rangeInvalid ? (
        <ErrorNote message="El rango es inválido: «Desde» no puede ser posterior a «Hasta»." />
      ) : loading ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-slate-400">
          <Loader2 className="size-8 animate-spin" aria-hidden />
          <p>Consultando…</p>
        </div>
      ) : error ? (
        <ErrorNote message={error} />
      ) : data ? (
        <ReportBody data={data} offset={offset} onPage={setOffset} onDetail={openDetail} />
      ) : null}

      {detailError && <ErrorNote message={detailError} />}
      {detail && <InvoiceDetailOverlay detail={detail} onClose={() => setDetail(null)} />}
    </main>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center"
    >
      <CircleAlert className="size-10 text-red-400" aria-hidden />
      <p className="max-w-xl text-slate-200">{message}</p>
    </div>
  );
}

function ReportBody(props: {
  data: ReportData;
  offset: number;
  onPage: (offset: number) => void;
  onDetail: (invoiceId: string) => void;
}) {
  const { data, offset, onPage, onDetail } = props;

  if (data.tab === 'summary') {
    const s = data.summary;
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard label="Ventas" value={`${s.sales_count}`} hint={s.sales_amount} />
          <StatCard label="Devoluciones" value={`${s.refunds_count}`} hint={s.refunds_amount} />
          <StatCard label="Anuladas" value={`${s.voided_count}`} />
          <StatCard label="Venta neta" value={s.net_amount} hint={`Ticket medio ${s.average_ticket}`} />
        </div>
        <ReportTable headers={['Tipo IVA', 'Base', 'Total']}>
          {s.tax_breakdown.map((line) => (
            <tr key={line.tax_rate}>
              <Td>{line.tax_rate} %</Td>
              <Td right>{line.base}</Td>
              <Td right>{line.total}</Td>
            </tr>
          ))}
          {s.tax_breakdown.length === 0 && <EmptyRow cols={3} />}
        </ReportTable>
      </div>
    );
  }

  const body = (() => {
    switch (data.tab) {
      case 'product':
        return (
          <ReportTable headers={['Producto', 'Pedidos', 'Cantidad', 'Base', 'Total']}>
            {data.page.items.map((row) => (
              <tr key={row.product_id ?? row.name} className="hover:bg-slate-800/60">
                <Td>{row.name}</Td>
                <Td right>{row.orders}</Td>
                <Td right>{row.quantity ?? '—'}</Td>
                <Td right>{row.base}</Td>
                <Td right>{row.total}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={5} />}
          </ReportTable>
        );
      case 'category':
        return (
          <ReportTable headers={['Categoría', 'Pedidos', 'Cantidad', 'Base', 'Total']}>
            {data.page.items.map((row) => (
              <tr key={row.category_id ?? row.name} className="hover:bg-slate-800/60">
                <Td>{row.name}</Td>
                <Td right>{row.orders}</Td>
                <Td right>{row.quantity ?? '—'}</Td>
                <Td right>{row.base}</Td>
                <Td right>{row.total}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={5} />}
          </ReportTable>
        );
      case 'waiter':
        return (
          <ReportTable headers={['Camarero', 'Usuario', 'Ventas', 'Devoluciones', 'Total']}>
            {data.page.items.map((row) => (
              <tr key={row.user_id} className="hover:bg-slate-800/60">
                <Td>{row.full_name ?? row.username}</Td>
                <Td>{row.username}</Td>
                <Td right>{row.sales_count}</Td>
                <Td right>{row.refunds_count}</Td>
                <Td right>{row.total}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={5} />}
          </ReportTable>
        );
      case 'payment':
        return (
          <ReportTable headers={['Forma', 'Tipo', 'Ventas', 'Importe', 'Devoluciones', 'Importe dev.']}>
            {data.page.items.map((row) => (
              <tr key={row.code} className="hover:bg-slate-800/60">
                <Td>{row.code}</Td>
                <Td>{row.kind}</Td>
                <Td right>{row.sales_count}</Td>
                <Td right>{row.sales_amount}</Td>
                <Td right>{row.refunds_count}</Td>
                <Td right>{row.refunds_amount}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={6} />}
          </ReportTable>
        );
      case 'period':
        return (
          <ReportTable headers={['Periodo', 'Ventas', 'Importe', 'Devoluciones', 'Importe dev.']}>
            {data.page.items.map((row) => (
              <tr key={row.bucket} className="hover:bg-slate-800/60">
                <Td>{new Date(row.bucket).toLocaleString()}</Td>
                <Td right>{row.sales_count}</Td>
                <Td right>{row.sales_amount}</Td>
                <Td right>{row.refunds_count}</Td>
                <Td right>{row.refunds_amount}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={5} />}
          </ReportTable>
        );
      case 'tickets':
        return (
          <ReportTable headers={['Documento', 'Emitido', 'Reimpresiones', 'Total']}>
            {data.page.items.map((row) => (
              <tr key={row.id} className="hover:bg-slate-800/60">
                <Td>{row.doc_number ?? `${row.series}-${row.number}`}</Td>
                <Td>{new Date(row.created_at).toLocaleString()}</Td>
                <Td right>{row.reprint_count}</Td>
                <Td right>{row.total_amount}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={4} />}
          </ReportTable>
        );
      case 'invoices':
        return (
          <ReportTable headers={['Documento', 'Fecha', 'Estado', 'Base', 'IVA', 'Total', '']}>
            {data.page.items.map((row) => (
              <tr key={row.id} className="hover:bg-slate-800/60">
                <Td>{row.doc_number ?? `${row.series}-${row.number}/${row.year}`}</Td>
                <Td>{row.issue_date}</Td>
                <Td>{row.status}</Td>
                <Td right>{row.total_base}</Td>
                <Td right>{row.total_tax}</Td>
                <Td right>{row.total_amount}</Td>
                <Td>
                  <button
                    type="button"
                    onClick={() => onDetail(row.id)}
                    className="rounded bg-slate-700 px-2 py-1 text-xs font-medium text-slate-100 hover:bg-slate-600"
                  >
                    Detalle
                  </button>
                </Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={7} />}
          </ReportTable>
        );
      case 'closures':
        return (
          <ReportTable headers={['Abierta', 'Cerrada', 'Apertura', 'Esperado', 'Contado', 'Dif.']}>
            {data.page.items.map((row) => (
              <tr key={row.id} className="hover:bg-slate-800/60">
                <Td>{new Date(row.opened_at).toLocaleString()}</Td>
                <Td>{row.closed_at ? new Date(row.closed_at).toLocaleString() : '—'}</Td>
                <Td right>{row.opening_amount}</Td>
                <Td right>{row.expected_amount ?? '—'}</Td>
                <Td right>{row.counted_amount ?? '—'}</Td>
                <Td right>{row.difference ?? '—'}</Td>
              </tr>
            ))}
            {data.page.items.length === 0 && <EmptyRow cols={6} />}
          </ReportTable>
        );
    }
  })();

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex min-h-0 flex-1 flex-col">{body}</div>
      <Pager page={data.page} offset={offset} onPage={onPage} />
    </div>
  );
}

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-3">
      <p className="text-sm text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-100">{value}</p>
      {hint && <p className="mt-0.5 text-xs tabular-nums text-slate-400">{hint}</p>}
    </div>
  );
}

function ReportTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-slate-700">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-slate-800 text-slate-300">
          <tr>
            {headers.map((header, index) => (
              <th
                key={`${header}-${index}`}
                className={
                  index === 0
                    ? 'px-3 py-2 font-medium'
                    : 'px-3 py-2 text-right font-medium last:text-left'
                }
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">{children}</tbody>
      </table>
    </div>
  );
}

function Td({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <td
      className={
        right ? 'px-3 py-2 text-right tabular-nums text-slate-200' : 'px-3 py-2 text-slate-200'
      }
    >
      {children}
    </td>
  );
}

function EmptyRow({ cols }: { cols: number }) {
  return (
    <tr>
      <td colSpan={cols} className="px-3 py-6 text-center text-slate-500">
        Sin resultados en el periodo.
      </td>
    </tr>
  );
}

function Pager({
  page,
  offset,
  onPage,
}: {
  page: Paged<unknown>;
  offset: number;
  onPage: (offset: number) => void;
}) {
  const first = page.total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + page.limit, page.total);
  return (
    <div className="flex items-center justify-between text-sm text-slate-400">
      <span>
        {page.total === 0
          ? 'Sin resultados'
          : `Filas ${first}–${last} de ${page.total} (páginas de ${page.limit} en el servidor)`}
      </span>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => onPage(Math.max(0, offset - page.limit))}
          className="flex items-center gap-1 rounded-lg bg-slate-700 px-3 py-1.5 font-medium text-slate-100 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronLeft className="size-4" aria-hidden />
          Anterior
        </button>
        <button
          type="button"
          disabled={offset + page.limit >= page.total}
          onClick={() => onPage(offset + page.limit)}
          className="flex items-center gap-1 rounded-lg bg-slate-700 px-3 py-1.5 font-medium text-slate-100 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Siguiente
          <ChevronRight className="size-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}

function InvoiceDetailOverlay({
  detail,
  onClose,
}: {
  detail: InvoiceDetail;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-6">
      <div className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl border border-slate-600 bg-slate-800 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h3 className="text-lg font-semibold text-amber-300">
              Factura {detail.doc_number || `${detail.series}-${detail.number}/${detail.year}`}
            </h3>
            <p className="text-sm text-slate-400">
              {detail.issue_date} · estado {detail.status}
              {detail.voided_at ? ` · anulada: ${detail.void_reason ?? 'sin motivo'}` : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-slate-700 px-3 py-1.5 text-sm font-medium text-slate-100 hover:bg-slate-600"
          >
            Cerrar
          </button>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
          <Detail label="Base" value={detail.total_base} />
          <Detail label="IVA" value={detail.total_tax} />
          <Detail label="Total" value={detail.total_amount} />
          <Detail label="Líneas (pedidos)" value={String(detail.lines.length)} />
        </dl>

        <pre className="mt-4 max-h-64 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-300">
          {JSON.stringify(detail.payload, null, 2)}
        </pre>
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="tabular-nums text-slate-100">{value}</dd>
    </div>
  );
}

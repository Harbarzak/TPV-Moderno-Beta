/**
 * Comanda de la mesa activa (§5.4, columna derecha del modo mesa). Las
 * líneas VIVEN en el servidor (el pedido de mesa es un borrador de ventas):
 * cada ± es un PATCH de cantidad y quitar es DELETE — el importe se
 * recarga del servidor (open_total), nunca se inventa aquí.
 */

import { useState } from 'react';
import {
  ArrowLeft,
  Loader2,
  Minus,
  Plus,
  ReceiptText,
  StickyNote,
  Trash2,
  UserRound,
  X,
} from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import {
  TABLE_STATUS_LABEL,
  formatElapsed,
  statusChipClass,
  type FloorOrderDetail,
  type FloorTable,
} from '../../lib/floor';
import { formatMoney, parseMoney, parseQty, formatQty } from '../../lib/money';
import { useCash } from '../../state/cash';
import { useFloor } from '../../state/floor';
import { useTerminal } from '../../state/terminal';
import { toast } from '../../state/toasts';

interface FloorTableTicketProps {
  table: FloorTable;
  order: FloorOrderDetail | null;
  nowMs: number;
  onBack: () => void;
  onEditSession: () => void;
  onCharge: () => void;
}

/** Paso del ±: unidades enteras o 100 g en líneas pesadas. */
function stepFor(quantity: string): number {
  return parseQty(quantity) % 1_000 === 0 ? 1_000 : 100;
}

export default function FloorTableTicket({
  table,
  order,
  nowMs,
  onBack,
  onEditSession,
  onCharge,
}: FloorTableTicketProps) {
  const reloadAll = useFloor((state) => state.reloadAll);
  const terminal = useTerminal((state) => state.terminal);
  const session = useCash((state) => state.session);

  const [busyLineId, setBusyLineId] = useState<string | null>(null);
  const [billBusy, setBillBusy] = useState(false);

  const totalCents = table.open_total === null ? null : parseMoney(table.open_total);
  const mark = table.bill_requested_at ?? table.opened_at;

  const guard = (): boolean => {
    if (!terminal) {
      toast.info('Configura primero el terminal de este puesto', { key: 'floor-line' });
      return false;
    }
    return true;
  };

  const changeQuantity = async (lineId: string, nextMilli: number) => {
    if (!guard() || !order) return;
    setBusyLineId(lineId);
    try {
      if (nextMilli <= 0) {
        await apiFetch(`/api/v1/sales/orders/${order.id}/lines/${lineId}`, { method: 'DELETE' });
      } else {
        await apiFetch(`/api/v1/sales/orders/${order.id}/lines/${lineId}`, {
          method: 'PATCH',
          body: JSON.stringify({ quantity: formatQty(nextMilli) }),
        });
      }
      await reloadAll();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo modificar la línea', { key: 'floor-line', detail });
    } finally {
      setBusyLineId(null);
    }
  };

  const toggleBill = async () => {
    if (!table.order_id) return;
    setBillBusy(true);
    try {
      await apiFetch(
        `/api/v1/restaurant/orders/${table.order_id}/bill${table.status === 'bill' ? '/cancel' : ''}`,
        { method: 'POST' },
      );
      toast.success(
        table.status === 'bill' ? 'Cuenta retirada' : 'Cuenta pedida',
        { key: 'floor-bill' },
      );
      await reloadAll();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo actualizar la cuenta', { key: 'floor-bill', detail });
    } finally {
      setBillBusy(false);
    }
  };

  return (
    <aside className="flex w-[22rem] shrink-0 flex-col border-l border-slate-800 bg-surface-raised">
      {/* Cabecera: mesa, estado, tiempo (tabular, §3.2) y datos de sala */}
      <div className="border-b border-slate-800 px-4 pb-3 pt-3">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-semibold text-ink-muted transition hover:bg-surface-hover hover:text-ink"
        >
          <ArrowLeft className="size-4" aria-hidden /> Plano
        </button>
        <div className="mt-1 flex items-center justify-between gap-2">
          <h2 className="truncate text-2xl font-extrabold text-ink">Mesa {table.name}</h2>
          <span
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold ${statusChipClass(table.status)}`}
          >
            {TABLE_STATUS_LABEL[table.status]}
            {mark && <span className="tabular-nums">· {formatElapsed(mark, nowMs)}</span>}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-muted">
          {table.waiter && <span>Camarero: {table.waiter}</span>}
          {table.guest_count !== null && (
            <span className="flex items-center gap-1">
              <UserRound className="size-3.5" aria-hidden />
              {table.guest_count} comensales
            </span>
          )}
          {table.note && <span className="italic">«{table.note}»</span>}
        </div>
        <button
          type="button"
          onClick={onEditSession}
          className="mt-2 flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-surface-sunken text-sm font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98]"
        >
          <StickyNote className="size-4" aria-hidden /> Nota y comensales
        </button>
      </div>

      {/* Líneas del servidor */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {order === null ? (
          <p className="flex items-center gap-2 py-6 text-sm text-ink-muted">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Cargando comanda…
          </p>
        ) : order.lines.length === 0 ? (
          <p className="py-6 text-sm text-ink-muted">
            Comanda vacía: toca un producto del panel para añadirla.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {order.lines.map((line) => {
              const qtyMilli = parseQty(line.quantity);
              const step = stepFor(line.quantity);
              return (
                <li
                  key={line.id}
                  className="rounded-xl bg-surface px-3 py-2"
                  aria-busy={busyLineId === line.id}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold text-ink">{line.name}</p>
                      <p className="text-xs tabular-nums text-ink-muted">
                        {formatQty(qtyMilli)} × {line.unit_price} €
                        {line.discount_pct !== '0.00' && line.discount_pct !== '0' && ` · -${line.discount_pct} %`}
                      </p>
                      {line.notes && <p className="text-xs italic text-ink-muted">{line.notes}</p>}
                    </div>
                    <p className="shrink-0 text-sm font-bold tabular-nums text-ink">
                      {formatMoney(parseMoney(line.total))} €
                    </p>
                  </div>
                  <div className="mt-1.5 flex items-center justify-end gap-1.5">
                    {busyLineId === line.id && (
                      <Loader2 className="size-4 animate-spin text-ink-muted" aria-hidden />
                    )}
                    <button
                      type="button"
                      aria-label={`Una unidad menos de ${line.name}`}
                      onClick={() => void changeQuantity(line.id, qtyMilli - step)}
                      className="grid size-10 place-items-center rounded-lg bg-surface-sunken text-ink transition hover:bg-surface-hover active:scale-[0.98]"
                    >
                      <Minus className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Una unidad más de ${line.name}`}
                      onClick={() => void changeQuantity(line.id, qtyMilli + step)}
                      className="grid size-10 place-items-center rounded-lg bg-surface-sunken text-ink transition hover:bg-surface-hover active:scale-[0.98]"
                    >
                      <Plus className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Quitar ${line.name}`}
                      onClick={() => void changeQuantity(line.id, 0)}
                      className="grid size-10 place-items-center rounded-lg bg-surface-sunken text-ink-muted transition hover:bg-surface-hover hover:text-danger-text active:scale-[0.98]"
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Pie: cuenta pedida + cobrar (el total lo fija el servidor) */}
      <div className="border-t border-slate-800 px-4 py-3">
        <div className="flex items-baseline justify-between">
          <span className="text-sm font-medium text-ink-muted">Total comanda</span>
          <span className="text-2xl font-extrabold tabular-nums text-ink">
            {totalCents === null ? '—' : `${formatMoney(totalCents)} €`}
          </span>
        </div>
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            onClick={() => void toggleBill()}
            disabled={billBusy}
            className="flex h-12 flex-1 items-center justify-center gap-2 rounded-xl bg-surface-sunken font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98] disabled:opacity-40"
          >
            {billBusy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : table.status === 'bill' ? (
              <X className="size-4" aria-hidden />
            ) : (
              <ReceiptText className="size-4" aria-hidden />
            )}
            {table.status === 'bill' ? 'Quitar cuenta' : 'Pedir cuenta'}
          </button>
          <button
            type="button"
            onClick={onCharge}
            disabled={totalCents === null || totalCents === 0n}
            title={
              !terminal || session?.status !== 'open'
                ? 'Hace falta el terminal configurado y la caja abierta'
                : undefined
            }
            className="h-12 flex-1 rounded-xl bg-accent font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
          >
            Cobrar
          </button>
        </div>
      </div>
    </aside>
  );
}

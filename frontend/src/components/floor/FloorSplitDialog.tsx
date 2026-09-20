/**
 * División de cuenta (§5.4): por cada línea se elige CUÁNTA cantidad viaja a
 * la mesa libre destino. El servidor reparte bases/impuestos con precisión
 * decimal y corta el orden de las líneas (el cliente no inventa importes).
 */

import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Loader2, Minus, Plus } from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import {
  floorOrderDetailSchema,
  type FloorOrderDetail,
  type FloorTable,
} from '../../lib/floor';
import { formatQty, parseQty } from '../../lib/money';
import { useFloor } from '../../state/floor';
import { toast } from '../../state/toasts';

interface FloorSplitDialogProps {
  table: FloorTable;
  onDone: () => void;
  onClose: () => void;
}

/** Paso de ajuste: unidades enteras o, en pesadas, 100 g. */
function stepFor(quantity: string): number {
  return parseQty(quantity) % 1_000 === 0 ? 1_000 : 100;
}

export default function FloorSplitDialog({ table, onDone, onClose }: FloorSplitDialogProps) {
  const tables = useFloor((state) => state.tables);
  const reloadAll = useFloor((state) => state.reloadAll);
  const setActiveTable = useFloor((state) => state.setActiveTable);

  const [order, setOrder] = useState<FloorOrderDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [moves, setMoves] = useState<Record<string, number>>({});
  const [targetId, setTargetId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!table.order_id) return;
    apiFetch<unknown>(`/api/v1/sales/orders/${table.order_id}`)
      .then((raw) => {
        if (!alive) return;
        const detail = floorOrderDetailSchema.parse(raw);
        setOrder(detail);
        setMoves(Object.fromEntries(detail.lines.map((line) => [line.id, 0])));
      })
      .catch((err) => {
        if (!alive) return;
        setLoadError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [table.order_id]);

  const targets = tables.filter(
    (candidate) => candidate.active && candidate.status === 'free' && candidate.id !== table.id,
  );

  const adjust = (lineId: string, max: number, delta: number) => {
    setMoves((current) => {
      const next = Math.min(max, Math.max(0, (current[lineId] ?? 0) + delta));
      return { ...current, [lineId]: next };
    });
  };

  const split = async () => {
    if (!order || targetId === null) return;
    const selected = order.lines
      .map((line) => ({ line_id: line.id, milli: moves[line.id] ?? 0 }))
      .filter((move) => move.milli > 0);
    if (selected.length === 0) {
      toast.info('Elige cuánta cantidad llevarse de alguna línea', { key: 'floor-split' });
      return;
    }
    setSubmitting(true);
    try {
      await apiFetch(`/api/v1/restaurant/tables/${table.id}/split`, {
        method: 'POST',
        body: JSON.stringify({
          target_table_id: targetId,
          lines: selected.map((move) => ({ line_id: move.line_id, quantity: formatQty(move.milli) })),
        }),
      });
      toast.success('Cuenta dividida', { key: 'floor-split' });
      await reloadAll();
      await setActiveTable(null);
      onDone();
      onClose();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo dividir la cuenta', { key: 'floor-split', detail });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[90vh] w-[min(36rem,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="text-lg font-bold text-ink">Dividir cuenta de {table.name}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            Marca la cantidad de cada línea que pasa a la mesa destino.
          </Dialog.Description>

          {loadError && (
            <p role="alert" className="mt-3 text-sm text-danger-text">
              No se pudo cargar la comanda: {loadError}
            </p>
          )}

          {order && (
            <ul className="mt-4 flex flex-col gap-2">
              {order.lines.map((line) => {
                const qtyMilli = parseQty(line.quantity);
                const step = stepFor(line.quantity);
                const moved = moves[line.id] ?? 0;
                return (
                  <li
                    key={line.id}
                    className="flex items-center justify-between gap-3 rounded-xl bg-surface px-3 py-2"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-ink">{line.name}</span>
                      <span className="block text-xs tabular-nums text-ink-muted">
                        {formatQty(qtyMilli)} × {line.unit_price} € = {line.total} €
                      </span>
                    </span>
                    <span className="flex items-center gap-1">
                      <button
                        type="button"
                        aria-label={`Menos ${line.name}`}
                        onClick={() => adjust(line.id, qtyMilli, -step)}
                        disabled={moved === 0}
                        className="grid size-11 place-items-center rounded-lg bg-surface-sunken text-ink transition hover:bg-surface-hover active:scale-[0.98] disabled:opacity-30"
                      >
                        <Minus className="size-4" aria-hidden />
                      </button>
                      <span className="w-14 text-center text-sm font-bold tabular-nums text-ink">
                        {formatQty(moved)}
                      </span>
                      <button
                        type="button"
                        aria-label={`Más ${line.name}`}
                        onClick={() => adjust(line.id, qtyMilli, step)}
                        disabled={moved >= qtyMilli}
                        className="grid size-11 place-items-center rounded-lg bg-surface-sunken text-ink transition hover:bg-surface-hover active:scale-[0.98] disabled:opacity-30"
                      >
                        <Plus className="size-4" aria-hidden />
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          {order && (
            <>
              <p className="mt-4 text-sm font-medium text-ink-muted">Mesa destino (libre)</p>
              {targets.length === 0 ? (
                <p className="mt-1 text-sm text-ink-muted">No hay mesas libres ahora mismo.</p>
              ) : (
                <div className="mt-2 flex flex-wrap gap-2">
                  {targets.map((candidate) => (
                    <button
                      key={candidate.id}
                      type="button"
                      aria-pressed={targetId === candidate.id}
                      onClick={() => setTargetId(candidate.id)}
                      className={`h-11 rounded-xl px-4 font-semibold transition active:scale-[0.98] ${
                        targetId === candidate.id
                          ? 'bg-accent text-accent-ink'
                          : 'bg-surface-sunken text-ink hover:bg-surface-hover'
                      }`}
                    >
                      {candidate.name}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          <div className="mt-5 flex items-center justify-between gap-3">
            <Dialog.Close asChild>
              <button
                type="button"
                disabled={submitting}
                className="rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover disabled:opacity-40"
              >
                Cancelar
              </button>
            </Dialog.Close>
            <button
              type="button"
              onClick={() => void split()}
              disabled={submitting || !order || targetId === null}
              className="flex h-12 items-center gap-2 rounded-xl bg-accent px-6 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:opacity-40"
            >
              {submitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Separar
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

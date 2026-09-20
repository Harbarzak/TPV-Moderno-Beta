/**
 * Ticket actual (§4.1): vive en el cliente, persiste entre reinicios y solo
 * el cobro —CheckoutDialog— lo escribe en la base de datos. Cantidades con
 * +/−, descuento por línea con preview céntimo-exacto (la MISMA fórmula del
 * backend: money.ts ↔ app/domain/sales.py) y desglose de IVA en vivo.
 */

import { useState } from 'react';
import { Minus, Percent, Plus, Trash2, UserRound } from 'lucide-react';
import { useConnectivity } from '../lib/connectivity';
import {
  formatMoney,
  formatQty,
  lineTotalDiscounted,
  parseMoney,
  ticketTotals,
  UNIT_MILLI,
} from '../lib/money';
import { useCart } from '../state/cart';
import ConfirmDialog from './ConfirmDialog';
import CustomerDialog from './CustomerDialog';
import DiscountDialog from './DiscountDialog';

const ICON_BUTTON =
  'flex size-11 items-center justify-center rounded-lg bg-slate-600 text-slate-100 ' +
  'transition hover:bg-slate-500 active:scale-95';

interface TicketPanelProps {
  onCheckout: () => void;
}

export default function TicketPanel({ onCheckout }: TicketPanelProps) {
  const lines = useCart((state) => state.lines);
  const addQty = useCart((state) => state.addQty);
  const removeLine = useCart((state) => state.removeLine);
  const clear = useCart((state) => state.clear);
  const online = useConnectivity((state) => state.online);

  const [clearOpen, setClearOpen] = useState(false);
  const [discountLineId, setDiscountLineId] = useState<string | null>(null);
  const [customerOpen, setCustomerOpen] = useState(false);

  const totals = ticketTotals(lines);
  const discountLine = lines.find((line) => line.lineId === discountLineId) ?? null;

  return (
    <aside
      aria-label="Ticket actual"
      className="flex w-96 shrink-0 flex-col border-l border-slate-700 bg-slate-800"
    >
      <div className="flex items-center justify-between border-b border-slate-700 px-4 py-3">
        <h2 className="text-lg font-bold text-slate-100">
          Ticket {lines.length > 0 && <span className="text-sm font-normal text-slate-400">({lines.length} líneas)</span>}
        </h2>
        <button
          type="button"
          onClick={() => lines.length > 0 && setClearOpen(true)}
          disabled={lines.length === 0}
          title="Vaciar ticket"
          className={`${ICON_BUTTON} disabled:cursor-not-allowed disabled:opacity-40`}
        >
          <Trash2 className="size-5" aria-hidden />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {lines.length === 0 ? (
          <p className="p-6 text-center text-slate-400">
            Toca un producto o pasa un código de barras para empezar.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {lines.map((line) => {
              const priceCents = parseMoney(line.price);
              const hasDiscount = line.discountPct !== '0.00';
              return (
                <li
                  key={line.lineId}
                  className="flex items-center gap-2 rounded-lg bg-slate-700/70 px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 truncate text-sm font-semibold text-slate-100" title={line.name}>
                      <span className="truncate">{line.name}</span>
                      {hasDiscount && (
                        <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-300">
                          −{line.discountPct.replace('.', ',')} %
                        </span>
                      )}
                    </p>
                    <p className="text-xs text-slate-400">
                      {formatQty(line.qtyMilli)}
                      {line.weighable ? ' kg' : ' ud'} × {formatMoney(priceCents)} €
                    </p>
                  </div>
                  <span className="text-sm font-bold text-amber-300">
                    {formatMoney(lineTotalDiscounted(priceCents, line.qtyMilli, line.discountPct))} €
                  </span>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      aria-label={`Descuento de ${line.name}`}
                      onClick={() => setDiscountLineId(line.lineId)}
                      title="Descuento de línea"
                      className={ICON_BUTTON}
                    >
                      <Percent className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Restar una unidad de ${line.name}`}
                      onClick={() => addQty(line.lineId, -(line.weighable ? 100 : UNIT_MILLI))}
                      className={ICON_BUTTON}
                    >
                      <Minus className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Sumar una unidad de ${line.name}`}
                      onClick={() => addQty(line.lineId, line.weighable ? 100 : UNIT_MILLI)}
                      className={ICON_BUTTON}
                    >
                      <Plus className="size-4" aria-hidden />
                    </button>
                    <button
                      type="button"
                      aria-label={`Quitar ${line.name} del ticket`}
                      onClick={() => removeLine(line.lineId)}
                      className={`${ICON_BUTTON} bg-slate-700 hover:bg-red-700`}
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

      <div className="border-t border-slate-700 p-4">
        <button
          type="button"
          onClick={() => setCustomerOpen(true)}
          className="mb-2 flex w-full items-center gap-2 rounded-lg bg-slate-700/50 px-3 py-2 text-left text-sm text-slate-300 transition hover:bg-slate-700"
        >
          <UserRound className="size-4 shrink-0" aria-hidden />
          <span className="min-w-0 truncate">
            Cliente: <span className="font-semibold text-slate-100">sin asignar</span>
          </span>
          <span className="ml-auto shrink-0 text-xs font-medium text-amber-300">Asignar</span>
        </button>

        {totals.slices.map((slice) => (
          <p key={slice.taxRate} className="flex justify-between text-xs text-slate-400">
            <span>
              Base {slice.taxRate} % ({slice.taxCode})
            </span>
            <span>{formatMoney(slice.base)} €</span>
          </p>
        ))}
        <p className="mt-1 flex justify-between text-xs text-slate-400">
          <span>IVA</span>
          <span>{formatMoney(totals.tax)} €</span>
        </p>
        <p className="mt-2 flex items-baseline justify-between">
          <span className="text-base font-semibold text-slate-200">Total (PVP)</span>
          <span className="text-3xl font-extrabold tabular-nums text-amber-400">
            {formatMoney(totals.total)} €
          </span>
        </p>

        <button
          type="button"
          onClick={onCheckout}
          disabled={lines.length === 0 || !online}
          title={
            lines.length === 0
              ? 'El ticket está vacío'
              : !online
                ? 'El cobro necesita conexión con el servidor'
                : undefined
          }
          className="mt-4 flex h-14 w-full items-center justify-center rounded-xl bg-accent text-xl font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Cobrar {formatMoney(totals.total)} €
        </button>
        <p className="mt-2 text-center text-xs text-slate-500">
          Atajo: F4 · requiere caja abierta del terminal
        </p>
      </div>

      {discountLine && (
        <DiscountDialog line={discountLine} onClose={() => setDiscountLineId(null)} />
      )}
      {customerOpen && <CustomerDialog onClose={() => setCustomerOpen(false)} />}
      <ConfirmDialog
        open={clearOpen}
        title="Vaciar el ticket"
        description={`Se quitarán ${lines.length} ${lines.length === 1 ? 'línea' : 'líneas'} del ticket actual. Esta acción no se puede deshacer.`}
        confirmLabel="Vaciar"
        onConfirm={() => clear()}
        onOpenChange={setClearOpen}
      />
    </aside>
  );
}

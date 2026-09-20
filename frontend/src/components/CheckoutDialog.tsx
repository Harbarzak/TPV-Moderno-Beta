/**
 * Cobro del ticket (design-system.md §5.3): diálogo de 720 px SIN cierre por
 * Esc/overlay durante el proceso, pago mixto (varios métodos hasta cubrir el
 * total), entregado solo en efectivo y cambio calculado por el backend
 * (change_total). El flujo CREATE → líneas → CLOSE es reanudable: si algo
 * falla a mitad, el reintento sigue desde el paso caído con la MISMA clave de
 * idempotencia — nunca un pedido duplicado ni un doble cobro (fase 14/QA E25).
 *
 * El total cobrado lo fija el servidor en el close; nuestra suma local usa su
 * misma fórmula (money.ts ↔ app/domain/sales.py) para que quepan en exactos.
 */

import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Banknote, CheckCircle2, CreditCard, Loader2, Ticket, Wallet } from 'lucide-react';
import { ApiError, apiFetch } from '../lib/api';
import {
  buildClosePayments,
  buildLinePayloads,
  pendingCents,
  validatePayment,
  type PaymentKindApi,
  type StagedPayment,
} from '../lib/checkout';
import { amountToCents, centsToBuffer, pressAmount, type KeypadKey } from '../lib/keypad';
import { formatMoney, parseMoney, ticketTotals, type Cents } from '../lib/money';
import { paymentMethodListSchema, type PaymentMethodApi } from '../lib/schemas';
import { useCart } from '../state/cart';
import { useCash } from '../state/cash';
import { useTerminal } from '../state/terminal';
import { toast } from '../state/toasts';
import Keypad from './Keypad';

interface CheckoutDialogProps {
  onClose: () => void;
  /**
   * Cobro de MESA (fase 30): el pedido ya existe en el servidor (la comanda
   * es un borrador); aquí solo se cierra. Sin él, el flujo es el de
   * mostrador: CREATE → líneas → CLOSE sobre el carrito local.
   */
  table?: { orderId: string; label: string; totalCents: Cents };
  /** Solo modo mesa: el plano se recarga y vuelve al mapa tras cobrar. */
  onCompleted?: () => void;
}

type Phase = 'edit' | 'submitting' | 'done';

const QUICK_CASH: { label: string; cents: bigint | null }[] = [
  { label: 'Exacto', cents: null }, // = importe del pago
  { label: '5 €', cents: 500n },
  { label: '10 €', cents: 1_000n },
  { label: '20 €', cents: 2_000n },
  { label: '50 €', cents: 5_000n },
];

const METHOD_ICON: Record<PaymentKindApi, typeof Banknote> = {
  cash: Banknote,
  card: CreditCard,
  voucher: Ticket,
  credit: Wallet,
  other: Wallet,
};

export default function CheckoutDialog({ onClose, table, onCompleted }: CheckoutDialogProps) {
  const lines = useCart((state) => state.lines);
  const clearCart = useCart((state) => state.clear);
  const terminal = useTerminal((state) => state.terminal);
  const session = useCash((state) => state.session);

  const [methods, setMethods] = useState<PaymentMethodApi[] | null>(null);
  const [methodsError, setMethodsError] = useState<string | null>(null);

  const [selected, setSelected] = useState<PaymentMethodApi | null>(null);
  const [staged, setStaged] = useState<StagedPayment[]>([]);
  const [amountBuf, setAmountBuf] = useState('');
  const [tenderedBuf, setTenderedBuf] = useState('');
  const [field, setField] = useState<'amount' | 'tendered'>('amount');

  const [phase, setPhase] = useState<Phase>('edit');
  const [orderId, setOrderId] = useState<string | null>(null);
  const [linesDone, setLinesDone] = useState(0);
  const [idemKey, setIdemKey] = useState<string | null>(null);
  const [result, setResult] = useState<{ change: string; ticketNumber: string } | null>(null);

  // En modo mesa el total lo dicta el pedido del servidor (open_total).
  const total = table ? table.totalCents : ticketTotals(lines).total;
  const pending = pendingCents(total, staged);

  // Formas de pago al abrir: activas y ordenadas (la UI es la última palabra).
  useEffect(() => {
    let alive = true;
    apiFetch<{ items: unknown[] }>('/api/v1/payments/payment-methods')
      .then((response) => {
        if (!alive) return;
        const parsed = paymentMethodListSchema.parse(response).items;
        setMethods(parsed.filter((method) => method.active).sort((a, b) => a.sort_order - b.sort_order));
      })
      .catch((err) => {
        if (!alive) return;
        setMethodsError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  const selectMethod = (method: PaymentMethodApi) => {
    setSelected(method);
    setField('amount');
    setAmountBuf(centsToBuffer(pendingCents(total, staged)));
    setTenderedBuf(method.kind === 'cash' ? centsToBuffer(pendingCents(total, staged)) : '');
  };

  const onKey = (key: KeypadKey) => {
    if (field === 'amount') setAmountBuf((current) => pressAmount(current, key));
    else setTenderedBuf((current) => pressAmount(current, key));
  };

  const setTendered = (cents: bigint) => {
    setField('tendered');
    setTenderedBuf(centsToBuffer(cents));
  };

  const addPayment = () => {
    if (!selected) return;
    const amountCents = amountToCents(amountBuf) ?? 0n;
    const candidate: StagedPayment = {
      methodId: selected.id,
      methodName: selected.name,
      methodKind: selected.kind,
      amountCents,
      tenderedCents: selected.kind === 'cash' ? (amountToCents(tenderedBuf) ?? amountCents) : null,
    };
    const verdict = validatePayment(total, staged, candidate);
    if (!verdict.ok) {
      toast.error(verdict.reason, { key: 'checkout-pay' });
      return;
    }
    setStaged((current) => [...current, candidate]);
    setSelected(null);
    setAmountBuf('');
    setTenderedBuf('');
  };

  const cobrar = async () => {
    if (
      phase !== 'edit' ||
      !terminal ||
      session?.status !== 'open' ||
      staged.length === 0 ||
      pending !== 0n
    ) {
      return;
    }
    setPhase('submitting');
    const key = idemKey ?? crypto.randomUUID();
    setIdemKey(key);
    try {
      // Modo mesa: el pedido ya existe — solo cierra (mismo idempotency key).
      let currentOrderId = orderId;
      if (table) {
        currentOrderId = table.orderId;
      }
      // 1) Pedido (borrador). Con la misma clave, el reintento no duplica.
      if (!currentOrderId) {
        const order = await apiFetch<{ id: string }>('/api/v1/sales/orders', {
          method: 'POST',
          body: JSON.stringify({ terminal_id: terminal.id, order_type: 'bar' }),
        }, { idempotencyKey: `${key}-create` });
        currentOrderId = order.id;
        setOrderId(currentOrderId);
      }
      // 2) Líneas pendientes (si el fallo fue a mitad, se sigue desde aquí).
      // En modo mesa NO se tocan líneas: ya están en el pedido del servidor.
      const payloads = table ? [] : buildLinePayloads(lines);
      for (let index = linesDone; index < payloads.length; index++) {
        await apiFetch(`/api/v1/sales/orders/${currentOrderId}/lines`, {
          method: 'POST',
          body: JSON.stringify(payloads[index]),
        }, { idempotencyKey: `${key}-line-${index}` });
        setLinesDone(index + 1);
      }
      // 3) Cierre: el servidor recalcula totales, exige suma EXACTA y emite ticket.
      const close = await apiFetch<{ change_total: string; ticket: { doc_number: string } }>(
        `/api/v1/sales/orders/${currentOrderId}/close`,
        {
          method: 'POST',
          body: JSON.stringify({
            cash_session_id: session.id,
            payments: buildClosePayments(staged),
          }),
        },
        { idempotencyKey: key },
      );
      setResult({ change: close.change_total, ticketNumber: close.ticket.doc_number });
      setPhase('done');
    } catch (err) {
      setPhase('edit');
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      const withHint =
        err instanceof ApiError && err.status === 403 && lines.some((line) => line.discountPct !== '0.00')
          ? `${detail} — comprueba el permiso «orders.discount» para los descuentos aplicados.`
          : detail;
      toast.error('No se pudo completar el cobro', { key: 'checkout', detail: withHint });
    }
  };

  const newTicket = () => {
    if (table) {
      // Mesa cobrada: el plano se refresca y vuelve al mapa.
      onCompleted?.();
      onClose();
      return;
    }
    clearCart();
    onClose();
  };

  const canPay = staged.length > 0 && pending === 0n && !!terminal && session?.status === 'open';

  if (phase === 'done' && result) {
    const change = parseMoney(result.change);
    return (
      <Dialog.Root open onOpenChange={() => { /* el cobro no se cierra con Esc/overlay (§5.3) */ }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 bg-black/60" />
          <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(28rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-6 text-center shadow-2xl">
            <Dialog.Title className="flex items-center justify-center gap-2 text-2xl font-bold text-ink">
              <CheckCircle2 className="size-7 text-success" aria-hidden />
              Venta cobrada
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-ink-muted">
              {table ? `Mesa ${table.label}` : 'Ticket'}{' '}
              <span className="font-mono font-bold text-ink">{result.ticketNumber}</span>
            </Dialog.Description>
            <p className="mt-6 text-sm text-ink-muted">Cambio</p>
            <p className="text-3xl font-extrabold tabular-nums text-success">
              {change > 0n ? `${formatMoney(change)} €` : '—'}
            </p>
            <button
              type="button"
              autoFocus
              onClick={newTicket}
              className="mt-8 h-14 w-full rounded-xl bg-accent text-xl font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98]"
            >
              {table ? 'Volver al plano' : 'Nuevo ticket'}
            </button>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    );
  }

  return (
    <Dialog.Root open onOpenChange={(open) => !open && phase === 'edit' && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[94vh] w-[min(45rem,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <div className="flex items-baseline justify-between gap-4">
            <Dialog.Title className="text-lg font-bold text-ink">
              {table ? `Cobrar mesa ${table.label}` : 'Cobrar'}
            </Dialog.Title>
            <p className="text-3xl font-extrabold tabular-nums text-accent">
              {formatMoney(total)} €
            </p>
          </div>
          <Dialog.Description className="sr-only">
            Elige formas de pago hasta cubrir el total y pulsa Cobrar.
          </Dialog.Description>

          <div className="mt-4 grid gap-5 md:grid-cols-2">
            {/* Izquierda: métodos + pagos apuntados + pendiente */}
            <div className="min-w-0">
              {methods === null && !methodsError && (
                <p className="flex items-center gap-2 py-6 text-sm text-ink-muted">
                  <Loader2 className="size-4 animate-spin" aria-hidden /> Cargando formas de pago…
                </p>
              )}
              {methodsError && (
                <p role="alert" className="text-sm text-danger-text">
                  No se pudieron cargar las formas de pago: {methodsError}
                </p>
              )}
              {methods !== null && methods.length === 0 && (
                <p className="py-6 text-sm text-ink-muted">
                  Sin formas de pago activas. Se dan de alta en Administración.
                </p>
              )}

              {methods !== null && methods.length > 0 && (
                <div className="grid grid-cols-2 gap-2">
                  {methods.map((method) => {
                    const Icon = METHOD_ICON[method.kind] ?? Wallet;
                    const active = selected?.id === method.id;
                    return (
                      <button
                        key={method.id}
                        type="button"
                        onClick={() => selectMethod(method)}
                        aria-pressed={active}
                        className={`flex h-14 items-center gap-2 rounded-xl px-4 text-left font-semibold transition active:scale-[0.98] ${
                          active
                            ? 'bg-accent text-accent-ink'
                            : 'bg-surface-sunken text-ink hover:bg-surface-hover'
                        }`}
                      >
                        <Icon className="size-5 shrink-0" aria-hidden />
                        <span className="truncate">{method.name}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              {staged.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1.5">
                  {staged.map((payment, index) => (
                    <li
                      key={`${payment.methodId}-${index}`}
                      className="flex items-center justify-between gap-2 rounded-lg bg-surface px-3 py-2"
                    >
                      <span className="truncate text-sm text-ink">{payment.methodName}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-sm font-bold tabular-nums text-ink">
                          {formatMoney(payment.amountCents)} €
                        </span>
                        {phase === 'edit' && (
                          <button
                            type="button"
                            onClick={() => setStaged((current) => current.filter((_, i) => i !== index))}
                            aria-label={`Quitar pago de ${payment.methodName}`}
                            className="rounded px-1.5 text-ink-muted transition hover:text-danger-text"
                          >
                            ✕
                          </button>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              <p className="mt-3 flex items-baseline justify-between rounded-xl bg-surface px-4 py-3">
                <span className="text-sm font-medium text-ink-muted">Pendiente</span>
                <span
                  className={`text-2xl font-extrabold tabular-nums ${
                    pending === 0n ? 'text-success' : 'text-ink'
                  }`}
                >
                  {formatMoney(pending)} €
                </span>
              </p>
            </div>

            {/* Derecha: importe del pago seleccionado + keypad */}
            <div className="min-w-0">
              {!selected ? (
                <p className="rounded-xl border border-dashed border-surface-sunken px-4 py-8 text-center text-sm text-ink-muted">
                  Elige una forma de pago para introducir el importe.
                </p>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => setField('amount')}
                    className={`w-full rounded-xl px-4 py-3 text-left transition ${
                      field === 'amount' ? 'bg-surface ring-2 ring-accent-hover' : 'bg-surface'
                    }`}
                  >
                    <span className="block text-xs font-medium text-ink-muted">Importe</span>
                    <span className="block text-right text-2xl font-bold tabular-nums text-ink">
                      {amountBuf === '' ? '—' : `${amountBuf} €`}
                    </span>
                  </button>

                  {selected.kind === 'cash' && (
                    <>
                      <button
                        type="button"
                        onClick={() => setField('tendered')}
                        className={`mt-2 w-full rounded-xl px-4 py-3 text-left transition ${
                          field === 'tendered' ? 'bg-surface ring-2 ring-accent-hover' : 'bg-surface'
                        }`}
                      >
                        <span className="block text-xs font-medium text-ink-muted">Entregado (efectivo)</span>
                        <span className="block text-right text-2xl font-bold tabular-nums text-ink">
                          {tenderedBuf === '' ? '—' : `${tenderedBuf} €`}
                        </span>
                      </button>
                      <div className="mt-2 grid grid-cols-5 gap-2">
                        {QUICK_CASH.map((chip) => (
                          <button
                            key={chip.label}
                            type="button"
                            onClick={() => setTendered(chip.cents ?? amountToCents(amountBuf) ?? 0n)}
                            className="h-11 rounded-lg bg-surface-sunken text-sm font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98]"
                          >
                            {chip.label}
                          </button>
                        ))}
                      </div>
                    </>
                  )}

                  <Keypad onKey={onKey} onSubmit={addPayment} submitLabel="Añadir pago" className="mt-3" />
                </>
              )}
            </div>
          </div>

          <div className="mt-5 flex items-center justify-between gap-3">
            <Dialog.Close asChild>
              <button
                type="button"
                disabled={phase === 'submitting'}
                className="rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                Cancelar
              </button>
            </Dialog.Close>
            <button
              type="button"
              onClick={() => void cobrar()}
              disabled={!canPay || phase === 'submitting'}
              title={
                !terminal || session?.status !== 'open'
                  ? 'Hace falta el terminal configurado y la caja abierta'
                  : undefined
              }
              className="flex h-14 items-center gap-2 rounded-xl bg-accent px-8 text-xl font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {phase === 'submitting' && <Loader2 className="size-5 animate-spin" aria-hidden />}
              {phase === 'submitting' ? 'Cobrando…' : `Cobrar ${formatMoney(total)} €`}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

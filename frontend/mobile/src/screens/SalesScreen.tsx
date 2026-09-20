/**
 * Venta (flujo camarero): tocar producto → línea en el servidor (auto-save);
 * el total se LEE del pedido (GET), nunca se calcula aquí; el cobro envía el
 * total textual del servidor y el cambio lo devuelve el backend. Si no hay
 * caja abierta en el terminal, el cobro lo dice y remite a Caja.
 *
 * Offline (fase 14): sin servidor el catálogo sale de la copia guardada y
 * tocar producto ENCOLA la línea (outbox, con Idempotency-Key); el ticket
 * local se muestra ESTIMADO y etiquetado. Cobrar y anular exigen servidor —
 * se deshabilitan; el cobro SIEMPRE viaja con Idempotency-Key que se
 * reutiliza solo si el cuerpo es idéntico (§4.1: nunca un doble cobro).
 */

import { useCallback, useEffect, useState } from 'react';
import { Plus } from 'lucide-react';
import { apiFetch, ApiError } from '../lib/api';
import { loadCachedCatalog, saveCatalog } from '../lib/catalogCache';
import { useConnectivity } from '../lib/connectivity';
import { formatMoney, parseAmount } from '../lib/money';
import { sellingActionsFor } from '../lib/permissions';
import { getTerminalId, isValidUuid } from '../lib/terminal';
import {
  cashSessionSchema,
  orderCloseSchema,
  orderDetailSchema,
  orderSchema,
  paymentMethodListSchema,
  posCatalogSchema,
  type CashSession,
  type OrderClose,
  type OrderDetail,
  type PaymentMethod,
  type PosProduct,
} from '../lib/schemas';
import { useOutbox } from '../state/outbox';
import { useSale } from '../state/sale';
import { useSession } from '../state/session';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';
import Keypad from '../components/Keypad';
import type { ScreenId } from '../App';

type CheckoutState =
  | { step: 'idle' }
  | { step: 'loading' }
  | { step: 'ready'; session: CashSession; methods: PaymentMethod[]; methodId: string | null; tendered: string | '' }
  | { step: 'error'; message: string };

interface SalesScreenProps {
  onNavigate: (screen: ScreenId) => void;
}

export default function SalesScreen({ onNavigate }: SalesScreenProps) {
  const { orderId, setOrderId, localOrderId, setLocalOrderId } = useSale();
  const me = useSession((state) => state.me);
  const actions = sellingActionsFor(me?.permissions ?? []);
  const online = useConnectivity((state) => state.online);
  const outboxEntries = useOutbox((state) => state.entries);
  const outboxNotices = useOutbox((state) => state.notices);
  const enqueue = useOutbox((state) => state.enqueue);
  const dismissNotice = useOutbox((state) => state.dismissNotice);
  const linesFor = useOutbox((state) => state.linesFor);
  const estimatedTotal = useOutbox((state) => state.estimatedTotal);
  const serverIdForLocal = useOutbox((state) =>
    localOrderId !== null ? state.serverIds[localOrderId] : undefined,
  );

  // Catálogo: primero la copia guardada (si la hay), luego la red (SWR).
  const [catalog, setCatalog] = useState<PosProduct[] | null>(() => loadCachedCatalog());
  const [catalogStale, setCatalogStale] = useState(false);
  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [closed, setClosed] = useState<OrderClose | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [checkout, setCheckout] = useState<CheckoutState>({ step: 'idle' });
  // Idempotencia del cobro (§4.1): clave + cuerpo con el que voló la última
  // petición; si el reintento repite el cuerpo, viaja la MISMA clave.
  const [chargeKey, setChargeKey] = useState<{ key: string; body: string } | null>(null);
  const [voiding, setVoiding] = useState(false);
  const [voidReason, setVoidReason] = useState('');

  const terminalId = getTerminalId();
  const terminalOk = terminalId !== null && isValidUuid(terminalId);

  const loadCatalog = useCallback(async () => {
    setError(null);
    const data = posCatalogSchema.parse(await apiFetch<unknown>('/api/v1/catalog/pos'));
    setCatalog(data.products);
    saveCatalog(data.products); // copia para la próxima vez sin red (fase 14)
    setCatalogStale(false);
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    setDetail(orderDetailSchema.parse(await apiFetch<unknown>(`/api/v1/sales/orders/${id}`)));
  }, []);

  useEffect(() => {
    loadCatalog().catch((cause: unknown) => {
      // Sin respuesta: la copia guardada aguanta (solo avisa quien no la tiene).
      setCatalogStale(true);
      if (loadCachedCatalog() === null) {
        setError(cause instanceof Error ? cause.message : 'No se pudo cargar el catálogo.');
      }
    });
  }, [loadCatalog]);

  // Pedido en curso de una pestaña anterior: se relee del servidor.
  useEffect(() => {
    if (orderId !== null) {
      loadDetail(orderId).catch(() => setOrderId(null));
    } else {
      setDetail(null);
    }
  }, [orderId, loadDetail, setOrderId]);

  // El outbox creó el pedido en el servidor al volver la conexión: la fuente
  // de verdad vuelve a ser el GET y el ticket local se jubila (fase 14).
  useEffect(() => {
    if (localOrderId !== null && serverIdForLocal !== undefined) {
      setOrderId(serverIdForLocal);
      setLocalOrderId(null);
    }
  }, [localOrderId, serverIdForLocal, setOrderId, setLocalOrderId]);

  /** Offline (fase 14): apertura+línea se encolan con UUID de cliente. */
  function enqueueLine(product: PosProduct) {
    let local = localOrderId;
    if (local === null) {
      local = crypto.randomUUID();
      setLocalOrderId(local);
      enqueue({
        kind: 'create-order',
        localOrderId: local,
        terminalId,
        productId: null,
        productName: '',
        unitPrice: '',
        key: crypto.randomUUID(),
      });
    }
    enqueue({
      kind: 'add-line',
      localOrderId: local,
      terminalId,
      productId: product.id,
      productName: product.short_name ?? product.name,
      unitPrice: product.price,
      key: crypto.randomUUID(),
    });
  }

  async function addLine(product: PosProduct) {
    if (busy || !terminalOk) return;
    if (!online) {
      enqueueLine(product);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let id = orderId;
      if (id === null) {
        // POST /orders responde 201 con OrderResponse: el pedido nace en el
        // servidor (con su terminal y usuario) y aquí solo guardamos su ID.
        const created = orderSchema.parse(
          await apiFetch<unknown>('/api/v1/sales/orders', {
            method: 'POST',
            body: JSON.stringify({ terminal_id: terminalId }),
          }),
        );
        id = created.id;
        setOrderId(created.id);
      }
      await apiFetch(`/api/v1/sales/orders/${id}/lines`, {
        method: 'POST',
        body: JSON.stringify({ product_id: product.id, quantity: '1' }),
      });
      await loadDetail(id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo añadir el producto.');
    } finally {
      setBusy(false);
    }
  }

  async function openCheckout() {
    if (detail?.total_amount === null || detail === null || !online) return;
    setCheckout({ step: 'loading' });
    try {
      const rawSession = await apiFetch<unknown>(
        `/api/v1/cash/sessions/current?terminal_id=${terminalId}`,
      );
      const session = cashSessionSchema.parse(rawSession);
      const rawMethods = await apiFetch<unknown>('/api/v1/admin/payment-methods');
      const methods = paymentMethodListSchema.parse(rawMethods).items.filter((m) => m.active);
      if (methods.length === 0) {
        setCheckout({ step: 'error', message: 'No hay formas de pago activas.' });
        return;
      }
      setCheckout({ step: 'ready', session, methods, methodId: methods[0]?.id ?? null, tendered: '' });
    } catch (cause) {
      const message =
        cause instanceof ApiError && cause.status === 404
          ? 'No hay caja abierta en este terminal. Ábrela en la sección Caja.'
          : cause instanceof Error
            ? cause.message
            : 'No se pudo preparar el cobro.';
      setCheckout({ step: 'error', message });
    }
  }

  async function confirmCharge() {
    if (checkout.step !== 'ready' || checkout.methodId === null || busy || !online) return;
    const current = detail;
    const total = current?.total_amount;
    if (current === null || current === undefined || total === null || total === undefined) return;
    const method = checkout.methods.find((m) => m.id === checkout.methodId);
    const tendered =
      method?.kind === 'cash' && checkout.tendered !== '' ? parseAmount(checkout.tendered) : null;
    const body = JSON.stringify({
      cash_session_id: checkout.session.id,
      payments: [
        {
          payment_method_id: checkout.methodId,
          amount: total,
          ...(tendered !== null && method?.kind === 'cash' ? { tendered } : {}),
        },
      ],
    });
    // Idempotencia del cobro (fase 14 · §4.1): el móvil SIEMPRE envía la
    // cabecera. Si el reintento repite el MISMO cuerpo (la red cayó tras
    // enviar la primera), viaja la MISMA clave y el servidor responde el
    // replay — nunca dos cobros. Si el cuerpo cambia, clave nueva.
    const key =
      chargeKey !== null && chargeKey.body === body ? chargeKey.key : crypto.randomUUID();
    setChargeKey({ key, body });
    setBusy(true);
    try {
      const result = orderCloseSchema.parse(
        await apiFetch<unknown>(`/api/v1/sales/orders/${current.id}/close`, {
          method: 'POST',
          headers: { 'Idempotency-Key': key },
          body,
        }),
      );
      setChargeKey(null);
      setCheckout({ step: 'idle' });
      setDetail(null);
      setOrderId(null);
      setClosed(result);
    } catch (cause) {
      if (cause instanceof ApiError) {
        // Rechazo de negocio con respuesta del servidor: un reintento sería
        // OTRO intento (y con la misma clave chocaría con el 409 de reuse).
        setChargeKey(null);
      }
      setCheckout({
        step: 'error',
        message: cause instanceof Error ? cause.message : 'El cobro fue rechazado.',
      });
    } finally {
      setBusy(false);
    }
  }

  async function confirmVoid() {
    if (orderId === null || busy || !online) return;
    setBusy(true);
    try {
      await apiFetch(`/api/v1/sales/orders/${orderId}/void`, {
        method: 'POST',
        body: JSON.stringify({ reason: voidReason }),
      });
      setVoiding(false);
      setVoidReason('');
      setDetail(null);
      setOrderId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo anular el pedido.');
    } finally {
      setBusy(false);
    }
  }

  // -- render ----------------------------------------------------------------
  if (closed !== null) {
    return (
      <Screen title="Venta cobrada">
        <div className="card text-center">
          <p className="text-4xl">✅</p>
          <p className="mt-2 text-lg font-semibold text-slate-800">
            Ticket {closed.ticket.doc_number}
          </p>
          {parseMoney(closed.change_total) && (
            <p className="mt-1 text-sm text-slate-500">
              Cambio a devolver: <strong>{formatMoney(closed.change_total)}</strong>
            </p>
          )}
          <button
            type="button"
            className="btn-primary mt-4 w-full"
            onClick={() => setClosed(null)}
          >
            Nueva venta
          </button>
        </div>
      </Screen>
    );
  }

  if (!terminalOk) {
    return (
      <Screen title="Venta">
        <div className="card text-center">
          <p className="text-sm text-slate-600">
            Configura el UUID del terminal para vender desde este móvil.
          </p>
          <button type="button" className="btn-primary mt-3 w-full" onClick={() => onNavigate('settings')}>
            Ir a Ajustes
          </button>
        </div>
      </Screen>
    );
  }

  // Ticket LOCAL (offline, fase 14): líneas encoladas en el outbox, total
  // ESTIMADO con los precios del catálogo guardado — el servidor lo
  // confirmará al reconectar. Cobrar/anular requieren servidor: no están.
  if (localOrderId !== null && orderId === null) {
    const lines = linesFor(localOrderId);
    const estimated = estimatedTotal(localOrderId);
    const pending = outboxEntries.filter((entry) => entry.localOrderId === localOrderId).length;
    return (
      <Screen title={`Ticket sin conexión · ${pending} en cola`}>
        <div className="mb-3 rounded-xl bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">
          Este ticket vive en el móvil y se enviará al servidor al volver la
          conexión (en orden, sin duplicados). El total es un{' '}
          <strong>estimado</strong>: lo recalcula el servidor al cobrar.
        </div>
        {lines.length === 0 ? (
          <EmptyNote>Toca productos para añadir líneas al ticket local.</EmptyNote>
        ) : (
          <>
            <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
              {lines.map((line, index) => (
                <li
                  key={`${line.name}-${index}`}
                  className="flex items-center justify-between gap-3 px-4 py-3"
                >
                  <p className="truncate text-sm font-medium text-slate-800">{line.name}</p>
                  <p className="shrink-0 text-sm font-semibold text-slate-800">
                    {formatMoney(line.total)}
                  </p>
                </li>
              ))}
            </ul>
            <div className="card mt-3 text-center">
              <p className="text-sm text-slate-500">Total estimado</p>
              <p className="text-3xl font-bold text-teal-700" aria-live="polite">
                {estimated === null ? formatMoney('0.00') : formatMoney(estimated)}
              </p>
            </div>
          </>
        )}
        <p className="mt-4 mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Añadir productos
        </p>
        {catalog === null ? (
          <EmptyNote>
            Sin catálogo guardado en este móvil: cuando vuelva la conexión se
            descargará para la próxima vez.
          </EmptyNote>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {catalog.map((product) => (
              <button
                key={product.id}
                type="button"
                onClick={() => enqueueLine(product)}
                className="card flex min-h-[76px] flex-col items-start justify-between text-left active:bg-teal-50"
              >
                <span className="text-sm font-medium leading-tight text-slate-800">
                  {product.short_name ?? product.name}
                </span>
                <span className="flex items-center gap-1 text-xs font-semibold text-teal-700">
                  <Plus size={12} aria-hidden />
                  {formatMoney(product.price)}
                </span>
              </button>
            ))}
          </div>
        )}
      </Screen>
    );
  }

  if (checkout.step !== 'idle' && detail !== null) {
    return (
      <Screen title="Cobrar">
        {checkout.step === 'loading' && <Spinner label="Preparando cobro…" />}
        {checkout.step === 'error' && (
          <ErrorBox
            message={checkout.message}
            onRetry={() => setCheckout({ step: 'idle' })}
          />
        )}
        {checkout.step === 'ready' && (
          <div className="space-y-3">
            <div className="card text-center">
              <p className="text-sm text-slate-500">Total del pedido (servidor)</p>
              <p className="text-3xl font-bold text-teal-700">
                {formatMoney(detail.total_amount ?? '0.00')}
              </p>
            </div>
            <div className="space-y-2">
              {checkout.methods.map((method) => (
                <button
                  key={method.id}
                  type="button"
                  onClick={() => setCheckout({ ...checkout, methodId: method.id })}
                  className={`btn-touch w-full justify-start border ${
                    checkout.methodId === method.id
                      ? 'border-teal-600 bg-teal-50 text-teal-800'
                      : 'border-slate-300 bg-white text-slate-700'
                  }`}
                >
                  {method.name}
                </button>
              ))}
            </div>
            {checkout.methods.find((m) => m.id === checkout.methodId)?.kind === 'cash' && (
              <div className="card">
                <p className="text-sm text-slate-600">
                  Entregado (vacío = importe justo)
                </p>
                <p className="mt-1 text-2xl font-semibold" aria-live="polite">
                  {checkout.tendered === '' ? '—' : `${checkout.tendered} €`}
                </p>
                <div className="mt-2">
                  <Keypad
                    onDigit={(digit) =>
                      setCheckout((current) =>
                        current.step === 'ready'
                          ? { ...current, tendered: appendAmount(current.tendered, digit) }
                          : current,
                      )
                    }
                    onDot={() =>
                      setCheckout((current) =>
                        current.step === 'ready'
                          ? { ...current, tendered: appendAmount(current.tendered, ',') }
                          : current,
                      )
                    }
                    onBackspace={() =>
                      setCheckout((current) =>
                        current.step === 'ready'
                          ? { ...current, tendered: current.tendered.slice(0, -1) }
                          : current,
                      )
                    }
                  />
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button type="button" className="btn-secondary" onClick={() => setCheckout({ step: 'idle' })}>
                Volver
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={busy || checkout.methodId === null || !online}
                title={online ? undefined : 'El cobro requiere conexión con el servidor.'}
                onClick={() => void confirmCharge()}
              >
                Cobrar
              </button>
            </div>
          </div>
        )}
      </Screen>
    );
  }

  if (orderId !== null && detail !== null) {
    return (
      <Screen
        title={`Pedido · ${formatMoney(detail.total_amount ?? '0.00')}`}
        action={
          actions.charge && detail.status === 'draft' ? (
            <button
              type="button"
              className="btn-primary px-4"
              disabled={!online}
              title={online ? undefined : 'El cobro requiere conexión con el servidor.'}
              onClick={() => void openCheckout()}
            >
              Cobrar
            </button>
          ) : undefined
        }
      >
        {error && <ErrorBox message={error} />}
        {detail.status !== 'draft' && (
          <p className="mb-3 rounded-xl bg-slate-100 p-3 text-center text-sm text-slate-600">
            Estado: {detail.status}. Este pedido ya no admite cambios.
          </p>
        )}
        {detail.lines.length === 0 ? (
          <EmptyNote>Toca productos del catálogo para añadir líneas.</EmptyNote>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
            {detail.lines.map((line) => (
              <li key={line.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-slate-800">{line.name}</p>
                  <p className="text-xs text-slate-500">
                    {formatQty(line.quantity)} × {formatMoney(line.unit_price)}
                  </p>
                </div>
                <p className="shrink-0 text-sm font-semibold text-slate-800">
                  {formatMoney(line.total)}
                </p>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-4 space-y-2">
          {detail.status === 'draft' && (
            <>
              <button type="button" className="btn-secondary w-full" onClick={() => setOrderId(null)}>
                + Añadir productos
              </button>
              {actions.voidOrder && (
                <button
                  type="button"
                  className="btn-danger w-full"
                  disabled={!online}
                  title={online ? undefined : 'La anulación requiere conexión con el servidor.'}
                  onClick={() => setVoiding(true)}
                >
                  Anular pedido
                </button>
              )}
            </>
          )}
        </div>

        {voiding && (
          <div className="fixed inset-0 z-10 grid place-items-center bg-slate-900/50 p-6">
            <div className="card w-full max-w-sm">
              <p className="font-semibold text-slate-800">Anular pedido</p>
              <p className="mt-1 text-xs text-slate-500">El motivo queda registrado en el servidor.</p>
              <textarea
                className="input-base mt-2 min-h-20"
                placeholder="Motivo de anulación"
                value={voidReason}
                onChange={(event) => setVoidReason(event.target.value)}
              />
              <div className="mt-3 grid grid-cols-2 gap-2">
                <button type="button" className="btn-secondary" onClick={() => setVoiding(false)}>
                  Cancelar
                </button>
                <button
                  type="button"
                  className="btn-danger"
                  disabled={busy || voidReason.trim().length === 0}
                  onClick={() => void confirmVoid()}
                >
                  Anular
                </button>
              </div>
            </div>
          </div>
        )}
      </Screen>
    );
  }

  // Catálogo (sin pedido abierto)
  return (
    <Screen title="Vender">
      {outboxNotices.length > 0 && (
        <div className="mb-3 space-y-1">
          {outboxNotices.map((notice, index) => (
            <div
              key={`${notice}-${index}`}
              className="flex items-start justify-between gap-2 rounded-xl bg-amber-50 p-2 text-xs text-amber-900"
            >
              <span>{notice}</span>
              <button
                type="button"
                aria-label="Descartar aviso"
                className="shrink-0 font-semibold text-amber-700"
                onClick={() => dismissNotice(index)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      {catalogStale && catalog !== null && online && (
        <p className="mb-3 rounded-xl bg-slate-100 p-2 text-center text-xs text-slate-500">
          Mostrando la copia guardada: no se pudo refrescar el catálogo.
        </p>
      )}
      {error && <ErrorBox message={error} onRetry={() => void loadCatalog()} />}
      {catalog === null ? (
        <Spinner />
      ) : catalog.length === 0 ? (
        <EmptyNote>Catálogo vacío.</EmptyNote>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {catalog.map((product) => (
            <button
              key={product.id}
              type="button"
              disabled={busy}
              onClick={() => void addLine(product)}
              className="card flex min-h-[76px] flex-col items-start justify-between text-left active:bg-teal-50"
            >
              <span className="text-sm font-medium leading-tight text-slate-800">
                {product.short_name ?? product.name}
              </span>
              <span className="flex items-center gap-1 text-xs font-semibold text-teal-700">
                <Plus size={12} aria-hidden />
                {formatMoney(product.price)}
              </span>
            </button>
          ))}
        </div>
      )}
    </Screen>
  );
}

// -- helpers locales (solo sintaxis de entrada) --------------------------------
function appendAmount(current: string, chunk: string): string {
  if (chunk === ',') return current.includes(',') || current === '' ? current : `${current},`;
  return current.length < 8 ? current + chunk : current;
}

function formatQty(quantity: string): string {
  const value = Number(quantity);
  return Number.isNaN(value) ? quantity : String(value);
}

function parseMoney(value: string): boolean {
  return /^\d{1,10}\.\d{2}$/.test(value);
}

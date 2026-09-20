/**
 * Pedidos abiertos (borradores) del terminal/sala: lista del SERVIDOR con su
 * total textual. Abrir uno solo apunta el ticket en curso — todo lo demás
 * (líneas, cobro, anulación) vive en la pestaña Venta.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';
import { formatMoney } from '../lib/money';
import { orderListSchema, type Order } from '../lib/schemas';
import { useSale } from '../state/sale';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';
import type { ScreenId } from '../App';

const STATUS_LABEL: Record<string, string> = {
  draft: 'Abierto',
  paid: 'Cobrado',
  voided: 'Anulado',
};

export default function OrdersScreen({ onNavigate }: { onNavigate: (screen: ScreenId) => void }) {
  const setOrderId = useSale((state) => state.setOrderId);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const data = orderListSchema.parse(
      await apiFetch<unknown>('/api/v1/sales/orders?status=draft&limit=50'),
    );
    setOrders(data.items);
  }, []);

  useEffect(() => {
    load().catch((cause: unknown) =>
      setError(cause instanceof Error ? cause.message : 'No se pudieron cargar los pedidos.'),
    );
  }, [load]);

  return (
    <Screen title="Pedidos" action={<RefreshButton onLoad={load} />}>
      {error && <ErrorBox message={error} onRetry={() => void load()} />}
      {orders === null ? (
        <Spinner />
      ) : orders.length === 0 ? (
        <EmptyNote>No hay pedidos abiertos.</EmptyNote>
      ) : (
        <ul className="space-y-2">
          {orders.map((order) => (
            <li key={order.id}>
              <button
                type="button"
                onClick={() => {
                  setOrderId(order.id);
                  onNavigate('sales');
                }}
                className="card flex w-full items-center justify-between text-left active:bg-slate-50"
              >
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {new Date(order.created_at).toLocaleTimeString('es-ES', {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                    {' · '}
                    {order.order_type}
                  </p>
                  <p className="text-xs text-slate-500">{STATUS_LABEL[order.status] ?? order.status}</p>
                </div>
                <p className="text-base font-semibold text-teal-700">
                  {order.total_amount === null ? '—' : formatMoney(order.total_amount)}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Screen>
  );
}

function RefreshButton({ onLoad }: { onLoad: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      className="btn-secondary px-3 text-sm"
      disabled={busy}
      onClick={() => {
        setBusy(true);
        onLoad().finally(() => setBusy(false));
      }}
    >
      {busy ? '…' : '↻'}
    </button>
  );
}

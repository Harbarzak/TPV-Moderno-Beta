/**
 * Mesas (fase 30 plano · fase 31 camarero): el móvil ya no solo consulta el
 * plano — tocar una mesa LIBRE la abre (crea el pedido de sala, idempotente)
 * y lleva directo al pedido; tocar una ABIERTA o CON CUENTA entra en su
 * pedido. El estado usa el semáforo del diseño (§3.2): SIEMPRE texto + tiempo
 * transcurrido en cifras tabulares, nunca solo color. Refresco por sondeo.
 *
 * Honestidad offline: la sala es estado compartido (mostrador, sala, KDS);
 * abrir o entrar en una mesa sin conexión no se encola — se avisa y se queda
 * en lectura.
 */

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';
import { formatMoney } from '../lib/money';
import { getTerminalId, isValidUuid } from '../lib/terminal';
import { useConnectivity } from '../lib/connectivity';
import { orderSchema } from '../lib/schemas';
import { z } from 'zod';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';
import Keypad from '../components/Keypad';

const tableSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  seats: z.number().int().positive(),
  status: z.enum(['free', 'open', 'bill']),
  order_id: z.string().uuid().nullable(),
  guest_count: z.number().int().positive().nullable(),
  waiter: z.string().nullable(),
  opened_at: z.string().nullable(),
  bill_requested_at: z.string().nullable(),
  open_total: z.string().nullable(),
});

// GET /restaurant/tables devuelve {items}, no un array pelado (fase 30).
const tablesSchema = z.object({ items: z.array(tableSchema) });

type FloorTable = z.infer<typeof tableSchema>;

const STATUS_LABEL: Record<FloorTable['status'], string> = {
  free: 'Libre',
  open: 'Abierta',
  bill: 'Cuenta pedida',
};

/** Semáforo en tema claro: mismo texto que el mostrador, tonos legibles. */
const STATUS_CHIP: Record<FloorTable['status'], string> = {
  free: 'bg-emerald-100 text-emerald-800',
  open: 'bg-amber-100 text-amber-800',
  bill: 'bg-sky-100 text-sky-800',
};

/** «5 min» / «1 h 05» — tabular, con reloj parado a 0 si el reloj va mal. */
function formatElapsed(fromIso: string, nowMs: number): string {
  const minutes = Math.max(0, Math.floor((nowMs - Date.parse(fromIso)) / 60_000));
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')}`;
}

const REFRESH_MS = 15_000;

/** Comensales tecleados: entero 1-999 o nada (opcional en TableOpen). */
function parseGuests(raw: string): number | null {
  if (!/^\d{1,3}$/.test(raw)) return null;
  const value = Number.parseInt(raw, 10);
  return value >= 1 ? value : null;
}

export default function TablesScreen({
  onEnterTable,
}: {
  onEnterTable: (orderId: string, label: string) => void;
}) {
  const online = useConnectivity((state) => state.online);
  const [tables, setTables] = useState<FloorTable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [opening, setOpening] = useState<FloorTable | null>(null);
  const [guestsRaw, setGuestsRaw] = useState('');
  const [openingBusy, setOpeningBusy] = useState(false);
  const [openingError, setOpeningError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const data = tablesSchema.parse(await apiFetch<unknown>('/api/v1/restaurant/tables'));
    setTables(data.items);
  }, []);

  useEffect(() => {
    load().catch((cause: unknown) =>
      setError(cause instanceof Error ? cause.message : 'No se pudieron cargar las mesas.'),
    );
    const refresh = setInterval(() => {
      load().catch(() => {
        /* el siguiente intento lo reintentará; el error visible no parpadea */
      });
    }, REFRESH_MS);
    const clock = setInterval(() => setNowMs(Date.now()), 30_000);
    return () => {
      clearInterval(refresh);
      clearInterval(clock);
    };
  }, [load]);

  const tapTable = (table: FloorTable) => {
    setError(null);
    if (!online) {
      setError('Sin conexión: la sala necesita servidor para abrir o seguir un pedido.');
      return;
    }
    if (table.status === 'free') {
      setGuestsRaw('');
      setOpeningError(null);
      setOpening(table);
    } else if (table.order_id !== null) {
      onEnterTable(table.order_id, table.name);
    }
  };

  const confirmOpen = () => {
    if (opening === null || openingBusy) return;
    const terminalId = getTerminalId();
    if (terminalId === null || !isValidUuid(terminalId)) {
      setOpeningError('Configura un terminal válido en Ajustes para abrir mesas.');
      return;
    }
    const body: Record<string, string> = { terminal_id: terminalId };
    const guests = parseGuests(guestsRaw);
    if (guests !== null) body.guest_count = String(guests);
    setOpeningBusy(true);
    setOpeningError(null);
    (async () => {
      try {
        // Abrir la mesa CREA el pedido (201): la sesión de mesa es un borrador
        // de venta. Idempotency-Key: un doble toque no abre dos pedidos.
        const order = orderSchema.parse(
          await apiFetch<unknown>(`/api/v1/restaurant/tables/${opening.id}/open`, {
            method: 'POST',
            headers: { 'Idempotency-Key': crypto.randomUUID() },
            body: JSON.stringify(body),
          }),
        );
        setOpening(null);
        onEnterTable(order.id, opening.name);
      } catch (cause: unknown) {
        setOpeningError(cause instanceof Error ? cause.message : 'No se pudo abrir la mesa.');
        void load().catch(() => {
          /* el sondeo periódico reintenta */
        });
      } finally {
        setOpeningBusy(false);
      }
    })();
  };

  const guestsValid = guestsRaw === '' || parseGuests(guestsRaw) !== null;

  return (
    <Screen title="Mesas">
      {error && <ErrorBox message={error} onRetry={() => void load()} />}
      {tables === null ? (
        <Spinner />
      ) : tables.length === 0 ? (
        <EmptyNote>No hay mesas configuradas.</EmptyNote>
      ) : (
        <div className="grid grid-cols-3 gap-2">
          {tables.map((table) => {
            const mark = table.bill_requested_at ?? table.opened_at;
            const tappable = table.status === 'free' || table.order_id !== null;
            return (
              <button
                key={table.id}
                type="button"
                onClick={() => tapTable(table)}
                aria-label={
                  table.status === 'free'
                    ? `Abrir ${table.name}`
                    : `Ir al pedido de ${table.name}`
                }
                className="card flex flex-col gap-1.5 p-3 text-left transition active:scale-[0.98] disabled:opacity-60"
                disabled={!tappable}
              >
                <span className="truncate text-base font-bold text-slate-800">{table.name}</span>
                <span
                  className={`self-start rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_CHIP[table.status]}`}
                >
                  {STATUS_LABEL[table.status]}
                  {mark && <span className="tabular-nums"> · {formatElapsed(mark, nowMs)}</span>}
                </span>
                <span className="text-[11px] tabular-nums text-slate-500">
                  {table.guest_count !== null ? `${table.guest_count}/${table.seats}` : `${table.seats} plazas`}
                  {table.open_total !== null && (
                    <span className="font-semibold text-slate-700"> · {formatMoney(table.open_total)}</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {/* Apertura de mesa: comensales opcionales y confirmación */}
      {opening !== null && (
        <div
          className="fixed inset-0 z-40 flex flex-col justify-end bg-slate-900/40"
          onClick={openingBusy ? undefined : () => setOpening(null)}
        >
          <div
            className="mx-auto w-full max-w-md rounded-t-2xl bg-white p-4"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="text-base font-bold text-slate-800">Abrir {opening.name}</h2>
            <p className="mb-2 text-xs text-slate-500">
              Comensales (opcional, {opening.seats} plazas)
            </p>
            <p className="mb-2 text-center text-3xl font-bold tabular-nums text-slate-800">
              {guestsRaw === '' ? '—' : guestsRaw}
            </p>
            <Keypad
              hideDot
              onDigit={(digit) => setGuestsRaw((raw) => (raw.length < 3 ? raw + digit : raw))}
              onBackspace={() => setGuestsRaw((raw) => raw.slice(0, -1))}
            />
            {openingError && <ErrorBox message={openingError} />}
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="btn-secondary flex-1"
                disabled={openingBusy}
                onClick={() => setOpening(null)}
              >
                Cancelar
              </button>
              <button
                type="button"
                className="btn-primary flex-1"
                disabled={!guestsValid || openingBusy}
                onClick={confirmOpen}
              >
                Abrir mesa
              </button>
            </div>
          </div>
        </div>
      )}
    </Screen>
  );
}

/**
 * Tablero de cocina (fase 32): cuatro columnas NUEVO / PREPARANDO / LISTO /
 * SERVIDO, filtro por estación, urgencias, tiempos con semáforo de alerta,
 * avance tocando la línea, botones masivos por comanda y reimpresión KOT.
 *
 * En vivo por WebSocket (tema «kds»): un evento ⇒ releer el tablero (el sobre
 * nunca transporta estado). Sin hub, sondeo de respaldo cada 15 s. El estado
 * de verdad SIEMPRE llega por GET /kds/board: aquí no se aplica negocio, las
 * transiciones las valida el servidor (409 ⇒ aviso, la tarjeta no cambia).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Bell, BellOff, LogOut, Printer } from 'lucide-react';
import { ApiError, apiFetch, getToken } from '../lib/api';
import {
  boardSchema,
  stationListSchema,
  type BoardLine,
  type BoardTicket,
  type Station,
} from '../lib/schemas';
import {
  alertLevel,
  allLinesIn,
  countLines,
  elapsedMinutes,
  filterByStation,
  formatQuantity,
  nextLineStatus,
  splitByStatus,
  ticketDestination,
  type ColumnKey,
} from '../lib/board';
import { connectHub, type HubState } from '../lib/ws';
import { useSession } from '../state/session';

const POLL_MS = 15_000;
const TICK_MS = 30_000;
const WS_TOPICS = ['kds'];

const COLUMN_STYLES: Record<ColumnKey, string> = {
  pending: 'border-sky-500/40',
  preparing: 'border-amber-500/40',
  ready: 'border-emerald-500/40',
  served: 'border-slate-800',
};

const LINE_CHIP: Record<string, string> = {
  pending: 'bg-sky-950 text-sky-300',
  preparing: 'bg-amber-950 text-amber-300',
  ready: 'bg-emerald-950 text-emerald-300',
  served: 'bg-slate-800 text-slate-400',
  cancelled: 'bg-slate-800 text-slate-500',
};

const LINE_LABEL: Record<string, string> = {
  pending: 'NUEVO',
  preparing: 'PREP.',
  ready: 'LISTO',
  served: 'SERV.',
  cancelled: 'ANUL.',
};

const HUB_BADGE: Record<HubState, { text: string; cls: string }> = {
  live: { text: '● EN VIVO', cls: 'bg-emerald-950 text-emerald-300' },
  connecting: { text: '○ CONECTANDO', cls: 'bg-amber-950 text-amber-300' },
  down: { text: '○ SIN HUB · 15 s', cls: 'bg-slate-800 text-slate-400' },
};

const ALERT_BORDER = {
  ok: '',
  warn: 'border-amber-400',
  late: 'border-2 border-rose-500 animate-pulse',
} as const;

/** Aviso sonoro corto (sin ficheros: oscilador WebAudio). */
function beep(): void {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'square';
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.6);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.6);
    osc.onended = () => void ctx.close();
  } catch {
    // Sin audio disponible: la alerta visual sigue.
  }
}

interface TicketCardProps {
  ticket: BoardTicket;
  column: ColumnKey;
  now: Date;
  onLine: (line: BoardLine) => void;
  onBulk: (ticket: BoardTicket, status: 'ready' | 'served') => void;
  onPriority: (ticket: BoardTicket) => void;
  onPrint: (ticket: BoardTicket) => void;
}

function TicketCard({ ticket, column, now, onLine, onBulk, onPriority, onPrint }: TicketCardProps) {
  const base = column === 'served' ? (ticket.served_at ?? ticket.created_at) : ticket.created_at;
  const minutes = elapsedMinutes(base, now);
  const level = column === 'served' ? ('ok' as const) : alertLevel(minutes);

  return (
    <article className={`card-kitchen ${COLUMN_STYLES[column]} ${ALERT_BORDER[level]}`}>
      <header className="flex items-baseline justify-between gap-2">
        <div className="flex items-center gap-2">
          {ticket.priority === 1 && (
            <span className="rounded bg-rose-600 px-1.5 py-0.5 text-xs font-bold text-white">
              URGENTE
            </span>
          )}
          <h3 className="text-lg font-bold leading-none">{ticketDestination(ticket)}</h3>
        </div>
        <time
          className={`font-mono text-sm tabular-nums ${level === 'late' ? 'text-rose-400' : level === 'warn' ? 'text-amber-400' : 'text-slate-400'}`}
        >
          {minutes}′
        </time>
      </header>

      <ul className="flex flex-col gap-1.5">
        {ticket.lines.map((line) => {
          const target = nextLineStatus(line.status);
          return (
            <li key={line.id}>
              <button
                type="button"
                disabled={target === null}
                onClick={() => onLine(line)}
                className={`flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left transition ${
                  target === null
                    ? 'opacity-50'
                    : 'hover:bg-slate-800 active:bg-slate-700'
                }`}
                title={target === null ? undefined : `Marcar ${LINE_LABEL[target]}`}
              >
                <span className="font-mono text-base font-bold tabular-nums text-orange-400">
                  {formatQuantity(line.quantity)}×
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-base leading-tight">{line.name}</span>
                  {line.notes !== null && line.notes !== '' && (
                    <span className="mt-0.5 block text-sm font-medium text-amber-300">
                      ⓘ {line.notes}
                    </span>
                  )}
                </span>
                <span className={`line-status shrink-0 ${LINE_CHIP[line.status] ?? LINE_CHIP.cancelled}`}>
                  {LINE_LABEL[line.status] ?? '?'}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      <footer className="mt-auto grid grid-cols-3 gap-1.5">
        {column === 'ready' ? (
          <button
            type="button"
            className="btn-touch col-span-2 bg-emerald-700 text-white active:bg-emerald-800"
            onClick={() => onBulk(ticket, 'served')}
          >
            Servido
          </button>
        ) : column !== 'served' ? (
          <button
            type="button"
            className="btn-touch col-span-2 bg-emerald-700 text-white active:bg-emerald-800"
            disabled={allLinesIn(ticket.lines, 'ready')}
            onClick={() => onBulk(ticket, 'ready')}
          >
            Todo listo
          </button>
        ) : null}
        <button
          type="button"
          className="btn-touch border border-slate-700 text-slate-300 active:bg-slate-800"
          title="Reimprimir KOT"
          onClick={() => onPrint(ticket)}
        >
          <Printer size={16} aria-hidden />
        </button>
        {column !== 'served' && (
          <button
            type="button"
            className={`btn-touch col-span-3 ${
              ticket.priority === 1
                ? 'bg-rose-800 text-rose-100 active:bg-rose-900'
                : 'border border-rose-800 text-rose-300 active:bg-rose-950'
            }`}
            onClick={() => onPriority(ticket)}
          >
            {ticket.priority === 1 ? 'Quitar urgencia' : 'Urgente'}
          </button>
        )}
      </footer>
    </article>
  );
}

export default function BoardScreen() {
  const { me, logout } = useSession();
  const [tickets, setTickets] = useState<BoardTicket[] | null>(null);
  const [stations, setStations] = useState<Station[]>([]);
  const [stationFilter, setStationFilter] = useState<string | null>(null);
  const [hub, setHub] = useState<HubState>('connecting');
  const [muted, setMuted] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [now, setNow] = useState(() => new Date());

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hubRef = useRef<HubState>('connecting');
  const lateIds = useRef<Set<string>>(new Set());

  const showToast = useCallback((kind: 'ok' | 'error', text: string) => {
    setToast({ kind, text });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3500);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const raw = await apiFetch<unknown>('/api/v1/kds/board');
      setTickets(boardSchema.parse(raw).items);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        // api.ts ya limpió el token: fuera de la pantalla (App pinta el login).
        useSession.setState({ me: null });
        return;
      }
      showToast('error', cause instanceof Error ? cause.message : 'No se pudo cargar el tablero');
    }
  }, [showToast]);

  /** Refresco en ráfaga desdoblado (una comanda nueva dispara 2-3 eventos). */
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void refresh();
    }, 200);
  }, [refresh]);

  // Carga inicial: tablero + estaciones.
  useEffect(() => {
    void refresh();
    void apiFetch<unknown>('/api/v1/kds/stations')
      .then((raw) => setStations(stationListSchema.parse(raw).items))
      .catch(() => {
        // Sin estaciones el filtro «Todas» sigue funcionando.
      });
  }, [refresh]);

  // En vivo por WebSocket; backoff y reconexión los gobierna el cliente.
  useEffect(() => {
    const connection = connectHub({
      topics: WS_TOPICS,
      token: getToken(),
      onEvent: scheduleRefresh,
      onState: (state) => {
        hubRef.current = state;
        setHub(state);
      },
    });
    return () => connection.close();
  }, [scheduleRefresh]);

  // Sondeo de respaldo (solo sin hub) + reloj de los tiempos.
  useEffect(() => {
    const poll = setInterval(() => {
      if (hubRef.current !== 'live') void refresh();
    }, POLL_MS);
    const tick = setInterval(() => setNow(new Date()), TICK_MS);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [refresh]);

  // Alerta sonora al cruzar cualquier comanda el umbral de TARDE (una vez).
  useEffect(() => {
    if (tickets === null || muted) return;
    const late = new Set(
      tickets
        .filter(
          (ticket) =>
            ticket.status !== 'served' && alertLevel(elapsedMinutes(ticket.created_at, now)) === 'late',
        )
        .map((ticket) => ticket.id),
    );
    const isNew = [...late].some((id) => !lateIds.current.has(id));
    lateIds.current = late;
    if (isNew && late.size > 0) beep();
  }, [tickets, muted, now]);

  const board = useMemo(
    () => splitByStatus(filterByStation(tickets ?? [], stationFilter)),
    [tickets, stationFilter],
  );

  async function tapLine(line: BoardLine) {
    const target = nextLineStatus(line.status);
    if (target === null) return;
    try {
      await apiFetch(`/api/v1/kds/lines/${line.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: target }),
      });
      await refresh();
    } catch (cause) {
      showToast('error', cause instanceof Error ? cause.message : 'No se pudo actualizar la línea');
    }
  }

  async function bulk(ticket: BoardTicket, status: 'ready' | 'served') {
    try {
      await apiFetch(`/api/v1/kds/tickets/${ticket.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      });
      await refresh();
    } catch (cause) {
      showToast('error', cause instanceof Error ? cause.message : 'No se pudo actualizar la comanda');
    }
  }

  async function togglePriority(ticket: BoardTicket) {
    try {
      await apiFetch(`/api/v1/kds/tickets/${ticket.id}/priority`, {
        method: 'PATCH',
        body: JSON.stringify({ priority: ticket.priority === 1 ? 0 : 1 }),
      });
      await refresh();
    } catch (cause) {
      showToast('error', cause instanceof Error ? cause.message : 'No se pudo cambiar la prioridad');
    }
  }

  async function reprint(ticket: BoardTicket) {
    try {
      await apiFetch(`/api/v1/kds/tickets/${ticket.id}/print`, { method: 'POST' });
      showToast('ok', 'KOT en cola de impresión');
    } catch (cause) {
      showToast('error', cause instanceof Error ? cause.message : 'No se pudo reimprimir');
    }
  }

  return (
    <div className="flex h-dvh flex-col gap-2 p-2">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-bold">Cocina</h1>

        <nav className="flex flex-wrap gap-1" aria-label="Estaciones">
          <button
            type="button"
            onClick={() => setStationFilter(null)}
            className={`chip ${stationFilter === null ? 'bg-orange-600 text-white' : 'bg-slate-800 text-slate-300'}`}
          >
            Todas
          </button>
          {stations.map((station) => (
            <button
              key={station.id}
              type="button"
              onClick={() => setStationFilter(station.id)}
              className={`chip ${stationFilter === station.id ? 'bg-orange-600 text-white' : 'bg-slate-800 text-slate-300'}`}
            >
              {station.name}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <span className={`chip font-mono ${HUB_BADGE[hub].cls}`}>{HUB_BADGE[hub].text}</span>
          <button
            type="button"
            className="btn-touch border border-slate-700 px-2.5 text-slate-300"
            title={muted ? 'Activar avisos sonoros' : 'Silenciar avisos sonoros'}
            onClick={() => setMuted((current) => !current)}
          >
            {muted ? <BellOff size={18} aria-hidden /> : <Bell size={18} aria-hidden />}
          </button>
          <span className="hidden text-sm text-slate-400 sm:inline">{me?.full_name}</span>
          <button
            type="button"
            className="btn-touch border border-slate-700 px-2.5 text-slate-300"
            title="Salir"
            onClick={() => void logout()}
          >
            <LogOut size={18} aria-hidden />
          </button>
        </div>
      </header>

      {tickets === null ? (
        <p className="grid flex-1 place-items-center text-slate-400">Cargando tablero…</p>
      ) : (
        <main className="grid min-h-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {(
            [
              ['pending', board.pending],
              ['preparing', board.preparing],
              ['ready', board.ready],
              ['served', board.served],
            ] as const
          ).map(([column, columnTickets]) => (
            <section
              key={column}
              className={`flex min-h-0 flex-col rounded-xl border bg-slate-900/40 p-2 ${COLUMN_STYLES[column]}`}
            >
              <h2 className="mb-2 flex items-baseline justify-between px-1 text-sm font-bold tracking-wide text-slate-300">
                <span>{column === 'pending' ? 'NUEVO' : column === 'preparing' ? 'PREPARANDO' : column === 'ready' ? 'LISTO' : 'SERVIDO'}</span>
                <span className="font-mono text-xs tabular-nums text-slate-500">
                  {columnTickets.length}·{countLines(columnTickets)}
                </span>
              </h2>
              <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                {columnTickets.length === 0 ? (
                  <p className="py-6 text-center text-sm text-slate-600">—</p>
                ) : (
                  columnTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.id}
                      ticket={ticket}
                      column={column}
                      now={now}
                      onLine={(line) => void tapLine(line)}
                      onBulk={(t, status) => void bulk(t, status)}
                      onPriority={(t) => void togglePriority(t)}
                      onPrint={(t) => void reprint(t)}
                    />
                  ))
                )}
              </div>
            </section>
          ))}
        </main>
      )}

      {toast !== null && (
        <div
          role="status"
          className={`fixed bottom-4 left-1/2 -translate-x-1/2 rounded-lg px-4 py-2 text-sm font-medium shadow-lg ${
            toast.kind === 'ok' ? 'bg-emerald-800 text-emerald-100' : 'bg-rose-800 text-rose-100'
          }`}
        >
          {toast.text}
        </div>
      )}
    </div>
  );
}

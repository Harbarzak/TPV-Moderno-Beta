/**
 * Vista de SALA (fase 30 · Modo restaurante, §5.4): mapa 2D de mesas por
 * zonas, semáforo de estados con tiempo transcurrido, un toque para abrir o
 * entrar en una mesa y modo mesa (panel de productos + comanda del servidor
 * + cobro). Sin Three.js (§8): lienzo relativo con posiciones en % —
 * funciona igual en tablet que en escritorio.
 *
 * Refresco en vivo: el hub WS (fase 12) avisa de eventos «sales» y
 * «restaurant» ⇒ invalidar; si el canal cae, sondeo de respaldo cada 15 s.
 * Tras CADA acción propia también se recarga: la última palabra la tiene
 * siempre el servidor.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import {
  Armchair,
  ArrowLeft,
  CircleAlert,
  Loader2,
  MoreVertical,
  RadioTower,
  Search,
  Settings2,
  UserRound,
  WifiOff,
} from 'lucide-react';
import { ApiError, apiFetch, getToken } from '../lib/api';
import {
  TABLE_STATUS_LABEL,
  formatElapsed,
  openTotalCents,
  statusChipClass,
  type FloorTable,
} from '../lib/floor';
import { formatMoney } from '../lib/money';
import { connectHub } from '../lib/ws';
import type { Panel, PanelItem } from '../lib/schemas';
import { useFloor } from '../state/floor';
import { useCash } from '../state/cash';
import { useTerminal } from '../state/terminal';
import { toast } from '../state/toasts';
import FloorOpenDialog from './floor/FloorOpenDialog';
import FloorMoveDialog from './floor/FloorMoveDialog';
import FloorSplitDialog from './floor/FloorSplitDialog';
import FloorSessionDialog from './floor/FloorSessionDialog';
import FloorSettingsDialog from './floor/FloorSettingsDialog';
import FloorTableMenu, { actionsForTable, type FloorAction } from './floor/FloorTableMenu';
import FloorTableTicket from './floor/FloorTableTicket';
import CheckoutDialog from './CheckoutDialog';
import PanelNav from './PanelNav';
import PanelGrid from './PanelGrid';

const POLL_FALLBACK_MS = 15_000;
const CLOCK_TICK_MS = 30_000;

interface FloorViewProps {
  panels: Panel[];
  onBack: () => void;
}

type DialogState =
  | { kind: 'none' }
  | { kind: 'open'; table: FloorTable }
  | { kind: 'move'; table: FloorTable; mode: 'transfer' | 'merge' }
  | { kind: 'split'; table: FloorTable }
  | { kind: 'session'; table: FloorTable }
  | { kind: 'settings' };

export default function FloorView({ panels, onBack }: FloorViewProps) {
  const tables = useFloor((state) => state.tables);
  const zones = useFloor((state) => state.zones);
  const status = useFloor((state) => state.status);
  const error = useFloor((state) => state.error);
  const live = useFloor((state) => state.live);
  const load = useFloor((state) => state.load);
  const invalidate = useFloor((state) => state.invalidate);
  const setLive = useFloor((state) => state.setLive);
  const activeTableId = useFloor((state) => state.activeTableId);
  const activeOrder = useFloor((state) => state.activeOrder);
  const setActiveTable = useFloor((state) => state.setActiveTable);
  const reloadAll = useFloor((state) => state.reloadAll);

  const [zoneFilter, setZoneFilter] = useState<string>('');
  const [query, setQuery] = useState('');
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [dialog, setDialog] = useState<DialogState>({ kind: 'none' });
  const [menuTable, setMenuTable] = useState<FloorTable | null>(null);
  const [checkoutTable, setCheckoutTable] = useState<{
    orderId: string;
    label: string;
    totalCents: bigint;
  } | null>(null);

  // Modo mesa: navegación de catálogo PROPIA (no comparte panel con la venta).
  const [panelId, setPanelId] = useState<string | null>(panels[0]?.id ?? null);
  const [subpanelId, setSubpanelId] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Carga inicial + hub en vivo con sondeo de respaldo al caer.
  useEffect(() => {
    void load();
    const hub = connectHub({
      topics: ['sales', 'restaurant'],
      token: getToken(),
      onEvent: () => invalidate(),
      onState: (state) => {
        setLive(state === 'live');
        if (state === 'live') {
          stopPolling();
        } else if (pollRef.current === null) {
          pollRef.current = setInterval(() => invalidate(), POLL_FALLBACK_MS);
        }
      },
    });
    return () => {
      hub.close();
      stopPolling();
    };
  }, [load, invalidate, setLive, stopPolling]);

  // Reloj de los chips de tiempo (30 s: el chip es compacto y en minutos).
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const activeTable = tables.find((table) => table.id === activeTableId) ?? null;

  // ------------------------------------------------------------------
  // Acciones
  // ------------------------------------------------------------------
  const tableTap = useCallback(
    (table: FloorTable) => {
      if (table.status === 'free') {
        setDialog({ kind: 'open', table });
        return;
      }
      void setActiveTable(table.id);
    },
    [setActiveTable],
  );

  /** Cobrar mesa: mismos candados que el mostrador (terminal + caja abierta). */
  const startCharge = useCallback((table: FloorTable) => {
    if (!useTerminal.getState().terminal) {
      toast.info('Configura primero el terminal de este puesto', { key: 'floor-charge' });
      return;
    }
    if (useCash.getState().session?.status !== 'open') {
      toast.info('Abre la caja antes de cobrar', { key: 'floor-charge' });
      return;
    }
    const total = openTotalCents(table);
    if (table.order_id === null || total === null) return;
    setCheckoutTable({ orderId: table.order_id, label: table.name, totalCents: total });
  }, []);

  const requestBill = useCallback(
    async (table: FloorTable) => {
      if (table.order_id === null) return;
      try {
        await apiFetch(
          `/api/v1/restaurant/orders/${table.order_id}/bill${table.status === 'bill' ? '/cancel' : ''}`,
          { method: 'POST' },
        );
        toast.success(table.status === 'bill' ? 'Cuenta retirada' : 'Cuenta pedida', { key: 'floor-bill' });
      } catch (err) {
        const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
        toast.error('No se pudo actualizar la cuenta', { key: 'floor-bill', detail });
        return;
      }
      await reloadAll();
    },
    [reloadAll],
  );

  const handleMenuAction = useCallback(
    (table: FloorTable, action: FloorAction) => {
      switch (action) {
        case 'open':
          setDialog({ kind: 'open', table });
          break;
        case 'enter':
          void setActiveTable(table.id);
          break;
        case 'transfer':
        case 'merge':
          setDialog({ kind: 'move', table, mode: action });
          break;
        case 'split':
          setDialog({ kind: 'split', table });
          break;
        case 'session':
          setDialog({ kind: 'session', table });
          break;
        case 'bill-request':
        case 'bill-cancel':
          void requestBill(table);
          break;
        case 'charge':
          startCharge(table);
          break;
      }
    },
    [requestBill, setActiveTable, startCharge],
  );

  /** Añadir producto a la comanda EN EL SERVIDOR (a diferencia del carrito). */
  const pickItem = useCallback(
    async (item: PanelItem) => {
      if (activeTable?.order_id === undefined || activeTable?.order_id === null) return;
      try {
        await apiFetch(`/api/v1/sales/orders/${activeTable.order_id}/lines`, {
          method: 'POST',
          body: JSON.stringify({ product_id: item.product.id, quantity: '1.000' }),
        }, { idempotencyKey: crypto.randomUUID() });
        toast.success(`${item.product.name} añadido`, { key: 'floor-line' });
        await reloadAll();
      } catch (err) {
        const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
        toast.error(`No se pudo añadir ${item.product.name}`, { key: 'floor-line', detail });
      }
    },
    [activeTable, reloadAll],
  );

  // ------------------------------------------------------------------
  // Derivados del mapa
  // ------------------------------------------------------------------
  const normalize = (value: string): string => value.trim().toLowerCase();
  const visibleZones = zones.filter(
    (zone) => zone.active && (zoneFilter === '' || zone.id === zoneFilter),
  );
  const zoneTables = (zoneId: string): FloorTable[] =>
    tables.filter(
      (table) =>
        table.active &&
        table.zone_id === zoneId &&
        (query.trim() === '' || normalize(table.name).includes(normalize(query))),
    );

  // Panel activo del modo mesa (misma derivación que la vista de venta).
  const panel = panels.find((candidate) => candidate.id === panelId) ?? null;
  const activeItems: PanelItem[] = panel
    ? subpanelId === null
      ? panel.items
      : (panel.subpanels.find((sub) => sub.id === subpanelId)?.items ?? [])
    : [];

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <>
      {activeTable !== null ? (
        /* ---------------- MODO MESA: panel + comanda del servidor ---------------- */
        <div className="flex min-h-0 flex-1">
          <PanelNav
            panels={panels}
            selectedPanelId={panelId}
            selectedSubpanelId={subpanelId}
            onSelectPanel={(nextId) => {
              setPanelId(nextId);
              setSubpanelId(null);
            }}
            onSelectSubpanel={setSubpanelId}
          />
          <main className="flex min-w-0 flex-1 flex-col">
            <PanelGrid items={activeItems} onPick={(item) => void pickItem(item)} />
          </main>
          <FloorTableTicket
            table={activeTable}
            order={activeOrder}
            nowMs={nowMs}
            onBack={() => void setActiveTable(null)}
            onEditSession={() =>
              activeTable !== null && setDialog({ kind: 'session', table: activeTable })
            }
            onCharge={() => startCharge(activeTable)}
          />
        </div>
      ) : (
        /* ---------------- MAPA: zonas, búsqueda, lienzo y rejilla ---------------- */
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-4 py-3">
            <button
              type="button"
              onClick={onBack}
              className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm font-semibold text-ink-muted transition hover:bg-surface-hover hover:text-ink"
            >
              <ArrowLeft className="size-4" aria-hidden /> Venta
            </button>
            <h1 className="flex items-center gap-2 text-xl font-extrabold text-ink">
              <Armchair className="size-5" aria-hidden /> Sala
            </h1>
            <span
              title={live ? 'Canal en vivo (WebSocket)' : 'Sin canal: sondeo de respaldo'}
              className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${
                live ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/20 text-amber-300'
              }`}
            >
              {live ? <RadioTower className="size-3.5" aria-hidden /> : <WifiOff className="size-3.5" aria-hidden />}
              {live ? 'En vivo' : 'Sondeo'}
            </span>

            <div className="ms-auto flex items-center gap-2">
              <label className="relative">
                <span className="sr-only">Buscar mesa por nombre</span>
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-muted"
                  aria-hidden
                />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Buscar mesa…"
                  className="h-10 w-44 rounded-xl bg-surface ps-9 pe-3 text-sm text-ink outline-none ring-accent-hover focus:ring-2"
                />
              </label>
              <button
                type="button"
                onClick={() => setDialog({ kind: 'settings' })}
                className="flex h-10 items-center gap-2 rounded-xl bg-surface-sunken px-3 text-sm font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98]"
              >
                <Settings2 className="size-4" aria-hidden /> Ajustes
              </button>
            </div>

            {zones.length > 0 && (
              <div className="flex w-full flex-wrap gap-2">
                <button
                  type="button"
                  aria-pressed={zoneFilter === ''}
                  onClick={() => setZoneFilter('')}
                  className={`h-10 rounded-xl px-4 text-sm font-semibold transition active:scale-[0.98] ${
                    zoneFilter === ''
                      ? 'bg-accent text-accent-ink'
                      : 'bg-surface-sunken text-ink hover:bg-surface-hover'
                  }`}
                >
                  Todas
                </button>
                {zones
                  .filter((zone) => zone.active)
                  .map((zone) => (
                    <button
                      key={zone.id}
                      type="button"
                      aria-pressed={zoneFilter === zone.id}
                      onClick={() => setZoneFilter(zone.id)}
                      className={`h-10 rounded-xl px-4 text-sm font-semibold transition active:scale-[0.98] ${
                        zoneFilter === zone.id
                          ? 'bg-accent text-accent-ink'
                          : 'bg-surface-sunken text-ink hover:bg-surface-hover'
                      }`}
                    >
                      {zone.name}
                    </button>
                  ))}
              </div>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {status === 'loading' && tables.length === 0 && (
              <p className="flex items-center justify-center gap-2 py-16 text-ink-muted">
                <Loader2 className="size-6 animate-spin" aria-hidden /> Cargando el plano…
              </p>
            )}
            {status === 'error' && (
              <div className="flex flex-col items-center gap-3 py-16 text-center">
                <CircleAlert className="size-10 text-red-400" aria-hidden />
                <p className="text-ink">No se pudo cargar la sala</p>
                <p className="text-sm text-ink-muted">{error}</p>
                <button
                  type="button"
                  onClick={() => void load(true)}
                  className="rounded-lg bg-amber-500 px-6 py-3 font-semibold text-slate-900 hover:bg-amber-400"
                >
                  Reintentar
                </button>
              </div>
            )}
            {status !== 'error' && visibleZones.length === 0 && (
              <p className="py-16 text-center text-ink-muted">
                Sin zonas todavía: crea la primera en «Ajustes».
              </p>
            )}

            {visibleZones.map((zone) => {
              const zoneTablesList = zoneTables(zone.id);
              const placed = zoneTablesList.filter((t) => t.pos_x !== null && t.pos_y !== null);
              const unplaced = zoneTablesList.filter((t) => t.pos_x === null || t.pos_y === null);
              if (zoneTablesList.length === 0) return null;
              return (
                <section key={zone.id} className="mb-6">
                  <h2 className="mb-2 text-sm font-bold uppercase tracking-wide text-ink-muted">
                    {zone.name}
                  </h2>

                  {placed.length > 0 && (
                    <div className="relative mb-3 h-[26rem] w-full rounded-2xl border border-slate-800 bg-slate-950/60">
                      {placed.map((table) => (
                        <TableCard
                          key={table.id}
                          table={table}
                          nowMs={nowMs}
                          className="absolute -translate-x-1/2 -translate-y-1/2"
                          style={{ left: `${table.pos_x}%`, top: `${table.pos_y}%` }}
                          onTap={() => tableTap(table)}
                          onMenu={() => setMenuTable(table)}
                        />
                      ))}
                    </div>
                  )}

                  {unplaced.length > 0 && (
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(9rem,1fr))] gap-3">
                      {unplaced.map((table) => (
                        <TableCard
                          key={table.id}
                          table={table}
                          nowMs={nowMs}
                          onTap={() => tableTap(table)}
                          onMenu={() => setMenuTable(table)}
                        />
                      ))}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </div>
      )}

      {/* ---------------- Diálogos ---------------- */}
      {menuTable !== null && (
        <FloorTableMenu
          tableName={menuTable.name}
          actions={actionsForTable(menuTable.status)}
          onAction={(action) => handleMenuAction(menuTable, action)}
          onClose={() => setMenuTable(null)}
        />
      )}
      {dialog.kind === 'open' && (
        <FloorOpenDialog
          table={dialog.table}
          onOpened={() => void reloadAll()}
          onClose={() => setDialog({ kind: 'none' })}
        />
      )}
      {dialog.kind === 'move' && (
        <FloorMoveDialog
          table={dialog.table}
          mode={dialog.mode}
          onDone={() => setDialog({ kind: 'none' })}
          onClose={() => setDialog({ kind: 'none' })}
        />
      )}
      {dialog.kind === 'split' && (
        <FloorSplitDialog
          table={dialog.table}
          onDone={() => setDialog({ kind: 'none' })}
          onClose={() => setDialog({ kind: 'none' })}
        />
      )}
      {dialog.kind === 'session' && (
        <FloorSessionDialog
          table={dialog.table}
          onSaved={() => setDialog({ kind: 'none' })}
          onClose={() => setDialog({ kind: 'none' })}
        />
      )}
      {dialog.kind === 'settings' && (
        <FloorSettingsDialog onClose={() => setDialog({ kind: 'none' })} />
      )}
      {checkoutTable !== null && (
        <CheckoutDialog
          table={checkoutTable}
          onCompleted={() => {
            void reloadAll();
            void setActiveTable(null);
          }}
          onClose={() => setCheckoutTable(null)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Tarjeta de mesa (§5.4): nombre 20 px, chip con texto + tiempo tabular,
// comensales/plazas e importe abierto. Nunca solo color (§3.2).
// ---------------------------------------------------------------------------
interface TableCardProps {
  table: FloorTable;
  nowMs: number;
  className?: string;
  style?: CSSProperties;
  onTap: () => void;
  onMenu: () => void;
}

function TableCard({ table, nowMs, className = '', style, onTap, onMenu }: TableCardProps) {
  const total = openTotalCents(table);
  const mark = table.bill_requested_at ?? table.opened_at;
  const border =
    table.status === 'free'
      ? 'border-emerald-500/30'
      : table.status === 'open'
        ? 'border-amber-500/40'
        : 'border-sky-500/40';
  return (
    <div
      role="button"
      tabIndex={0}
      style={style}
      onClick={onTap}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onTap();
        }
      }}
      className={`flex h-28 w-36 select-none flex-col justify-between rounded-xl border bg-surface-raised p-2.5 text-left shadow-sm transition hover:bg-surface-hover active:scale-[0.98] ${border} ${className}`}
    >
      <div className="flex items-start justify-between gap-1">
        <span className="truncate text-xl font-extrabold leading-tight text-ink">{table.name}</span>
        <button
          type="button"
          aria-label={`Acciones de la mesa ${table.name}`}
          onClick={(event) => {
            event.stopPropagation();
            onMenu();
          }}
          className="-me-1 -mt-1 grid size-8 place-items-center rounded-lg text-ink-muted transition hover:bg-surface-sunken hover:text-ink"
        >
          <MoreVertical className="size-4" aria-hidden />
        </button>
      </div>

      <span
        className={`flex items-center gap-1.5 self-start rounded-full px-2 py-0.5 text-[11px] font-bold ${statusChipClass(table.status)}`}
      >
        {TABLE_STATUS_LABEL[table.status]}
        {mark !== null && <span className="tabular-nums">· {formatElapsed(mark, nowMs)}</span>}
      </span>

      <div className="flex items-center justify-between text-xs text-ink-muted">
        <span className="flex items-center gap-1">
          <UserRound className="size-3.5" aria-hidden />
          <span className="tabular-nums">
            {table.guest_count !== null ? `${table.guest_count}/${table.seats}` : `${table.seats} plazas`}
          </span>
        </span>
        {total !== null && (
          <span className="font-bold tabular-nums text-ink">{formatMoney(total)} €</span>
        )}
      </div>
    </div>
  );
}

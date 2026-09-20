/**
 * Estado del plano de mesas (§5.4). La comanda de mesa es un BORRADOR en el
 * servidor: aquí solo vivimos de sus lecturas (mesas + pedido activo); toda
 * mutación la hace un componente contra la API y luego recarga con
 * ``reloadAll()``. El hub WS (fase 12) avisa de cambios ajenos vía
 * ``invalidate()`` con sondeo de respaldo cuando el canal está caído.
 */

import { create } from 'zustand';
import { apiFetch, ApiError } from '../lib/api';
import {
  floorOrderDetailSchema,
  floorTableListSchema,
  floorZoneListSchema,
  type FloorOrderDetail,
  type FloorTable,
  type FloorZone,
} from '../lib/floor';

export type FloorStatus = 'idle' | 'loading' | 'ready' | 'error';

interface FloorState {
  zones: FloorZone[];
  tables: FloorTable[];
  status: FloorStatus;
  error: string | null;
  /** Canal WS del hub: en vivo ⇒ sin sondeo de respaldo. */
  live: boolean;
  activeTableId: string | null;
  activeOrder: FloorOrderDetail | null;
  load: (force?: boolean) => Promise<void>;
  /** Aviso del hub o del sondeo: refresca (con anti-rafaga de 2 s). */
  invalidate: () => void;
  setLive: (live: boolean) => void;
  setActiveTable: (tableId: string | null) => Promise<void>;
  refreshActiveOrder: () => Promise<void>;
  /** Recarga del plano y, si hay mesa activa, también su comanda. */
  reloadAll: () => Promise<void>;
}

const INVALIDATE_GUARD_MS = 2_000;
let lastLoadAt = 0;
let pendingInvalidate: ReturnType<typeof setTimeout> | null = null;

export const useFloor = create<FloorState>((set, get) => ({
  zones: [],
  tables: [],
  status: 'idle',
  error: null,
  live: false,
  activeTableId: null,
  activeOrder: null,

  load: async (force = false) => {
    if (get().status === 'loading' && !force) return;
    set({ status: 'loading' });
    try {
      const [zones, tables] = await Promise.all([
        apiFetch<{ items: FloorZone[] }>('/api/v1/restaurant/zones'),
        apiFetch<{ items: FloorTable[] }>('/api/v1/restaurant/tables'),
      ]);
      lastLoadAt = Date.now();
      set({
        zones: floorZoneListSchema.parse(zones).items,
        tables: floorTableListSchema.parse(tables).items,
        status: 'ready',
        error: null,
      });
    } catch (err) {
      set({
        status: 'error',
        error:
          err instanceof ApiError
            ? `Error ${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : 'Error desconocido',
      });
    }
  },

  invalidate: () => {
    if (get().status === 'idle') return; // nunca cargado: no hay nada que refrescar
    const remaining = INVALIDATE_GUARD_MS - (Date.now() - lastLoadAt);
    if (remaining <= 0) {
      void get().load(true);
      return;
    }
    // Antirráfaga: una ráfaga de eventos ⇒ una sola recarga al final.
    if (pendingInvalidate) return;
    pendingInvalidate = setTimeout(() => {
      pendingInvalidate = null;
      void get().load(true);
    }, remaining);
  },

  setLive: (live) => {
    if (get().live !== live) set({ live });
  },

  setActiveTable: async (tableId) => {
    set({ activeTableId: tableId, activeOrder: null });
    if (tableId === null) return;
    await get().refreshActiveOrder();
  },

  refreshActiveOrder: async () => {
    const { activeTableId, tables } = get();
    if (activeTableId === null) return;
    const table = tables.find((t) => t.id === activeTableId);
    if (!table?.order_id) {
      set({ activeOrder: null });
      return;
    }
    try {
      const order = await apiFetch<unknown>(`/api/v1/sales/orders/${table.order_id}`);
      set({ activeOrder: floorOrderDetailSchema.parse(order) });
    } catch (err) {
      set({
        activeOrder: null,
        error: err instanceof ApiError ? `Error ${err.status}: ${err.message}` : 'Error al cargar la comanda',
      });
    }
  },

  reloadAll: async () => {
    await get().load(true);
    await get().refreshActiveOrder();
  },
}));

/** Mesa activa (atajo para los selectores de componentes). */
export function activeTable(state: FloorState): FloorTable | null {
  return state.tables.find((t) => t.id === state.activeTableId) ?? null;
}

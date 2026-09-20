/**
 * Bandeja de salida offline (fase 14 · Offline): las dos operaciones que el
 * camarero SÍ puede hacer sin servidor — abrir pedido y añadir línea — se
 * ENCOLAN aquí (append-only, en localStorage) con un UUID de cliente y se
 * reenvían EN ORDER cuando vuelve la conexión. Sin resolución de conflictos
 * (no hay nada que conflicte: la línea offline nace y muere en el móvil):
 *  - cada POST viaja con su ``Idempotency-Key`` (§4.1) → un reintento que ya
 *    había llegado no duplica pedido ni línea: el servidor responde el replay.
 *  - ``add-line`` solo se envía cuando su pedido ya tiene ID de servidor.
 *  - 4xx → reglas de negocio rechazadas: se DESCARTA con aviso (reintentarla
 *    volvería a fallar; p. ej. producto eliminado mientras estaba offline).
 *  - fallo de red → se PARA y todo queda en cola para el próximo intento.
 * El cobro y la anulación NO encolan: dinero real, exigen servidor.
 */

import { create } from 'zustand';
import { ApiError, apiFetch } from '../lib/api';

export type OutboxKind = 'create-order' | 'add-line';

export interface OutboxEntry {
  id: string;
  kind: OutboxKind;
  /** UUID generado en el móvil: une las líneas con su pedido antes de que exista en el servidor. */
  localOrderId: string;
  terminalId: string | null;
  /** Solo add-line: para el POST y para el ticket estimado mientras tanto. */
  productId: string | null;
  productName: string;
  /** PVP final con IVA («1.50», el mismo string del catálogo). */
  unitPrice: string;
  /** Idempotency-Key del POST que le toca (fase 14 · §4.1). */
  key: string;
  createdAt: string;
}

export interface DraftLine {
  name: string;
  unitPrice: string;
  total: string;
}

const ENTRIES_KEY = 'tpv-mobile-outbox';
const MAP_KEY = 'tpv-mobile-outbox-map';

function loadEntries(): OutboxEntry[] {
  try {
    const raw = localStorage.getItem(ENTRIES_KEY);
    const parsed: unknown = raw === null ? [] : JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is OutboxEntry =>
        typeof item === 'object' &&
        item !== null &&
        ((item as OutboxEntry).kind === 'create-order' ||
          (item as OutboxEntry).kind === 'add-line') &&
        typeof (item as OutboxEntry).localOrderId === 'string' &&
        typeof (item as OutboxEntry).key === 'string',
    );
  } catch {
    return [];
  }
}

function loadServerIds(): Record<string, string> {
  try {
    const raw = localStorage.getItem(MAP_KEY);
    const parsed: unknown = raw === null ? {} : JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return {};
    return Object.fromEntries(
      Object.entries(parsed).filter(
        ([local, server]) => typeof local === 'string' && typeof server === 'string',
      ),
    );
  } catch {
    return {};
  }
}

interface OutboxState {
  entries: OutboxEntry[];
  /** localOrderId → id real ya creado en el servidor (persistido). */
  serverIds: Record<string, string>;
  /** Avisos de operaciones descartadas (4xx): se muestran, no se esconden. */
  notices: string[];
  enqueue: (entry: Omit<OutboxEntry, 'id' | 'createdAt'>) => void;
  /** Reenvía en orden; 'drained' = cola vacía, 'stalled' = quedó pendiente. */
  flush: () => Promise<'drained' | 'stalled'>;
  dismissNotice: (index: number) => void;
  pendingFor: (localOrderId: string) => OutboxEntry[];
  /** Ticket estimado (cantidades siempre 1): presentación, no negocio. */
  linesFor: (localOrderId: string) => DraftLine[];
  estimatedTotal: (localOrderId: string) => string | null;
}

export const useOutbox = create<OutboxState>((set, get) => ({
  entries: loadEntries(),
  serverIds: loadServerIds(),
  notices: [],

  enqueue(data) {
    const entry: OutboxEntry = {
      ...data,
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
    };
    const entries = [...get().entries, entry];
    set({ entries });
    try {
      localStorage.setItem(ENTRIES_KEY, JSON.stringify(entries));
    } catch {
      /* sin cuota: la cola vive en memoria hasta cerrar la pestaña */
    }
  },

  async flush() {
    const ids: Record<string, string> = { ...get().serverIds };
    const notices = [...get().notices];
    const queue = [...get().entries];
    const kept: OutboxEntry[] = [];

    while (queue.length > 0) {
      const entry = queue[0]!;
      const serverOrderId = ids[entry.localOrderId];
      if (entry.kind === 'add-line' && serverOrderId === undefined) {
        kept.push(...queue); // su pedido aún no existe: el resto, en orden, detrás
        break;
      }
      queue.shift();
      try {
        if (entry.kind === 'create-order') {
          const created = await apiFetch<{ id: string }>('/api/v1/sales/orders', {
            method: 'POST',
            headers: { 'Idempotency-Key': entry.key },
            body: JSON.stringify({ terminal_id: entry.terminalId }),
          });
          ids[entry.localOrderId] = created.id;
        } else {
          await apiFetch(`/api/v1/sales/orders/${serverOrderId}/lines`, {
            method: 'POST',
            headers: { 'Idempotency-Key': entry.key },
            body: JSON.stringify({ product_id: entry.productId, quantity: '1' }),
          });
        }
      } catch (cause) {
        if (cause instanceof ApiError) {
          // Regla de negocio: reintentar no cambiará el veredicto → descartada,
          // y con ella las líneas de ese mismo pedido (el ticket ha muerto).
          notices.push(`Descartada «${labelOf(entry)}»: ${cause.message}`);
          for (let i = queue.length - 1; i >= 0; i -= 1) {
            const follower = queue[i];
            if (follower !== undefined && follower.localOrderId === entry.localOrderId) {
              queue.splice(i, 1);
              notices.push(`Descartada «${labelOf(follower)}»: su pedido fue rechazado.`);
            }
          }
        } else {
          kept.push(entry, ...queue); // red caída: para y conserva TODO en orden
        }
        break;
      }
    }

    set({ entries: kept, serverIds: ids, notices });
    try {
      localStorage.setItem(ENTRIES_KEY, JSON.stringify(kept));
      localStorage.setItem(MAP_KEY, JSON.stringify(ids));
    } catch {
      /* sin cuota: el estado en memoria manda */
    }
    return kept.length === 0 ? 'drained' : 'stalled';
  },

  dismissNotice(index) {
    set({ notices: get().notices.filter((_, i) => i !== index) });
  },

  pendingFor(localOrderId) {
    return get().entries.filter((entry) => entry.localOrderId === localOrderId);
  },

  linesFor(localOrderId) {
    return get()
      .entries.filter((entry) => entry.kind === 'add-line' && entry.localOrderId === localOrderId)
      .map((entry) => ({ name: entry.productName, unitPrice: entry.unitPrice, total: entry.unitPrice }));
  },

  estimatedTotal(localOrderId) {
    const cents = get()
      .entries.filter((entry) => entry.kind === 'add-line' && entry.localOrderId === localOrderId)
      .reduce((sum, entry) => {
        const [whole, frac = ''] = entry.unitPrice.split('.');
        return sum + Number(whole) * 100 + Number(frac.padEnd(2, '0').slice(0, 2));
      }, 0);
    if (cents === 0) return null;
    return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`;
  },
}));

function labelOf(entry: OutboxEntry): string {
  if (entry.kind === 'create-order') return 'apertura del pedido';
  return entry.productName === '' ? 'línea del pedido' : `«${entry.productName}»`;
}

/**
 * Sesión de caja del terminal (QA E01/E13: una sola abierta por terminal, el
 * índice parcial único la garantiza en el servidor). El cobro exige
 * ``cash_session_id``, así que el TPV necesita saber SIEMPRE si hay caja
 * abierta: GET /cash/sessions/current la consulta y POST /cash/sessions la
 * abre (permiso ``cash.open``, el mismo del ciclo diario del móvil).
 */

import { create } from 'zustand';
import { ApiError, apiFetch } from '../lib/api';
import { cashSessionSchema, type CashSessionApi } from '../lib/schemas';

type CashStatus = 'idle' | 'loading' | 'ready' | 'error';

interface CashState {
  session: CashSessionApi | null;
  status: CashStatus;
  error: string | null;
  /** Consulta la sesión abierta del terminal; 404 ⇒ sin caja (estado limpio). */
  load: (terminalId: string) => Promise<void>;
  /** Abre caja con el fondo inicial contado; devuelve la sesión o lanza. */
  open: (terminalId: string, openingAmount: string) => Promise<CashSessionApi>;
}

export const useCash = create<CashState>((set) => ({
  session: null,
  status: 'idle',
  error: null,

  async load(terminalId) {
    set({ status: 'loading', error: null });
    try {
      const session = await apiFetch<CashSessionApi>(
        `/api/v1/cash/sessions/current?terminal_id=${encodeURIComponent(terminalId)}`,
      );
      set({ session: cashSessionSchema.parse(session), status: 'ready' });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Sin sesión abierta: no es un fallo, es el estado «caja cerrada».
        set({ session: null, status: 'ready' });
        return;
      }
      if (err instanceof ApiError && err.status === 401) return; // apiFetch ya cerró sesión
      set({ status: 'error', error: err instanceof Error ? err.message : String(err) });
    }
  },

  async open(terminalId, openingAmount) {
    const session = await apiFetch<CashSessionApi>('/api/v1/cash/sessions', {
      method: 'POST',
      body: JSON.stringify({ terminal_id: terminalId, opening_amount: openingAmount }),
    });
    const parsed = cashSessionSchema.parse(session);
    set({ session: parsed, status: 'ready' });
    return parsed;
  },
}));

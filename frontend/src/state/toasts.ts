/**
 * Avisos del mostrador con el Design System (§4: toast NO modal arriba-centro;
 * éxito 4 s, error persistente hasta cerrarlo, máximo 3, reemplazo por clave).
 * Sustituye al «notice» de la TopBar: el error ya no se esfera solo.
 */

import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'info';

export interface ToastItem {
  id: number;
  key?: string;
  kind: ToastKind;
  message: string;
  detail?: string;
}

interface ToastOptions {
  /** Reemplaza el aviso anterior con la misma clave (lecturas de lector seguidas…). */
  key?: string;
  detail?: string;
}

interface ToastsState {
  items: ToastItem[];
  push: (kind: ToastKind, message: string, options?: ToastOptions) => void;
  dismiss: (id: number) => void;
}

const MAX_TOASTS = 3;
const SUCCESS_MS = 4_000;

/** Timers fuera del estado: no son datos pintables. */
const timers = new Map<number, number>();
let nextId = 1;

function clearTimer(id: number): void {
  const timer = timers.get(id);
  if (timer !== undefined) {
    window.clearTimeout(timer);
    timers.delete(id);
  }
}

export const useToasts = create<ToastsState>((set) => ({
  items: [],

  push(kind, message, options = {}) {
    const id = nextId++;
    set((state) => {
      // Reemplazo por clave + tope de 3: aparto hueco para el nuevo y cae el más viejo.
      const filtered = state.items.filter((item) =>
        options.key ? item.key !== options.key : true,
      );
      const kept = filtered.slice(Math.max(0, filtered.length - (MAX_TOASTS - 1)));
      return { items: [...kept, { id, key: options.key, kind, message, detail: options.detail }] };
    });

    if (kind !== 'error') {
      // El éxito caduca solo; el error persiste hasta que el operador lo cierre.
      const timer = window.setTimeout(() => {
        timers.delete(id);
        set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
      }, SUCCESS_MS);
      timers.set(id, timer);
    }
  },

  dismiss(id) {
    clearTimer(id);
    set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
  },
}));

/** Atajos legibles en los componentes (y en los tests). */
export const toast = {
  success: (message: string, options?: ToastOptions) =>
    useToasts.getState().push('success', message, options),
  error: (message: string, options?: ToastOptions) =>
    useToasts.getState().push('error', message, options),
  info: (message: string, options?: ToastOptions) =>
    useToasts.getState().push('info', message, options),
};

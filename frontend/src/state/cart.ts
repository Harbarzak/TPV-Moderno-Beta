/**
 * Ticket actual (ARCHITECTURE.md §4.1): se edita EN EL CLIENTE y persiste en
 * localStorage para sobrevivir a reinicios y cuelgues. Solo el cobro escribe
 * en la base de datos (fase de venta): aquí no hay ninguna llamada al servidor.
 *
 * Los botones añaden 1 unidad; los pesables se pesan (cada peso es una línea
 * propia) y los unitarios se acumulan en una sola línea por producto.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { UNIT_MILLI } from '../lib/money';

export interface CartProduct {
  id: string;
  name: string;
  price: string;
  taxCode: string;
  taxRate: string;
  weighable: boolean;
}

export interface CartLine extends CartProduct {
  lineId: string;
  qtyMilli: number;
  /** Descuento de línea en formato de contrato («10.00» = 10 %); «0.00» = sin. */
  discountPct: string;
}

interface CartState {
  lines: CartLine[];
  /** Añade producto al ticket; qtyMilli por defecto 1 unidad. */
  addProduct: (product: CartProduct, qtyMilli?: number) => void;
  /** Suma (o resta) cantidad a una línea; al llegar a cero se quita. */
  addQty: (lineId: string, deltaMilli: number) => void;
  /** Fija (o quita, con «0.00») el descuento de la línea. */
  setDiscount: (lineId: string, discountPct: string) => void;
  removeLine: (lineId: string) => void;
  clear: () => void;
}

function newLineId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `line-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const useCart = create<CartState>()(
  persist(
    (set) => ({
      lines: [],

      addProduct: (product, qtyMilli = UNIT_MILLI) =>
        set((state) => {
          if (!product.weighable) {
            const existing = state.lines.find(
              (line) => line.id === product.id && !line.weighable,
            );
            if (existing) {
              return {
                lines: state.lines.map((line) =>
                  line.lineId === existing.lineId
                    ? { ...line, qtyMilli: line.qtyMilli + qtyMilli }
                    : line,
                ),
              };
            }
          }
          return {
            lines: [
              ...state.lines,
              { ...product, lineId: newLineId(), qtyMilli, discountPct: '0.00' },
            ],
          };
        }),

      addQty: (lineId, deltaMilli) =>
        set((state) => ({
          lines: state.lines.flatMap((line) => {
            if (line.lineId !== lineId) return [line];
            const qtyMilli = line.qtyMilli + deltaMilli;
            return qtyMilli > 0 ? [{ ...line, qtyMilli }] : [];
          }),
        })),

      removeLine: (lineId) =>
        set((state) => ({ lines: state.lines.filter((line) => line.lineId !== lineId) })),

      setDiscount: (lineId, discountPct) =>
        set((state) => ({
          lines: state.lines.map((line) =>
            line.lineId === lineId ? { ...line, discountPct } : line,
          ),
        })),

      clear: () => set({ lines: [] }),
    }),
    {
      name: 'tpv-ticket-draft',
      version: 2,
      storage: createJSONStorage(() => localStorage),
      // v1 → v2: el descuento por línea nace en «0.00» (sin descuento).
      migrate: (persisted, version) => {
        if (version < 2) {
          // zustand entrega el estado INTERNO ({lines}), no el envoltorio {state, version}.
          const draft = persisted as { lines?: Record<string, unknown>[] };
          for (const line of draft.lines ?? []) {
            if (typeof line.discountPct !== 'string') line.discountPct = '0.00';
          }
        }
        return persisted as CartState;
      },
    },
  ),
);

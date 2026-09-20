/**
 * Ticket en curso del camarero: solo el ID. Al volver a la pestaña Venta se
 * relee del servidor (fuente de verdad) — el cliente nunca reconstruye el
 * estado de un pedido por su cuenta. Con el pedido abierto OFFLINE (fase 14)
 * no hay ID de servidor: ``localOrderId`` guarda el UUID de cliente que une
 * la pantalla con las entradas pendientes del outbox.
 */

import { create } from 'zustand';

interface SaleState {
  orderId: string | null;
  localOrderId: string | null;
  setOrderId: (id: string | null) => void;
  setLocalOrderId: (id: string | null) => void;
}

export const useSale = create<SaleState>((set) => ({
  orderId: null,
  localOrderId: null,
  setOrderId: (id) => set({ orderId: id }),
  setLocalOrderId: (id) => set({ localOrderId: id }),
}));

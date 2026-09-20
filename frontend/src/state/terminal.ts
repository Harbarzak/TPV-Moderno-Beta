/**
 * Terminal de este puesto (patrón del móvil, fase 16): el UUID se configura
 * localmente y el backend lo valida en cada uso. GET /admin/terminals exige
 * permiso de administración, así que el diálogo de terminal intenta listarlas
 * y, sin permiso, acepta el UUID a mano. Es configuración de cliente.
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface TerminalConfig {
  id: string;
  label: string;
}

interface TerminalState {
  terminal: TerminalConfig | null;
  set: (terminal: TerminalConfig) => void;
  clear: () => void;
}

export const useTerminal = create<TerminalState>()(
  persist(
    (set) => ({
      terminal: null,
      set: (terminal) => set({ terminal }),
      clear: () => set({ terminal: null }),
    }),
    {
      name: 'tpv-pos-terminal',
      version: 1,
      storage: createJSONStorage(() => localStorage),
    },
  ),
);

export function isValidUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

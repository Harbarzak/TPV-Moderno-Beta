/**
 * Sesión del KDS: usuario/contraseña contra la MISMA API que el resto de
 * clientes TPV (el cocinero no maneja cajas ni turnos: solo hace falta
 * entrar). El token vive en localStorage y la identidad se restaura al
 * arrancar con GET /auth/me; entrar sin ``kds.operate`` servirá para ver el
 * 403 del tablero, que es lo que manda el servidor.
 */

import { z } from 'zod';
import { create } from 'zustand';
import { apiFetch, clearToken, getToken, setToken } from '../lib/api';
import { meSchema, type Me } from '../lib/schemas';

const tokenSchema = z.object({ access_token: z.string() });

interface SessionState {
  me: Me | null;
  /** true tras comprobar el token guardado (o al terminar sin él). */
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  restore: () => Promise<void>;
  logout: () => Promise<void>;
}

async function establish(token: string): Promise<Me> {
  setToken(token);
  const raw = await apiFetch<unknown>('/api/v1/auth/me');
  return meSchema.parse(raw);
}

export const useSession = create<SessionState>((set) => ({
  me: null,
  ready: false,

  login: async (username, password) => {
    const raw = await apiFetch<unknown>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
    const { access_token: token } = tokenSchema.parse(raw);
    set({ me: await establish(token) });
  },

  restore: async () => {
    if (getToken() === null) {
      set({ ready: true });
      return;
    }
    try {
      const raw = await apiFetch<unknown>('/api/v1/auth/me');
      set({ me: meSchema.parse(raw), ready: true });
    } catch {
      // Token caducado o servidor ausente: sin sesión local fingida.
      clearToken();
      set({ me: null, ready: true });
    }
  },

  logout: async () => {
    try {
      await apiFetch('/api/v1/auth/logout', { method: 'POST' });
    } catch {
      // El cierre local manda; si el servidor ya no conoce el token, da igual.
    }
    clearToken();
    set({ me: null });
  },
}));

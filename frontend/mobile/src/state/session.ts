/**
 * Sesión del móvil: acceso por PIN (suelo de venta) o usuario/contraseña,
 * ambos contra la MISMA API que el TPV de mostrador. El token vive en
 * localStorage y la identidad se restaura al arrancar con GET /auth/me —
 * los permisos que manda son siempre los que el servidor acaba de emitir.
 */

import { z } from 'zod';
import { create } from 'zustand';
import { apiFetch, clearToken, getToken, setToken } from '../lib/api';
import { meSchema, type Me } from '../lib/schemas';

const tokenSchema = z.object({ access_token: z.string() });

export type LoginMode = 'pin' | 'password';

interface SessionState {
  me: Me | null;
  /** true tras comprobar el token guardado (o al terminar sin él). */
  ready: boolean;
  loginWithPin: (username: string, pin: string) => Promise<void>;
  loginWithPassword: (username: string, password: string) => Promise<void>;
  restore: () => Promise<void>;
  logout: () => Promise<void>;
}

async function establish(token: string): Promise<Me> {
  setToken(token);
  const raw = await apiFetch<unknown>('/api/v1/auth/me');
  return meSchema.parse(raw);
}

async function login(mode: LoginMode, username: string, secret: string): Promise<Me> {
  const path = mode === 'pin' ? '/api/v1/auth/pin' : '/api/v1/auth/login';
  const body =
    mode === 'pin' ? { username, pin: secret } : { username, password: secret };
  const raw = await apiFetch<unknown>(path, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  const { access_token: token } = tokenSchema.parse(raw);
  return establish(token);
}

export const useSession = create<SessionState>((set) => ({
  me: null,
  ready: false,

  loginWithPin: async (username, pin) => {
    set({ me: await login('pin', username, pin) });
  },

  loginWithPassword: async (username, password) => {
    set({ me: await login('password', username, password) });
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

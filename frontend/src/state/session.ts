/**
 * Sesión del terminal (mínima para la fase): usuario+contraseña contra
 * /auth/login, token en localStorage. El ciclo completo de sesión (refresh
 * rotativo, PIN, cierre por inactividad) pertenece a las fases de terminales.
 */

import { create } from 'zustand';
import { ApiError, apiFetch, clearToken, setToken } from '../lib/api';

interface SessionState {
  user: string | null;
  checking: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
}

interface LoginResponse {
  access_token: string;
}

export const useSession = create<SessionState>((set) => ({
  user: null,
  checking: false,
  error: null,

  login: async (username, password) => {
    set({ checking: true, error: null });
    try {
      const data = await apiFetch<LoginResponse>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      setToken(data.access_token);
      set({ user: username, checking: false });
      return true;
    } catch (err) {
      const message =
        err instanceof ApiError && (err.status === 401 || err.status === 429)
          ? 'Usuario o contraseña incorrectos'
          : 'No se pudo conectar con el servidor';
      set({ checking: false, error: message });
      return false;
    }
  },

  logout: () => {
    clearToken();
    set({ user: null, checking: false, error: null });
  },
}));

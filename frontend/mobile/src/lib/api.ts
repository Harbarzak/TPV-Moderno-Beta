/**
 * Cliente HTTP de la PWA: MISMA API que el TPV de mostrador (§2), rutas
 * relativas (proxy de Vite en desarrollo, mismo servidor en producción) y
 * Bearer del token. Un 401 limpia la sesión local para volver al acceso;
 * las reglas de negocio (totales, permisos, estados) SIEMPRE las decide el
 * backend — aquí solo viajan y se muestran.
 */

const TOKEN_KEY = 'tpv-mobile-token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json');

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    if (response.status === 401) clearToken();
    const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(response.status, problem?.detail ?? `HTTP ${response.status}`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getToken } from '../lib/api';
import { useSession } from './session';

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  localStorage.clear();
  useSession.setState({ user: null, checking: false, error: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('sesión mínima del terminal', () => {
  it('login correcto guarda el token y el usuario', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { access_token: 'jwt-de-prueba', token_type: 'bearer' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const ok = await useSession.getState().login('jefe', 'Clave-Segura-2026');

    expect(ok).toBe(true);
    expect(getToken()).toBe('jwt-de-prueba');
    expect(useSession.getState().user).toBe('jefe');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/auth/login',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('login incorrecto muestra error genérico y no guarda token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'x' })));

    const ok = await useSession.getState().login('jefe', 'mala');

    expect(ok).toBe(false);
    expect(getToken()).toBeNull();
    expect(useSession.getState().user).toBeNull();
    expect(useSession.getState().error).toBe('Usuario o contraseña incorrectos');
  });

  it('un 401 en el cliente API limpia el token (sesión caducada)', async () => {
    const { apiFetch } = await import('../lib/api');
    localStorage.setItem('tpv-access-token', 'viejo');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'x' })));

    await expect(apiFetch('/api/v1/catalog/panels')).rejects.toThrow();
    expect(getToken()).toBeNull();
  });

  it('logout borra token y usuario', async () => {
    localStorage.setItem('tpv-access-token', 'algo');
    useSession.setState({ user: 'jefe' });

    useSession.getState().logout();

    expect(getToken()).toBeNull();
    expect(useSession.getState().user).toBeNull();
  });
});

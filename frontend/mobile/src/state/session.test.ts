import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSession } from './session';

const ME = {
  id: '0b6e4a5e-8a3e-4d0a-9a5e-1c2b3a4d5e6f',
  username: 'camarero',
  full_name: 'Ana Camarera',
  role: 'waiter',
  permissions: ['sales.sell'],
  scope: 'all',
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  localStorage.clear();
  useSession.setState({ me: null, ready: false });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('sesión contra la API real', () => {
  it('login por PIN guarda token y perfil validado', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'tok-123' }))
      .mockResolvedValueOnce(jsonResponse(200, ME));
    vi.stubGlobal('fetch', fetchMock);

    await useSession.getState().loginWithPin('camarero', '1234');

    expect(localStorage.getItem('tpv-mobile-token')).toBe('tok-123');
    expect(useSession.getState().me?.full_name).toBe('Ana Camarera');
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/pin');
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      username: 'camarero',
      pin: '1234',
    });
  });

  it('login por contraseña usa /auth/login', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(200, { access_token: 'tok-456' }))
      .mockResolvedValueOnce(jsonResponse(200, ME));
    vi.stubGlobal('fetch', fetchMock);

    await useSession.getState().loginWithPassword('camarero', 'secreto');

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/login');
    expect(useSession.getState().me?.username).toBe('camarero');
  });

  it('restore sin token no toca la red y deja ready', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await useSession.getState().restore();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useSession.getState().ready).toBe(true);
    expect(useSession.getState().me).toBeNull();
  });

  it('restore con token válido recupera la identidad', async () => {
    localStorage.setItem('tpv-mobile-token', 'tok-guardado');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(jsonResponse(200, ME)),
    );

    await useSession.getState().restore();

    expect(useSession.getState().me?.id).toBe(ME.id);
  });

  it('restore con token caducado limpia la sesión local', async () => {
    localStorage.setItem('tpv-mobile-token', 'tok-viejo');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' })));

    await useSession.getState().restore();

    expect(localStorage.getItem('tpv-mobile-token')).toBeNull();
    expect(useSession.getState().me).toBeNull();
  });

  it('logout borra el token aunque el servidor falle', async () => {
    localStorage.setItem('tpv-mobile-token', 'tok-activo');
    useSession.setState({ me: { ...ME }, ready: true });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('offline')));

    await useSession.getState().logout();

    expect(localStorage.getItem('tpv-mobile-token')).toBeNull();
    expect(useSession.getState().me).toBeNull();
  });
});

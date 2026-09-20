import { afterEach, describe, expect, it, vi } from 'vitest';
import { checkReachable, useConnectivity } from './connectivity';

function ok(): Response {
  return new Response(null, { status: 200 });
}

afterEach(() => {
  vi.unstubAllGlobals();
  useConnectivity.setState({ online: true, checking: false });
});

describe('checkReachable — «¿llego al servidor?»', () => {
  it('healthz 200 → alcanzable', async () => {
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => ok());
    await expect(checkReachable(fetchMock, true)).resolves.toBe(true);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/healthz');
  });

  it('5xx TAMBIÉN es alcanzable: el servidor escucha aunque esté mal', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 503 }));
    await expect(checkReachable(fetchMock, true)).resolves.toBe(true);
  });

  it('TypeError (la red no dejó pasar la petición) → fuera de línea', async () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('network down');
    });
    await expect(checkReachable(fetchMock, true)).resolves.toBe(false);
  });

  it('navegador offline → ni lo intenta', async () => {
    const fetchMock = vi.fn(async () => ok());
    await expect(checkReachable(fetchMock, false)).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('init — eventos del navegador y re-sondeo', () => {
  it('el evento offline baja el indicador sin sondear', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const cleanup = useConnectivity.getState().init();
    try {
      fetchMock.mockClear(); // init ya lanzó su sonda inicial; no cuenta aquí
      window.dispatchEvent(new Event('offline'));
      expect(useConnectivity.getState().online).toBe(false);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      cleanup();
    }
  });

  it('el evento online re-sondea y marca conexión', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ok()));
    const cleanup = useConnectivity.getState().init();
    try {
      window.dispatchEvent(new Event('offline'));
      expect(useConnectivity.getState().online).toBe(false);
      window.dispatchEvent(new Event('online'));
      await vi.waitFor(() => expect(useConnectivity.getState().online).toBe(true));
    } finally {
      cleanup();
    }
  });
});

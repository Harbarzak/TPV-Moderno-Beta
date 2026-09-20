import { afterEach, describe, expect, it, vi } from 'vitest';
import { cachedTables, probeTables } from './capabilities';

function mockFetchOnce(status: number | 'network-error'): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      if (status === 'network-error') throw new TypeError('network down');
      return new Response(null, { status });
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('probeTables — «mesas si están activas»', () => {
  it('200 → activa', async () => {
    mockFetchOnce(200);
    await expect(probeTables()).resolves.toBe('active');
  });

  it('404 (módulo aún no desplegado, fase 16) → inactiva', async () => {
    mockFetchOnce(404);
    await expect(probeTables()).resolves.toBe('inactive');
  });

  it('403 (sin permiso para este usuario) → inactiva', async () => {
    mockFetchOnce(403);
    await expect(probeTables()).resolves.toBe('inactive');
  });

  it('sin red → inactiva (conservador)', async () => {
    mockFetchOnce('network-error');
    await expect(probeTables()).resolves.toBe('inactive');
  });

  it('el resultado queda cacheado para el primer render', async () => {
    mockFetchOnce(200);
    await probeTables();
    expect(cachedTables()).toBe('active');
    mockFetchOnce(404);
    expect(cachedTables()).toBe('active'); // sin re-sondear
  });
});

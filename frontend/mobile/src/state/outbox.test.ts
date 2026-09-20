import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useOutbox, type OutboxEntry } from './outbox';

const TERMINAL = '11111111-1111-1111-1111-111111111111';
const PRODUCT = '22222222-2222-2222-2222-222222222222';
const SERVER_ORDER = '33333333-3333-3333-3333-333333333333';
const LOCAL = 'local-1';

function entry(overrides: Partial<OutboxEntry> = {}): Omit<OutboxEntry, 'id' | 'createdAt'> {
  return {
    kind: 'add-line',
    localOrderId: LOCAL,
    terminalId: TERMINAL,
    productId: PRODUCT,
    productName: 'Café solo',
    unitPrice: '1.50',
    key: 'k-line',
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  localStorage.clear();
  useOutbox.setState({ entries: [], serverIds: {}, notices: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('outbox — cola offline (fase 14)', () => {
  it('encolar persiste la cola en localStorage', () => {
    useOutbox.getState().enqueue(entry());

    const raw: unknown = JSON.parse(localStorage.getItem('tpv-mobile-outbox') ?? '[]');
    expect(Array.isArray(raw)).toBe(true);
    expect(raw).toHaveLength(1);
    const first = (raw as OutboxEntry[])[0];
    expect(first.kind).toBe('add-line');
    expect(first.id).toEqual(expect.any(String));
  });

  it('flush drena apertura+línea EN ORDEN, con Idempotency-Key y mapeo a servidor', async () => {
    useOutbox.getState().enqueue(
      entry({ kind: 'create-order', productId: null, productName: '', unitPrice: '', key: 'k-open' }),
    );
    useOutbox.getState().enqueue(entry({ key: 'k-line' }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(201, { id: SERVER_ORDER }))
      .mockResolvedValueOnce(jsonResponse(201, {}));
    vi.stubGlobal('fetch', fetchMock);

    await expect(useOutbox.getState().flush()).resolves.toBe('drained');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const init0 = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/sales/orders');
    // apiFetch normaliza las cabeceras a un Headers.
    expect(new Headers(init0.headers).get('Idempotency-Key')).toBe('k-open');
    expect(JSON.parse(String(init0.body))).toEqual({ terminal_id: TERMINAL });
    const init1 = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/v1/sales/orders/${SERVER_ORDER}/lines`);
    expect(new Headers(init1.headers).get('Idempotency-Key')).toBe('k-line');

    expect(useOutbox.getState().entries).toHaveLength(0);
    expect(useOutbox.getState().serverIds[LOCAL]).toBe(SERVER_ORDER);
    const persisted: Record<string, string> = JSON.parse(
      localStorage.getItem('tpv-mobile-outbox-map') ?? '{}',
    );
    expect(persisted[LOCAL]).toBe(SERVER_ORDER);
  });

  it('add-line sin su pedido NO sale de la cola (orden estricto)', async () => {
    useOutbox.getState().enqueue(entry());
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(useOutbox.getState().flush()).resolves.toBe('stalled');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useOutbox.getState().entries).toHaveLength(1);
    expect(useOutbox.getState().notices).toHaveLength(0);
  });

  it('4xx descarta la operación y con ella las líneas de ese ticket', async () => {
    useOutbox.getState().enqueue(
      entry({ kind: 'create-order', productId: null, productName: '', unitPrice: '', key: 'k-open' }),
    );
    useOutbox.getState().enqueue(entry({ key: 'k-line' }));
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce(jsonResponse(409, { detail: 'terminal desconocido' })),
    );

    await expect(useOutbox.getState().flush()).resolves.toBe('drained');

    expect(useOutbox.getState().entries).toHaveLength(0);
    const notices = useOutbox.getState().notices;
    expect(notices).toHaveLength(2);
    expect(notices[0]).toContain('Descartada');
    expect(notices[0]).toContain('terminal desconocido');
    expect(notices[1]).toContain('rechazado');
  });

  it('fallo de red PARA el drenaje y conserva la cola íntegra y en orden', async () => {
    useOutbox.getState().enqueue(
      entry({ kind: 'create-order', productId: null, productName: '', unitPrice: '', key: 'k-open' }),
    );
    useOutbox.getState().enqueue(entry({ key: 'k-line' }));
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('offline')));

    await expect(useOutbox.getState().flush()).resolves.toBe('stalled');

    const entries = useOutbox.getState().entries;
    expect(entries).toHaveLength(2);
    expect(entries[0]?.kind).toBe('create-order');
    expect(entries[1]?.kind).toBe('add-line');
    expect(useOutbox.getState().notices).toHaveLength(0);
  });

  it('ticket estimado: suma de PVP finales de las líneas (solo presentación)', () => {
    useOutbox.getState().enqueue(entry({ key: 'k1', unitPrice: '1.50' }));
    useOutbox.getState().enqueue(entry({ key: 'k2', unitPrice: '2.25' }));

    expect(useOutbox.getState().linesFor(LOCAL)).toEqual([
      { name: 'Café solo', unitPrice: '1.50', total: '1.50' },
      { name: 'Café solo', unitPrice: '2.25', total: '2.25' },
    ]);
    expect(useOutbox.getState().estimatedTotal(LOCAL)).toBe('3.75');
    expect(useOutbox.getState().estimatedTotal('otro-ticket')).toBeNull();
  });
});

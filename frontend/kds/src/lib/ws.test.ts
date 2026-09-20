import { describe, expect, it, vi } from 'vitest';
import { connectHub, handleHubFrame, type HubFrame } from './ws';

/** WebSocket de mentira: captura lo enviado y expone los callbacks. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  sent: HubFrame[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((raw: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data) as HubFrame);
  }

  close(): void {
    this.onclose?.();
  }

  /** El servidor entrega una trama al cliente. */
  serverSends(frame: HubFrame): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

describe('ws: reductor de tramas', () => {
  const makeCtx = () => {
    const sent: HubFrame[] = [];
    return {
      sent,
      live: vi.fn(),
      events: vi.fn(),
      ctx: {
        topics: ['sales', 'restaurant'],
        send: (frame: HubFrame) => sent.push(frame),
        onLive: () => undefined,
        onEvent: () => undefined,
      },
    };
  };

  it('tras «ready» pide la suscripción a sus temas', () => {
    const { ctx, sent, live, events } = makeCtx();
    ctx.onLive = live;
    ctx.onEvent = events;
    handleHubFrame({ type: 'ready' }, ctx);
    expect(sent).toEqual([{ type: 'subscribe', topics: ['sales', 'restaurant'] }]);
    expect(live).not.toHaveBeenCalled();
  });

  it('«subscribed» marca el canal en vivo', () => {
    const { ctx, live } = makeCtx();
    handleHubFrame({ type: 'subscribed', topics: [], denied: [] }, { ...ctx, onLive: live });
    expect(live).toHaveBeenCalledTimes(1);
  });

  it('responde pong a cada ping', () => {
    const { ctx, sent } = makeCtx();
    handleHubFrame({ type: 'ping' }, ctx);
    expect(sent).toEqual([{ type: 'pong' }]);
  });

  it('reenvía el evento de negocio al consumidor', () => {
    const { ctx, events } = makeCtx();
    const event = { id: 7, topic: 'restaurant', type: 'orders_merged' };
    handleHubFrame({ type: 'event', event }, { ...ctx, onEvent: events });
    expect(events).toHaveBeenCalledWith(event);
  });

  it('ignora tramas desconocidas sin romper', () => {
    const { ctx, sent, live, events } = makeCtx();
    handleHubFrame({ type: 'unsubscribed' }, { ...ctx, onLive: live, onEvent: events });
    expect(sent).toEqual([]);
    expect(live).not.toHaveBeenCalled();
    expect(events).not.toHaveBeenCalled();
  });
});

describe('ws: conexión con reconexión', () => {
  it('sin token no conecta y reporta caída', () => {
    FakeWebSocket.instances = [];
    const onState = vi.fn();
    connectHub({ topics: ['sales'], token: null, onEvent: () => undefined, onState });
    expect(onState).toHaveBeenCalledWith('down');
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('auth al abrir → ready → subscribe: canal en vivo con backoff reiniciado', () => {
    FakeWebSocket.instances = [];
    const states: string[] = [];
    const events: unknown[] = [];
    const hub = connectHub({
      topics: ['sales', 'restaurant'],
      token: 'jwt-abc',
      onEvent: (e) => events.push(e),
      onState: (s) => states.push(s),
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
    });

    const socket = FakeWebSocket.instances[0];
    // El servidor solo habla tras autenticar: la auth sale al abrir el socket.
    socket.onopen?.();
    expect(socket.sent).toEqual([{ type: 'auth', token: 'jwt-abc' }]);

    socket.serverSends({ type: 'ready' });
    expect(socket.sent[1]).toEqual({ type: 'subscribe', topics: ['sales', 'restaurant'] });

    socket.serverSends({ type: 'subscribed', topics: ['sales', 'restaurant'], denied: [] });
    expect(states).toContain('live');

    socket.serverSends({ type: 'ping' });
    expect(socket.sent.at(-1)).toEqual({ type: 'pong' });

    socket.serverSends({ type: 'event', event: { topic: 'restaurant', type: 'table_opened' } });
    expect(events).toHaveLength(1);

    hub.close();
  });

  it('tras cierre reconecta y re-autentica (backoff inyectado)', async () => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    const hub = connectHub({
      topics: ['sales'],
      token: 'jwt-re',
      onEvent: () => undefined,
      onState: () => undefined,
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
      backoffMs: 10,
    });

    expect(FakeWebSocket.instances).toHaveLength(1);
    FakeWebSocket.instances[0].onclose?.();

    await vi.advanceTimersByTimeAsync(10);
    expect(FakeWebSocket.instances).toHaveLength(2);

    const second = FakeWebSocket.instances[1];
    second.onopen?.();
    expect(second.sent[0]).toEqual({ type: 'auth', token: 'jwt-re' });

    hub.close();
    vi.useRealTimers();
  });

  it('close() detiene la reconexión para siempre', async () => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    const hub = connectHub({
      topics: ['sales'],
      token: 'jwt-x',
      onEvent: () => undefined,
      onState: () => undefined,
      WebSocketCtor: FakeWebSocket as unknown as typeof WebSocket,
      backoffMs: 10,
    });

    hub.close();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.useRealTimers();
  });
});

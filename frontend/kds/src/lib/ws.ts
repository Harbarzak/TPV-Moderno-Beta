/**
 * Cliente mínimo del hub WebSocket (fase 12, ``/api/v1/ws``), adaptación KDS
 * (fase 32): auth → subscribe → eventos. El tablero lo usa como AVISO de
 * invalidación: un evento en el tema «kds» ⇒ releer el tablero; el estado de
 * verdad SIEMPRE llega por GET /kds/board (aquí no se aplica negocio).
 *
 * Protocolo del servidor: ``auth`` es SIEMPRE la primera trama del cliente
 * (el hub cierra con 4401 si no llega a tiempo); tras el ``ready`` del
 * servidor se piden los temas con ``subscribe`` y se responde ``pong`` a cada
 * ``ping``. La reconexión usa backoff (1s → 15s) y el estado se reporta a la
 * UI para gobernar el sondeo de respaldo.
 */

export type HubState = 'connecting' | 'live' | 'down';

export interface HubFrame {
  type: string;
  [key: string]: unknown;
}

export interface HubFrameContext {
  /** Enviar una trama al servidor. */
  send: (frame: HubFrame) => void;
  topics: readonly string[];
  /** El hub aceptó la suscripción: canal en vivo. */
  onLive: () => void;
  /** Evento de negocio (invalidación: «algo cambió»). */
  onEvent: (event: unknown) => void;
}

/** Reductor puro de tramas entrantes: lo prueba todo el cliente real. */
export function handleHubFrame(frame: HubFrame, ctx: HubFrameContext): void {
  switch (frame.type) {
    case 'ready':
      // La auth ya salió al abrir el socket (onopen): «ready» es el visto
      // bueno del servidor para pedir los temas.
      ctx.send({ type: 'subscribe', topics: [...ctx.topics] });
      break;
    case 'subscribed':
      ctx.onLive();
      break;
    case 'ping':
      ctx.send({ type: 'pong' });
      break;
    case 'event':
      ctx.onEvent(frame.event);
      break;
    default:
      // error / unsubscribed / desconocidos: el «down» lo da onclose.
      break;
  }
}

export interface HubOptions {
  topics: string[];
  token: string | null;
  onEvent: (event: unknown) => void;
  onState: (state: HubState) => void;
  /** Inyectable para tests (por defecto, el WebSocket global). */
  WebSocketCtor?: typeof WebSocket;
  /** Retardo base del backoff, en ms (tests). */
  backoffMs?: number;
}

export interface HubConnection {
  close: () => void;
}

const BACKOFF_CAP_MS = 15_000;

/** URL del hub a partir de la página (mismo origen: pasa por el proxy). */
export function hubUrl(): string {
  const secure = window.location.protocol === 'https:';
  return `${secure ? 'wss' : 'ws'}://${window.location.host}/api/v1/ws`;
}

/**
 * Conecta al hub y se mantiene reconectando hasta ``close()``. Sin token no
 * hay canal: reporta ``down`` y no intenta (la auth del hub es obligatoria).
 */
export function connectHub(options: HubOptions): HubConnection {
  const { topics, token, onEvent, onState } = options;
  const WebSocketCtor = options.WebSocketCtor ?? WebSocket;
  const baseBackoff = options.backoffMs ?? 1_000;

  if (!token) {
    onState('down');
    return { close: () => undefined };
  }

  let closed = false;
  let socket: WebSocket | null = null;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const scheduleReconnect = () => {
    if (closed || timer) return;
    const delay = Math.min(baseBackoff * 2 ** attempts, BACKOFF_CAP_MS);
    attempts += 1;
    onState('connecting');
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  };

  const open = () => {
    if (closed) return;
    onState('connecting');
    socket = new WebSocketCtor(hubUrl());
    socket.onopen = () => {
      // El hub exige la auth como PRIMERA trama (§8.1; sin ella cierra 4401):
      // no se espera al «ready» — el servidor solo habla tras autenticar.
      socket?.send(JSON.stringify({ type: 'auth', token }));
    };
    socket.onmessage = (raw: MessageEvent) => {
      let frame: HubFrame;
      try {
        frame = JSON.parse(String(raw.data)) as HubFrame;
      } catch {
        return; // trama no-JSON: ignorar (el servidor habla JSON estricto)
      }
      handleHubFrame(frame, {
        topics,
        send: (out) => socket?.send(JSON.stringify(out)),
        onLive: () => {
          attempts = 0; // suscripción lograda: reinicia el backoff
          onState('live');
        },
        onEvent,
      });
    };
    socket.onclose = () => {
      socket = null;
      if (!closed) scheduleReconnect();
    };
    socket.onerror = () => {
      // onclose llega siempre después de onerror: allí se reprograma.
    };
  };

  open();

  return {
    close: () => {
      closed = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      if (socket) {
        socket.onclose = null;
        socket.close();
        socket = null;
      }
      onState('down');
    },
  };
}

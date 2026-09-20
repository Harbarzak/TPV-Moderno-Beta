/**
 * Conectividad del terminal (fase 14 · Offline): «¿puedo llegar al servidor?».
 * NO basta ``navigator.onLine`` (una WiFi sin salida da falso positivo): se
 * sonda ``GET /healthz`` — un ``TypeError`` (la red ni dejó pasar la petición)
 * significa FUERA DE LÍNEA; cualquier respuesta HTTP (incluso 5xx) significa
 * que el servidor ESCUCHA. Fuera de línea se re-sondea cada ~15 s para volver
 * solo cuando llegue la red; el banner de la venta lee este estado.
 */

import { create } from 'zustand';

export const HEALTHZ = '/api/v1/healthz';
export const POLL_MS = 15_000;

/** Prueba pura de alcance (inyectable para tests). */
export async function checkReachable(
  fetchFn: typeof fetch,
  browserOnline: boolean,
): Promise<boolean> {
  if (!browserOnline) return false;
  try {
    await fetchFn(HEALTHZ, { method: 'GET', cache: 'no-store' });
    return true; // el servidor respondió; su salud la cuenta el cuerpo, no aquí
  } catch {
    return false; // TypeError: sin camino hasta el servidor
  }
}

interface ConnectivityState {
  online: boolean;
  checking: boolean;
  probe: () => Promise<boolean>;
  /** Suscribe eventos del navegador y el re-sondeo; devuelve la limpieza. */
  init: () => () => void;
}

export const useConnectivity = create<ConnectivityState>((set, get) => ({
  online: navigator.onLine,
  checking: false,

  async probe() {
    if (!navigator.onLine) {
      set({ online: false, checking: false });
      return false;
    }
    set({ checking: true });
    const reachable = await checkReachable(fetch, navigator.onLine);
    set({ online: reachable, checking: false });
    return reachable;
  },

  init() {
    void get().probe();

    let timer: ReturnType<typeof setInterval> | null = null;
    const stopPoll = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    // Fuera de línea: sondeo periódico. En línea: sin trabajo de fondo. La
    // suscripción reacciona a CUALQUIER cambio de ``online`` (evento del
    // navegador o veredicto de la sonda, incluida la inicial): si se arranca
    // caído, el ciclo empieza aunque nadie dispare 'offline'.
    const resync = (online: boolean) => {
      if (online) stopPoll();
      else if (timer === null) timer = setInterval(() => void get().probe(), POLL_MS);
    };
    resync(get().online);
    const unsubscribe = useConnectivity.subscribe((state) => resync(state.online));

    const onOffline = () => {
      set({ online: false, checking: false });
    };
    const onOnline = () => {
      void get().probe();
    };

    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);

    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      unsubscribe();
      stopPoll();
    };
  },
}));

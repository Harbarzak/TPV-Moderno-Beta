/**
 * Caché de terminal (ARCHITECTURE.md §7.2): árbol de paneles + snapshot de
 * productos, validados con Zod al recibir. Recargar es explícito (botón o al
 * iniciar); la pantalla de venta NUNCA espera al servidor para pintar.
 *
 * Offline (fase 14): cada carga buena se guarda en localStorage; si el
 * servidor no responde, la tienda arranca de esa copia con ``stale`` a true
 * (el banner avisa) en vez de quedarse en pantalla de error. Un 401 no usa
 * copia: es un problema de sesión, no de red.
 */

import { create } from 'zustand';
import { ApiError, apiFetch } from '../lib/api';
import { loadCachedCatalog, saveCatalog } from '../lib/catalogCache';
import { panelTreeSchema, posCatalogSchema, type Panel, type PosProduct } from '../lib/schemas';
import { useSession } from './session';

type CatalogStatus = 'idle' | 'loading' | 'ready' | 'error';

interface CatalogState {
  status: CatalogStatus;
  error: string | null;
  /** true si lo pintado viene de la copia guardada, no del servidor. */
  stale: boolean;
  panels: Panel[];
  products: PosProduct[];
  load: (force?: boolean) => Promise<void>;
}

interface PanelTreeResponse {
  panels: Panel[];
}

interface PosCatalogResponse {
  products: PosProduct[];
}

export const useCatalog = create<CatalogState>((set, get) => ({
  status: 'idle',
  error: null,
  stale: false,
  panels: [],
  products: [],

  load: async (force = false) => {
    const { status } = get();
    if (!force && (status === 'loading' || status === 'ready')) return;
    set({ status: 'loading', error: null });
    try {
      const [tree, pos] = await Promise.all([
        apiFetch<PanelTreeResponse>('/api/v1/catalog/panels'),
        apiFetch<PosCatalogResponse>('/api/v1/catalog/pos'),
      ]);
      const panels = panelTreeSchema.parse(tree).panels;
      const products = posCatalogSchema.parse(pos).products;
      saveCatalog(panels, products);
      set({ status: 'ready', stale: false, panels, products });
    } catch (err) {
      // Token caducado: fuera de la sesión (clearToken) y de vuelta al acceso.
      if (err instanceof ApiError && err.status === 401) {
        useSession.getState().logout();
        return;
      }
      // Servidor inalcanzable (o contrato roto): la última copia buena
      // mantiene la venta en pie; se refrescará al volver la conexión.
      const cached = loadCachedCatalog();
      if (cached !== null) {
        set({ status: 'ready', stale: true, error: null, panels: cached.panels, products: cached.products });
        return;
      }
      set({ status: 'error', error: err instanceof Error ? err.message : String(err) });
    }
  },
}));

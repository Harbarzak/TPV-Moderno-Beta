/**
 * Copia guardada del catálogo de terminal (fase 14 · Offline): cada carga
 * buena deja en localStorage el último árbol de paneles + snapshot de
 * productos; si el servidor no responde, la venta arranca de la copia en vez
 * de quedarse en pantalla de error. Se valida con los mismos esquemas Zod que
 * la API: una copia corrupta o fuera de contrato se descarta (null), nunca se
 * pinta.
 */

import { panelTreeSchema, posCatalogSchema, type Panel, type PosProduct } from './schemas';

const CATALOG_KEY = 'tpv-terminal-catalog';

export interface CachedCatalog {
  panels: Panel[];
  products: PosProduct[];
}

/** Guarda la última carga buena (fallo de cuota → silencio: es solo una copia). */
export function saveCatalog(panels: Panel[], products: PosProduct[]): void {
  try {
    localStorage.setItem(CATALOG_KEY, JSON.stringify({ panels, products }));
  } catch {
    // Cuota llena o almacenamiento no disponible: la sesión sigue en memoria.
  }
}

/** Última copia buena, o null si no hay / está corrupta / fuera de contrato. */
export function loadCachedCatalog(): CachedCatalog | null {
  const raw = localStorage.getItem(CATALOG_KEY);
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    const panels = panelTreeSchema.parse(parsed).panels;
    const products = posCatalogSchema.parse(parsed).products;
    return { panels, products };
  } catch {
    return null;
  }
}

export function clearCatalogCache(): void {
  localStorage.removeItem(CATALOG_KEY);
}

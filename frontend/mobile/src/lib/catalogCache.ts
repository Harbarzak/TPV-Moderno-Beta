/**
 * Caché del catálogo POS en localStorage (fase 14 · Offline): stale-while-
 * revalidate — se pinta la copia guardada al instante y se refresca contra
 * el servidor cuando responde; si no responde, el catálogo guardado mantiene
 * la venta. Es SOLO catálogo comercial: precios, impuestos y totales reales
 * los recalcula SIEMPRE el backend al añadir la línea.
 */

import { posCatalogSchema, type PosProduct } from './schemas';

const CATALOG_KEY = 'tpv-mobile-catalog';

/** Copia guardada (validada con el mismo contrato que la red) o null. */
export function loadCachedCatalog(): PosProduct[] | null {
  try {
    const raw = localStorage.getItem(CATALOG_KEY);
    if (raw === null) return null;
    return posCatalogSchema.parse(JSON.parse(raw)).products;
  } catch {
    return null; // corrupta o fuera de contrato: se descarta sin ruido
  }
}

export function saveCatalog(products: PosProduct[]): void {
  try {
    localStorage.setItem(CATALOG_KEY, JSON.stringify({ products }));
  } catch {
    /* cuota llena: sin caché; la venta online sigue funcionando */
  }
}

export function clearCatalogCache(): void {
  localStorage.removeItem(CATALOG_KEY);
}

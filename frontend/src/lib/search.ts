/**
 * Búsqueda de productos sobre el snapshot local (§7.2: la terminal trabaja
 * con su caché; buscar no consulta el servidor). Sin acentos ni mayúsculas;
 * una consulta numérica larga se trata como código de barras exacto.
 */

import type { PosProduct } from './schemas';

export interface SearchResult {
  /** Lectura de código de barras exacta (la búsqueda acaba de añadir el producto). */
  exactBarcode: PosProduct | null;
  results: PosProduct[];
}

const MIN_BARCODE_LENGTH = 6;

/** minúsculas y sin diacríticos: "Café" y "cafe" son lo mismo al buscar. */
export function normalize(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFD")
    // rango U+0300–U+036F: marcas diacríticas descompuestas por el NFD
    .replace(/[̀-ͯ]/g, "");
}

export function searchProducts(products: readonly PosProduct[], query: string, limit = 30): SearchResult {
  const needle = normalize(query.trim());
  if (!needle) return { exactBarcode: null, results: [] };

  if (/^\d+$/.test(needle) && needle.length >= MIN_BARCODE_LENGTH) {
    const exact = products.find((product) => product.barcodes.includes(needle));
    return { exactBarcode: exact ?? null, results: [] };
  }

  const starts: PosProduct[] = [];
  const contains: PosProduct[] = [];
  for (const product of products) {
    const haystacks = [
      normalize(product.name),
      product.short_name ? normalize(product.short_name) : "",
      product.sku ? normalize(product.sku) : "",
    ];
    if (haystacks.some((text) => text.startsWith(needle))) {
      starts.push(product);
    } else if (haystacks.some((text) => text.includes(needle))) {
      contains.push(product);
    }
    if (starts.length >= limit) break;
  }
  return { exactBarcode: null, results: [...starts, ...contains].slice(0, limit) };
}

/** Producto por código de barras exacto (para el lector en la pantalla de venta). */
export function findByBarcode(products: readonly PosProduct[], code: string): PosProduct | null {
  return products.find((product) => product.barcodes.includes(code)) ?? null;
}

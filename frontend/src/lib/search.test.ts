import { describe, expect, it } from 'vitest';
import type { PosProduct } from './schemas';
import { findByBarcode, normalize, searchProducts } from './search';

function product(partial: Partial<PosProduct>): PosProduct {
  return {
    id: crypto.randomUUID(),
    name: '',
    short_name: null,
    sku: null,
    category_id: null,
    department_id: null,
    tax_code: 'general',
    tax_rate: '21.00',
    price: '1.00',
    weighable: false,
    kitchen: false,
    sort_order: 0,
    barcodes: [],
    tier_prices: [],
    ...partial,
  };
}

const CATALOG = [
  product({ name: 'Café solo', sku: 'CAF-1', barcodes: ['8412345678903'] }),
  product({ name: 'Café con leche', short_name: 'Cortado' }),
  product({ name: 'Té verde', sku: 'TE-2' }),
  product({ name: 'Croissant', price: '1.20' }),
];

describe('normalize', () => {
  it('quita mayúsculas y diacríticos', () => {
    expect(normalize('Café CON Leche')).toBe('cafe con leche');
    expect(normalize('TÉ')).toBe('te');
  });
});

describe('searchProducts', () => {
  it('busca sin preocuparse por acentos ni mayúsculas', () => {
    const { results } = searchProducts(CATALOG, 'cafe');
    expect(results.map((p) => p.name)).toEqual(['Café solo', 'Café con leche']);
  });

  it('coincide también por alias y SKU, prefijo primero', () => {
    const byAlias = searchProducts(CATALOG, 'corta');
    expect(byAlias.results.map((p) => p.name)).toEqual(['Café con leche']);

    const bySku = searchProducts(CATALOG, 'TE-');
    expect(bySku.results.map((p) => p.name)).toEqual(['Té verde']);
  });

  it('una cifra larga es un código de barras: coincidencia exacta única', () => {
    const hit = searchProducts(CATALOG, '8412345678903');
    expect(hit.exactBarcode?.name).toBe('Café solo');
    expect(hit.results).toEqual([]);

    expect(searchProducts(CATALOG, '9999999999999').exactBarcode).toBeNull();
  });

  it('consulta vacía sin resultados', () => {
    expect(searchProducts(CATALOG, '   ')).toEqual({ exactBarcode: null, results: [] });
  });
});

describe('findByBarcode', () => {
  it('localiza el producto por cualquier de sus códigos', () => {
    expect(findByBarcode(CATALOG, '8412345678903')?.name).toBe('Café solo');
    expect(findByBarcode(CATALOG, '0000')).toBeNull();
  });
});

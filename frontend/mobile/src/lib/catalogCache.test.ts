import { beforeEach, describe, expect, it } from 'vitest';
import { clearCatalogCache, loadCachedCatalog, saveCatalog } from './catalogCache';

const PRODUCT = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'Café solo',
  short_name: null,
  sku: null,
  category_id: null,
  department_id: null,
  tax_code: 'general',
  tax_rate: '21.00',
  price: '1.50',
  weighable: false,
  kitchen: false,
  sort_order: 0,
  barcodes: [],
  tier_prices: [],
};

beforeEach(() => {
  localStorage.clear();
});

describe('caché de catálogo (fase 14 · Offline)', () => {
  it('guarda y recupera la misma lista', () => {
    saveCatalog([PRODUCT]);
    expect(loadCachedCatalog()).toEqual([PRODUCT]);
  });

  it('sin caché → null', () => {
    expect(loadCachedCatalog()).toBeNull();
  });

  it('JSON corrupto → null, sin ruido', () => {
    localStorage.setItem('tpv-mobile-catalog', '{no soy json');
    expect(loadCachedCatalog()).toBeNull();
  });

  it('fuera de contrato (importe roto) → null', () => {
    localStorage.setItem(
      'tpv-mobile-catalog',
      JSON.stringify({ products: [{ ...PRODUCT, price: '1,5' }] }),
    );
    expect(loadCachedCatalog()).toBeNull();
  });

  it('clear elimina la copia', () => {
    saveCatalog([PRODUCT]);
    clearCatalogCache();
    expect(loadCachedCatalog()).toBeNull();
  });
});

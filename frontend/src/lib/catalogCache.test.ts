import { beforeEach, describe, expect, it } from 'vitest';
import { clearCatalogCache, loadCachedCatalog, saveCatalog } from './catalogCache';
import type { PosProduct } from './schemas';

const PRODUCT: PosProduct = {
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

describe('copia guardada del catálogo (fase 14 · Offline)', () => {
  it('guarda y recupera el mismo snapshot', () => {
    saveCatalog([], [PRODUCT]);
    expect(loadCachedCatalog()).toEqual({ panels: [], products: [PRODUCT] });
  });

  it('sin copia → null', () => {
    expect(loadCachedCatalog()).toBeNull();
  });

  it('JSON corrupto → null, sin ruido', () => {
    localStorage.setItem('tpv-terminal-catalog', '{no soy json');
    expect(loadCachedCatalog()).toBeNull();
  });

  it('fuera de contrato (importe roto) → null', () => {
    localStorage.setItem(
      'tpv-terminal-catalog',
      JSON.stringify({ panels: [], products: [{ ...PRODUCT, price: '1,5' }] }),
    );
    expect(loadCachedCatalog()).toBeNull();
  });

  it('clear elimina la copia', () => {
    saveCatalog([], [PRODUCT]);
    clearCatalogCache();
    expect(loadCachedCatalog()).toBeNull();
  });
});

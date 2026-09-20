import { describe, expect, it } from 'vitest';
import { panelTreeSchema, posCatalogSchema } from './schemas';

const POS_PAYLOAD = {
  products: [
    {
      id: '0b6c1a34-7c4f-4a3e-9f0b-2f1d3a5b6c7d',
      name: 'Café solo',
      short_name: null,
      sku: 'CAF-1',
      category_id: null,
      department_id: null,
      tax_code: 'general',
      tax_rate: '21.00',
      price: '1.50',
      weighable: false,
      kitchen: false,
      sort_order: 0,
      barcodes: ['8412345678903'],
      tier_prices: [{ code: 'mayorista', price: '1.20' }],
    },
  ],
};

const TREE_PAYLOAD = {
  panels: [
    {
      id: '1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a',
      name: 'Cafetería',
      sort_order: 0,
      items: [
        {
          id: '2b2b2b2b-2b2b-4b2b-8b2b-2b2b2b2b2b2b',
          label: 'Solo',
          color: '#8b4513',
          grid_row: 0,
          grid_col: 1,
          sort_order: 0,
          product: {
            id: '0b6c1a34-7c4f-4a3e-9f0b-2f1d3a5b6c7d',
            name: 'Café solo',
            short_name: null,
            sku: 'CAF-1',
            price: '1.50',
            tax_code: 'general',
            tax_rate: '21.00',
            weighable: false,
            kitchen: false,
          },
        },
      ],
      subpanels: [
        {
          id: '3c3c3c3c-3c3c-4c3c-8c3c-3c3c3c3c3c3c',
          name: 'Calientes',
          sort_order: 0,
          items: [],
        },
      ],
    },
  ],
};

describe('contrato del snapshot /catalog/pos', () => {
  it('valida la forma completa', () => {
    const parsed = posCatalogSchema.parse(POS_PAYLOAD);
    expect(parsed.products[0].price).toBe('1.50');
  });

  it('rechaza un precio float o con decimales de menos (deriva del §3)', () => {
    const floatPrice = {
      ...POS_PAYLOAD,
      products: [{ ...POS_PAYLOAD.products[0], price: 1.5 }],
    };
    expect(() => posCatalogSchema.parse(floatPrice)).toThrow();

    const shortDecimals = {
      ...POS_PAYLOAD,
      products: [{ ...POS_PAYLOAD.products[0], price: '1.5' }],
    };
    expect(() => posCatalogSchema.parse(shortDecimals)).toThrow();
  });
});

describe('contrato del árbol /catalog/panels', () => {
  it('valida panel con botón directo y subpanel', () => {
    const parsed = panelTreeSchema.parse(TREE_PAYLOAD);
    const panel = parsed.panels[0];
    expect(panel.items[0].label).toBe('Solo');
    expect(panel.items[0].product.price).toBe('1.50');
    expect(panel.subpanels[0].name).toBe('Calientes');
  });

  it('rechaza posiciones de rejilla fuera del máximo 20×20', () => {
    const outside = structuredClone(TREE_PAYLOAD) as typeof TREE_PAYLOAD;
    outside.panels[0].items[0].grid_row = 20;
    expect(() => panelTreeSchema.parse(outside)).toThrow();
  });

  it('rechaza un tree sin el campo money formateado en el producto embebido', () => {
    const broken = structuredClone(TREE_PAYLOAD) as typeof TREE_PAYLOAD;
    (broken.panels[0].items[0].product as { price: string }).price = '1,50';
    expect(() => panelTreeSchema.parse(broken)).toThrow();
  });
});

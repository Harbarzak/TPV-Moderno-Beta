import { beforeEach, describe, expect, it } from 'vitest';
import { UNIT_MILLI } from '../lib/money';
import { useCart, type CartProduct } from './cart';

const CAFE: CartProduct = {
  id: 'p-cafe',
  name: 'Café solo',
  price: '1.50',
  taxCode: 'general',
  taxRate: '21.00',
  weighable: false,
};
const JAMON: CartProduct = {
  id: 'p-jamon',
  name: 'Jamón (kg)',
  price: '12.00',
  taxCode: 'general',
  taxRate: '21.00',
  weighable: true,
};

beforeEach(() => {
  localStorage.clear();
  useCart.getState().clear();
});

describe('ticket actual en el cliente (§4.1)', () => {
  it('añade unitarios acumulando la misma línea', () => {
    useCart.getState().addProduct(CAFE);
    useCart.getState().addProduct(CAFE);
    const { lines } = useCart.getState();
    expect(lines).toHaveLength(1);
    expect(lines[0].qtyMilli).toBe(2 * UNIT_MILLI);
  });

  it('cada peso es una línea propia con sus mili-unidades', () => {
    useCart.getState().addProduct(JAMON, 350); // 0.350 kg
    useCart.getState().addProduct(JAMON, 620);
    const { lines } = useCart.getState();
    expect(lines.map((line) => line.qtyMilli)).toEqual([350, 620]);
  });

  it('sumar y restar cantidad: al llegar a cero la línea se quita', () => {
    useCart.getState().addProduct(CAFE);
    const lineId = useCart.getState().lines[0].lineId;

    useCart.getState().addQty(lineId, UNIT_MILLI);
    expect(useCart.getState().lines[0].qtyMilli).toBe(2 * UNIT_MILLI);

    useCart.getState().addQty(lineId, -UNIT_MILLI);
    useCart.getState().addQty(lineId, -UNIT_MILLI);
    expect(useCart.getState().lines).toHaveLength(0);
  });

  it('removeLine quita solo su línea', () => {
    useCart.getState().addProduct(CAFE);
    useCart.getState().addProduct(JAMON, 100);
    const first = useCart.getState().lines[0].lineId;

    useCart.getState().removeLine(first);
    expect(useCart.getState().lines.map((line) => line.name)).toEqual(['Jamón (kg)']);
  });

  it('el borrador sobrevive a un "reinicio" (persistencia en localStorage)', async () => {
    useCart.getState().addProduct(CAFE);
    useCart.getState().addProduct(JAMON, 250);

    // El estado serializado está en localStorage bajo la clave pactada…
    const raw = localStorage.getItem('tpv-ticket-draft');
    expect(raw).not.toBeNull();
    const persisted = JSON.parse(raw!) as { state: { lines: unknown[] } };
    expect(persisted.state.lines).toHaveLength(2);

    // …y un arranque en frío (storage intacto, estado vacío) rehidrata las líneas.
    // (el persist reescribe el storage en cada cambio: el borrador se restaura después)
    useCart.setState({ lines: [] });
    localStorage.setItem('tpv-ticket-draft', raw!);
    await useCart.persist.rehydrate();
    expect(useCart.getState().lines).toHaveLength(2);
    expect(useCart.getState().lines[1].qtyMilli).toBe(250);
  });

  it('clear vacía el ticket (nueva venta)', () => {
    useCart.getState().addProduct(CAFE);
    useCart.getState().clear();
    expect(useCart.getState().lines).toEqual([]);
  });
});

describe('descuento de línea (fase TPV visual)', () => {
  it('las líneas nuevas nacen sin descuento ("0.00" del contrato dp2)', () => {
    useCart.getState().addProduct(CAFE);
    expect(useCart.getState().lines[0].discountPct).toBe('0.00');
  });

  it('setDiscount cambia SOLO su línea y admite quitarlo ("0.00")', () => {
    useCart.getState().addProduct(CAFE);
    useCart.getState().addProduct(JAMON, 100);
    const [first] = useCart.getState().lines.map((line) => line.lineId);

    useCart.getState().setDiscount(first, '10.00');
    const lines = useCart.getState().lines;
    expect(lines[0].discountPct).toBe('10.00');
    expect(lines[1].discountPct).toBe('0.00');

    useCart.getState().setDiscount(first, '0.00');
    expect(useCart.getState().lines[0].discountPct).toBe('0.00');
  });

  it('migración v1 → v2: las líneas guardadas sin descuento lo heredan a "0.00"', async () => {
    const v1 = {
      state: {
        lines: [
          {
            lineId: 'l-1',
            id: 'p-cafe',
            name: 'Café solo',
            price: '1.50',
            taxCode: 'general',
            taxRate: '21.00',
            weighable: false,
            qtyMilli: 1_000,
          },
        ],
      },
      version: 1,
    };
    localStorage.setItem('tpv-ticket-draft', JSON.stringify(v1));
    await useCart.persist.rehydrate();

    const lines = useCart.getState().lines;
    expect(lines).toHaveLength(1);
    expect(lines[0].discountPct).toBe('0.00');
  });
});

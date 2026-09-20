import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { toast, useToasts } from './toasts';

describe('avisos del mostrador (§4: toast no modal arriba-centro)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useToasts.setState({ items: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('éxito e información caducan solos a los 4 s', () => {
    toast.success('Café solo añadido', { key: 'barcode' });
    toast.info('El ticket está vacío');
    expect(useToasts.getState().items).toHaveLength(2);

    vi.advanceTimersByTime(3_999);
    expect(useToasts.getState().items).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(useToasts.getState().items).toHaveLength(0);
  });

  it('el error persiste hasta que el operador lo cierra', () => {
    toast.error('No se pudo completar el cobro', { detail: 'HTTP 403' });
    vi.advanceTimersByTime(60_000);
    expect(useToasts.getState().items).toHaveLength(1);

    const id = useToasts.getState().items[0].id;
    useToasts.getState().dismiss(id);
    expect(useToasts.getState().items).toHaveLength(0);
  });

  it('la misma clave reemplaza el aviso anterior en vez de acumularse', () => {
    toast.error('Código no encontrado: 841', { key: 'barcode' });
    toast.error('Código no encontrado: 842', { key: 'barcode' });

    const items = useToasts.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0].message).toBe('Código no encontrado: 842');
  });

  it('como mucho 3 avisos: cae el más viejo', () => {
    toast.info('uno');
    toast.info('dos');
    toast.info('tres');
    toast.info('cuatro');

    const items = useToasts.getState().items;
    expect(items.map((item) => item.message)).toEqual(['dos', 'tres', 'cuatro']);
  });

  it('el reemplazo por clave libera hueco antes de recortar al tope', () => {
    toast.error('a', { key: 'a' });
    toast.error('b', { key: 'b' });
    toast.error('c', { key: 'c' });
    toast.error('d', { key: 'd' });
    // q4: quedaban b/c/d; al reponer «a» se recorta al tope−1 antes de añadir.
    toast.error('a2', { key: 'a' });

    const items = useToasts.getState().items;
    expect(items).toHaveLength(3);
    expect(items.map((item) => item.message)).toEqual(['c', 'd', 'a2']);
  });
});

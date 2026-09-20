import { afterEach, describe, expect, it, vi } from 'vitest';
import { attachBarcodeListener, isValidEan13 } from './barcode';

function typeText(text: string) {
  for (const ch of text) {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
  }
}

function pressEnter() {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('lector por keyboard wedge', () => {
  it('detecta una ráfaga rápida terminada en Enter', () => {
    const onScan = vi.fn();
    const detach = attachBarcodeListener(window, onScan, { maxGapMs: 100 });

    typeText('8412345678903');
    pressEnter();

    expect(onScan).toHaveBeenCalledTimes(1);
    expect(onScan).toHaveBeenCalledWith('8412345678903');
    detach();
  });

  it('encadena lecturas seguidas (el buffer se vacía con cada Enter)', () => {
    const onScan = vi.fn();
    const detach = attachBarcodeListener(window, onScan, { maxGapMs: 100 });

    typeText('1111');
    pressEnter();
    typeText('2222');
    pressEnter();

    expect(onScan).toHaveBeenCalledTimes(2);
    expect(onScan).toHaveBeenNthCalledWith(2, '2222');
    detach();
  });

  it('descarta el tecleo lento de una persona', async () => {
    const onScan = vi.fn();
    const detach = attachBarcodeListener(window, onScan, { maxGapMs: 20 });

    typeText('11');
    await new Promise((resolve) => setTimeout(resolve, 40)); // hueco > maxGapMs
    typeText('22');
    pressEnter();

    expect(onScan).not.toHaveBeenCalled();
    detach();
  });

  it('ignora ráfagas más cortas que la longitud mínima', () => {
    const onScan = vi.fn();
    const detach = attachBarcodeListener(window, onScan, { minDigits: 8 });

    typeText('123');
    pressEnter();
    // una persona tecleando su acceso no dispara lecturas
    expect(onScan).not.toHaveBeenCalled();
    detach();
  });

  it('no lee mientras se escribe en un campo de texto', () => {
    const onScan = vi.fn();
    const detach = attachBarcodeListener(window, onScan, { maxGapMs: 100 });

    const input = document.createElement('input');
    document.body.appendChild(input);
    const burst = (target: EventTarget) => {
      for (const ch of '1234') {
        target.dispatchEvent(new KeyboardEvent('keydown', { key: ch, bubbles: true }));
      }
      target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    };
    burst(input);

    expect(onScan).not.toHaveBeenCalled();
    input.remove();
    detach();
  });
});

describe('dígito de control EAN-13', () => {
  it('valida un EAN real y rechaza un dígito cambiado', () => {
    expect(isValidEan13('4006381333931')).toBe(true);
    expect(isValidEan13('4006381333932')).toBe(false);
    expect(isValidEan13('1234')).toBe(false);
  });
});

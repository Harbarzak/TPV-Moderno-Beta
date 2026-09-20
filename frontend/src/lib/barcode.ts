/**
 * Lector de códigos de barras por "keyboard wedge" (ARCHITECTURE.md §1.3).
 *
 * El lector se comporta como un teclado: ráfaga rápida de caracteres que
 * termina en Enter. Se distingue de una persona tecleando por el hueco máximo
 * entre teclas; una ráfaga válida convierte en lectura aunque el foco esté en
 * cualquier parte de la pantalla de venta (nunca dentro de un campo de texto).
 */

export interface BarcodeOptions {
  /** Longitud mínima de una ráfaga para considerarla lectura. */
  minDigits?: number;
  /** Hueco máximo entre teclas (ms). */
  maxGapMs?: number;
}

const DEFAULTS = { minDigits: 4, maxGapMs: 60 } satisfies Required<BarcodeOptions>;

/**
 * Escucha global en fase de captura. Devuelve la función de desconexionado.
 * Las teclas de modificación/navegación y escribir dentro de un input rompen
 * la ráfaga sin generar lecturas falsas.
 */
export function attachBarcodeListener(
  target: Window,
  onScan: (code: string) => void,
  options: BarcodeOptions = {},
): () => void {
  const { minDigits, maxGapMs } = { ...DEFAULTS, ...options };
  let buffer = "";
  let lastAt = 0;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.ctrlKey || event.altKey || event.metaKey) {
      buffer = "";
      return;
    }
    const owner = event.target;
    if (
      owner instanceof HTMLElement &&
      (owner.tagName === "INPUT" ||
        owner.tagName === "TEXTAREA" ||
        owner.tagName === "SELECT" ||
        owner.isContentEditable)
    ) {
      buffer = ""; // el usuario está escribiendo a mano en un campo
      return;
    }

    const now = performance.now();
    if (now - lastAt > maxGapMs) buffer = "";
    lastAt = now;

    if (event.key === "Enter") {
      if (buffer.length >= minDigits) onScan(buffer);
      buffer = "";
      return;
    }
    if (event.key.length === 1) {
      buffer += event.key;
    } else {
      buffer = ""; // teclas de navegación/función: no es una ráfaga del lector
    }
  };

  target.addEventListener("keydown", onKeyDown, true);
  return () => target.removeEventListener("keydown", onKeyDown, true);
}

/** Dígito de control EAN-13/UPC-A (útil para validar lecturas sueltas). */
export function isValidEan13(code: string): boolean {
  if (!/^\d{13}$/.test(code)) return false;
  const digits = [...code].map(Number);
  const sum = digits
    .slice(0, 12)
    .reduce((acc, digit, index) => acc + digit * (index % 2 ? 3 : 1), 0);
  return (10 - (sum % 10)) % 10 === digits[12];
}

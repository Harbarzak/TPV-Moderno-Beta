/**
 * Cantidades pesables (fase 31 · pedido en mesa): el teclado da dígitos con
 * coma y el backend espera qty «X.XXX» (tres decimales, positivo). Aquí vive
 * la conversión y el formateo corto del ticket.
 */

/** Dígitos con coma opcional -> «0.850»; null si la entrada aún no vale. */
export function toQty(raw: string): string | null {
  const normalized = raw.replace(',', '.');
  if (!/^\d{1,4}(\.\d{1,3})?$/.test(normalized)) return null;
  const value = Number.parseFloat(normalized);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value.toFixed(3);
}

/** «1.000» -> «1»; «0.500» -> «0,5» — sin decimales vanos en el ticket. */
export function formatQty(qty: string): string {
  return qty.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '').replace('.', ',');
}

/**
 * Dinero (§3): en la red SIEMPRE string «12.34»; el formateo a «12,34 €» es
 * la única transformación local y es puro presentación. `parseAmount`
 * normaliza lo que escribe el usuario a contrato válido o `null` (no hay
 * cálculo de deuda en el cliente: el total lo dicta el servidor).
 */

const MONEY_RE = /^\d{1,10}\.\d{2}$/;

export function isMoney(value: unknown): value is string {
  return typeof value === 'string' && MONEY_RE.test(value);
}

/** «12.34» → «12,34 €» (es-ES). Un contrato roto se ve, no se esconde. */
export function formatMoney(value: string): string {
  if (!isMoney(value)) return `⚠ ${value}`;
  const [whole, cents] = value.split('.');
  // Agrupación manual: no depende del ICU del entorno (Node/WebView antiguo).
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${grouped},${cents} €`;
}

/**
 * Entrada del usuario («1.5», «1,50», « 2 ») → «1.50» o null. La regla es
 * sintáctica: decidir si el importe sirve para cobrar lo hace el backend.
 */
export function parseAmount(input: string): string | null {
  const normalized = input.trim().replace(',', '.');
  if (!/^\d{1,8}(\.\d{0,2})?$/.test(normalized)) return null;
  const [whole, cents = ''] = normalized.split('.');
  return `${whole}.${(cents + '00').slice(0, 2)}`;
}

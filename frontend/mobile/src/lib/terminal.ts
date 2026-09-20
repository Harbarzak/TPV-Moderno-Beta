/**
 * Terminal asociado al móvil: hoy no existe endpoint de terminales (llega con
 * Administración, fase 21), así que el UUID se configura localmente en
 * Ajustes y el backend lo valida en cada uso. Es configuración de cliente,
 * no lógica de negocio.
 */

const TERMINAL_KEY = 'tpv-mobile-terminal';

export function getTerminalId(): string | null {
  return localStorage.getItem(TERMINAL_KEY);
}

export function setTerminalId(value: string): void {
  localStorage.setItem(TERMINAL_KEY, value);
}

export function isValidUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

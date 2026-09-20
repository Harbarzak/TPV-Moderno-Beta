/**
 * Capacidades del servidor que se sondean una vez por sesión: hoy solo
 * «mesas» (modo restaurante, fase 16). La PWA es tolerante: mientras el
 * backend no exponga el módulo, la sección NO se muestra («mesas si están
 * activas») — cuando la fase 16 añada el endpoint, aparece sola.
 */

export type Capability = 'active' | 'inactive';

const TABLES_CACHE_KEY = 'tpv-mobile-cap-tables';

async function probe(path: string): Promise<Capability> {
  try {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${localStorage.getItem('tpv-mobile-token') ?? ''}` } });
    // 200 → módulo activo. 401/403/404 → no disponible para este usuario o
    // aún no existe: conservador, se oculta. Sin red → se oculta.
    return response.ok ? 'active' : 'inactive';
  } catch {
    return 'inactive';
  }
}

export async function probeTables(): Promise<Capability> {
  const status = await probe('/api/v1/restaurant/tables');
  sessionStorage.setItem(TABLES_CACHE_KEY, status);
  return status;
}

/** Resultado cacheado de la sonda (para el primer render, sin petición). */
export function cachedTables(): Capability {
  return (sessionStorage.getItem(TABLES_CACHE_KEY) as Capability | null) ?? 'inactive';
}

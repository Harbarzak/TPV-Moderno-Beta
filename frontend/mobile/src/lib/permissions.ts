/**
 * Qué ve cada usuario: los permisos los EMITE el backend (GET /auth/me) y
 * aquí solo deciden VISIBILIDAD de secciones — nunca habilitan una operación
 * que el servidor no permitiría (el 403 del backend es la última palabra).
 */

export type Permissions = readonly string[];

export function can(perms: Permissions, permission: string): boolean {
  return perms.includes(permission);
}

export interface MobileSections {
  /** Vender y Pedidos (flujo de venta completo). */
  selling: boolean;
  /** Consulta de productos del catálogo. */
  products: boolean;
  /** Caja: apertura, X, movimientos, arqueos y cierre Z. */
  cash: boolean;
  /** Estadísticas de turno (informe X de la sesión abierta). */
  statsShift: boolean;
  /** Estadísticas de histórico (listado Z). */
  statsHistory: boolean;
}

export function sectionsFor(perms: Permissions): MobileSections {
  return {
    selling: can(perms, 'sales.sell'),
    products: can(perms, 'products.view'),
    cash: can(perms, 'cash.open'),
    statsShift: can(perms, 'cash.open'),
    statsHistory: can(perms, 'reports.view'),
  };
}

/** Acciones concretas dentro del flujo de venta (además de `sales.sell`). */
export interface SellingActions {
  addLines: boolean;
  charge: boolean;
  voidOrder: boolean;
}

export function sellingActionsFor(perms: Permissions): SellingActions {
  return {
    addLines: can(perms, 'sales.sell'),
    charge: can(perms, 'payments.take'),
    voidOrder: can(perms, 'sales.void'),
  };
}

/** Acciones de caja (además de `cash.open`). */
export function canMoveCash(perms: Permissions): boolean {
  return can(perms, 'cash.movements');
}

export function canCloseCash(perms: Permissions): boolean {
  return can(perms, 'cash.close');
}

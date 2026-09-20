/**
 * Qué ve cada administrador: los permisos los EMITE el backend (GET
 * /auth/me) y aquí solo deciden VISIBILIDAD de secciones del panel —
 * nunca habilitan una operación que el servidor no permitiría (el 403
 * del backend es la última palabra).
 */

export type Permissions = readonly string[];

export function can(perms: Permissions, permission: string): boolean {
  return perms.includes(permission);
}

/** Módulos del panel y el permiso que los habilita (ver Section.tsx). */
export type SectionId =
  | 'productos'
  | 'categorias'
  | 'departamentos'
  | 'paneles'
  | 'usuarios'
  | 'permisos'
  | 'camareros'
  | 'formas-pago'
  | 'terminales'
  | 'dispositivos'
  | 'impresoras'
  | 'configuracion'
  | 'backups'
  | 'auditoria';

export interface SectionDef {
  id: SectionId;
  label: string;
  permission: string;
}

export const SECTIONS: SectionDef[] = [
  { id: 'productos', label: 'Productos', permission: 'products.view' },
  { id: 'categorias', label: 'Categorías', permission: 'products.view' },
  { id: 'departamentos', label: 'Departamentos', permission: 'products.view' },
  { id: 'paneles', label: 'Paneles', permission: 'products.view' },
  { id: 'usuarios', label: 'Usuarios', permission: 'admin.users' },
  { id: 'permisos', label: 'Permisos', permission: 'admin.roles' },
  { id: 'camareros', label: 'Camareros', permission: 'admin.users' },
  { id: 'formas-pago', label: 'Formas de pago', permission: 'admin.parameters' },
  { id: 'terminales', label: 'Terminales', permission: 'admin.terminals' },
  { id: 'dispositivos', label: 'Dispositivos', permission: 'admin.terminals' },
  { id: 'impresoras', label: 'Impresoras', permission: 'admin.printers' },
  { id: 'configuracion', label: 'Configuración', permission: 'admin.parameters' },
  { id: 'backups', label: 'Backups', permission: 'admin.backups' },
  { id: 'auditoria', label: 'Auditoría', permission: 'admin.audit' },
];

export function sectionsFor(perms: Permissions): SectionDef[] {
  return SECTIONS.filter((section) => can(perms, section.permission));
}

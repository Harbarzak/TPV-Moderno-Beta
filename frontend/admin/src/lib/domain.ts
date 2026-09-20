/**
 * Contratos de los recursos que administra el panel (§3: el dinero viaja
 * como STRING — el precio del producto es texto, nunca número). Espejo de
 * los response_model del backend: si el servidor cambia, zod avisa aquí.
 */

import { z } from 'zod';
import { paged, uuid } from './schemas';

const iso = z.string();

// ---------------------------------------------------------------------------
// Catálogo (/catalog)
// ---------------------------------------------------------------------------
export const departmentSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type Department = z.infer<typeof departmentSchema>;

export const categorySchema = z.object({
  id: uuid,
  department_id: uuid.nullable(),
  name: z.string(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type Category = z.infer<typeof categorySchema>;

export const taxRateSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  rate: z.string(), // decimal como string (§3)
  valid_from: iso,
  valid_to: iso.nullable(),
});
export type TaxRate = z.infer<typeof taxRateSchema>;

export const productSchema = z.object({
  id: uuid,
  sku: z.string().nullable(),
  name: z.string(),
  short_name: z.string().nullable(),
  category_id: uuid.nullable(),
  tax_rate_id: uuid,
  price: z.string(), // string, nunca float
  weighable: z.boolean(),
  kitchen: z.boolean(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type Product = z.infer<typeof productSchema>;
export const productPageSchema = paged(productSchema);
export type ProductPage = z.infer<typeof productPageSchema>;

export const panelProductSchema = z.object({
  id: uuid,
  name: z.string(),
  short_name: z.string().nullable(),
  sku: z.string().nullable(),
  price: z.string(),
  tax_code: z.string(),
  tax_rate: z.string(),
  weighable: z.boolean(),
  kitchen: z.boolean(),
});

export const panelItemSchema = z.object({
  id: uuid,
  label: z.string().nullable(),
  color: z.string().nullable(),
  grid_row: z.number().int(),
  grid_col: z.number().int(),
  sort_order: z.number().int(),
  product: panelProductSchema,
});
export type PanelItem = z.infer<typeof panelItemSchema>;

export const subPanelSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  items: z.array(panelItemSchema),
});
export type SubPanel = z.infer<typeof subPanelSchema>;

export const panelSchema = z.object({
  id: uuid,
  name: z.string(),
  sort_order: z.number().int(),
  subpanels: z.array(subPanelSchema),
  items: z.array(panelItemSchema),
});
export type Panel = z.infer<typeof panelSchema>;
export const panelTreeSchema = z.object({ panels: z.array(panelSchema) });

// ---------------------------------------------------------------------------
// Usuarios, roles y permisos (/admin)
// ---------------------------------------------------------------------------
export const userSchema = z.object({
  id: uuid,
  username: z.string(),
  full_name: z.string(),
  role_code: z.string(),
  active: z.boolean(),
  has_pin: z.boolean(),
  last_login_at: iso.nullable(),
  created_at: iso,
  updated_at: iso,
});
export type User = z.infer<typeof userSchema>;
export const userPageSchema = paged(userSchema);

export const roleSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  is_system: z.boolean(),
  permissions: z.array(z.string()),
});
export type Role = z.infer<typeof roleSchema>;
export const roleListSchema = z.object({ items: z.array(roleSchema) });

export const permissionSchema = z.object({
  code: z.string(),
  description: z.string(),
});
export type Permission = z.infer<typeof permissionSchema>;
export const permissionListSchema = z.object({ items: z.array(permissionSchema) });

// ---------------------------------------------------------------------------
// Terminales y dispositivos (/admin)
// ---------------------------------------------------------------------------
export const terminalSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  active: z.boolean(),
  created_at: iso,
  updated_at: iso,
});
export type Terminal = z.infer<typeof terminalSchema>;
export const terminalListSchema = z.object({ items: z.array(terminalSchema) });

export const deviceSchema = z.object({
  id: uuid,
  kind: z.string(),
  name: z.string(),
  terminal_id: uuid.nullable(),
  active: z.boolean(),
  last_seen_at: iso.nullable(),
  created_at: iso,
});
export type Device = z.infer<typeof deviceSchema>;
export const deviceListSchema = z.object({ items: z.array(deviceSchema) });

/** El token SOLO existe en la respuesta de alta/rotación: ahí se copia. */
export const deviceEnrolledSchema = z.object({
  device: deviceSchema,
  token: z.string(),
});
export type DeviceEnrolled = z.infer<typeof deviceEnrolledSchema>;

// ---------------------------------------------------------------------------
// Auditoría y backups (/admin)
// ---------------------------------------------------------------------------
export const auditEntrySchema = z.object({
  id: z.number().int(),
  occurred_at: iso,
  user_id: uuid.nullable(),
  username: z.string().nullable(),
  terminal_id: uuid.nullable(),
  action: z.string(),
  entity: z.string(),
  entity_id: uuid.nullable(),
  ip: z.string().nullable(),
  before_data: z.record(z.unknown()).nullable(),
  after_data: z.record(z.unknown()).nullable(),
});
export type AuditEntry = z.infer<typeof auditEntrySchema>;
export const auditPageSchema = paged(auditEntrySchema);

export const backupFileSchema = z.object({
  name: z.string(),
  size_bytes: z.number().int(),
  modified_at: iso,
});
export type BackupFile = z.infer<typeof backupFileSchema>;
export const backupListSchema = z.object({ items: z.array(backupFileSchema) });

// ---------------------------------------------------------------------------
// Formas de pago, impresoras y configuración
// ---------------------------------------------------------------------------
export const paymentMethodSchema = z.object({
  id: uuid,
  code: z.string(),
  name: z.string(),
  kind: z.string(),
  opens_drawer: z.boolean(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
export type PaymentMethod = z.infer<typeof paymentMethodSchema>;
export const paymentMethodListSchema = z.object({ items: z.array(paymentMethodSchema) });

export const printerSchema = z.object({
  id: uuid,
  name: z.string(),
  kind: z.string(),
  connection: z.string(),
  address: z.string().nullable(),
  device_id: uuid.nullable(),
  width_chars: z.number().int(),
  is_default: z.boolean(),
  active: z.boolean(),
  created_at: iso,
  updated_at: iso,
});
export type Printer = z.infer<typeof printerSchema>;
export const printerListSchema = z.object({ items: z.array(printerSchema) });

export const businessSettingsSchema = z.object({
  business: z.record(z.string()),
  logo: z.record(z.unknown()).nullable(),
  series: z.record(z.string()),
  currency: z.record(z.string()),
});
export type BusinessSettings = z.infer<typeof businessSettingsSchema>;

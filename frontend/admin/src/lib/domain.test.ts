/**
 * Los schemas de dominio son el espejo de los response_model: si el
 * backend cambiara de forma (p. ej. dinero como número), estos tests
 * avisan. Recordatorio §3: el dinero viaja como STRING, nunca float.
 */

import { describe, expect, it } from 'vitest';
import {
  auditPageSchema,
  backupListSchema,
  businessSettingsSchema,
  categorySchema,
  departmentSchema,
  deviceEnrolledSchema,
  panelTreeSchema,
  paymentMethodListSchema,
  printerListSchema,
  productPageSchema,
  roleListSchema,
  taxRateSchema,
  terminalListSchema,
  userPageSchema,
} from './domain';

const ID = '11111111-1111-4111-8111-111111111111';
const ID2 = '22222222-2222-4222-8222-222222222222';

describe('catálogo', () => {
  it('departamento y categoría admiten departamento nulo', () => {
    const department = departmentSchema.parse({
      id: ID, code: 'BEBIDAS', name: 'Bebidas', sort_order: 1, active: true,
    });
    expect(department.name).toBe('Bebidas');
    const category = categorySchema.parse({
      id: ID2, department_id: null, name: 'Cafés', sort_order: 0, active: true,
    });
    expect(category.department_id).toBeNull();
  });

  it('el tipo de IVA expone rate como string', () => {
    const rate = taxRateSchema.parse({
      id: ID, code: 'GENERAL', name: 'IVA 21%', rate: '21', valid_from: '2026-01-01', valid_to: null,
    });
    expect(typeof rate.rate).toBe('string');
  });

  it('la página de productos parsea dinero string y RECHAZA float', () => {
    const page = productPageSchema.parse({
      items: [{
        id: ID, sku: 'CAFE-01', name: 'Café con leche', short_name: 'C/LECHE',
        category_id: null, tax_rate_id: ID2, price: '1.50',
        weighable: false, kitchen: true, sort_order: 0, active: true,
      }],
      total: 1, limit: 100, offset: 0,
    });
    expect(page.items[0]?.price).toBe('1.50');
    expect(() =>
      productPageSchema.parse({
        items: [{
          id: ID, sku: null, name: 'X', short_name: null, category_id: null,
          tax_rate_id: ID2, price: 1.5, weighable: false, kitchen: false,
          sort_order: 0, active: true,
        }],
        total: 1, limit: 100, offset: 0,
      }),
    ).toThrow();
  });

  it('el árbol de paneles trae snapshot de producto con IVA string', () => {
    const tree = panelTreeSchema.parse({
      panels: [{
        id: ID, name: 'Bebidas', sort_order: 0,
        subpanels: [{
          id: ID2, name: 'Cafés', sort_order: 0,
          items: [{
            id: ID, label: 'C/L', color: '#4f46e5', grid_row: 0, grid_col: 1,
            sort_order: 0,
            product: {
              id: ID2, name: 'Café con leche', short_name: 'C/LECHE', sku: null,
              price: '1.50', tax_code: 'GENERAL', tax_rate: '21',
              weighable: false, kitchen: false,
            },
          }],
        }],
        items: [],
      }],
    });
    expect(tree.panels[0]?.subpanels[0]?.items[0]?.product.price).toBe('1.50');
  });
});

describe('usuarios, roles y terminales', () => {
  it('usuario con last_login nulo y has_pin', () => {
    const page = userPageSchema.parse({
      items: [{
        id: ID, username: 'cajero', full_name: 'Ana García', role_code: 'admin',
        active: true, has_pin: true, last_login_at: null,
        created_at: '2026-01-01T10:00:00', updated_at: '2026-01-01T10:00:00',
      }],
      total: 1, limit: 50, offset: 0,
    });
    expect(page.items[0]?.has_pin).toBe(true);
    expect(page.items[0]?.last_login_at).toBeNull();
  });

  it('roles con matriz de permisos', () => {
    const roles = roleListSchema.parse({
      items: [{ id: ID, code: 'admin', name: 'Administrador', is_system: true, permissions: ['admin.users', 'sales.sell'] }],
    });
    expect(roles.items[0]?.permissions).toContain('sales.sell');
  });

  it('terminales y alta de dispositivo con token en claro SOLO aquí', () => {
    const terminals = terminalListSchema.parse({
      items: [{ id: ID, code: 'TPV-01', name: 'Barra', active: true, created_at: '2026-01-01', updated_at: '2026-01-01' }],
    });
    expect(terminals.items).toHaveLength(1);
    const enrolled = deviceEnrolledSchema.parse({
      device: {
        id: ID2, kind: 'agent', name: 'Agente barra', terminal_id: null,
        active: true, last_seen_at: null, created_at: '2026-01-01',
      },
      token: 'tpvdev_tokensecreto',
    });
    expect(enrolled.token).toContain('tpvdev_');
    expect(Object.keys(enrolled.device)).not.toContain('token_hash');
  });
});

describe('auditoría, backups, pagos, impresoras y configuración', () => {
  it('entrada de auditoría con before/after nulos', () => {
    const page = auditPageSchema.parse({
      items: [{
        id: 7, occurred_at: '2026-01-01T10:00:00', user_id: null, username: null,
        terminal_id: null, action: 'admin.user_created', entity: 'user',
        entity_id: ID, ip: '127.0.0.1', before_data: null,
        after_data: { username: 'alguien' },
      }],
      total: 1, limit: 50, offset: 0,
    });
    expect(page.items[0]?.before_data).toBeNull();
  });

  it('listado de backups con tamaño entero', () => {
    const backups = backupListSchema.parse({
      items: [{ name: 'tpv_20260913_030000.dump', size_bytes: 1048576, modified_at: '2026-09-13T03:00:00' }],
    });
    expect(backups.items[0]?.size_bytes).toBe(1048576);
  });

  it('formas de pago con kind del enum', () => {
    const methods = paymentMethodListSchema.parse({
      items: [{ id: ID, code: 'EFECTIVO', name: 'Efectivo', kind: 'cash', opens_drawer: true, sort_order: 0, active: true }],
    });
    expect(methods.items[0]?.opens_drawer).toBe(true);
  });

  it('impresora de red con address nulo si va por agente', () => {
    const printers = printerListSchema.parse({
      items: [{
        id: ID, name: 'Cocina', kind: 'kitchen', connection: 'agent',
        address: null, device_id: ID2, width_chars: 42, is_default: false,
        active: true, created_at: '2026-01-01', updated_at: '2026-01-01',
      }],
    });
    expect(printers.items[0]?.address).toBeNull();
  });

  it('configuración de negocio con logo nulo', () => {
    const settings = businessSettingsSchema.parse({
      business: { name: 'Bar Ejemplo', tax_id: '', address: '', phone: '' },
      logo: null,
      series: { ticket: 'T-2026-' },
      currency: { code: 'EUR', symbol: '€' },
    });
    expect(settings.business.name).toBe('Bar Ejemplo');
    expect(settings.logo).toBeNull();
  });
});

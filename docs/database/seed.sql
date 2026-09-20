-- =============================================================================
-- TPV · Datos iniciales mínimos (fase 01 · Base de datos)
-- =============================================================================
-- Idempotente (ON CONFLICT DO NOTHING): seguro relanzarlo o sobre esquema poblado.
-- NO crea usuarios NI credenciales: eso lo hace el instalador con secretos nuevos
-- y rotados (ADR-008). Aplicar tras `alembic upgrade head`.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Roles base del sistema
-- -----------------------------------------------------------------------------
INSERT INTO roles (code, name, is_system) VALUES
    ('admin',   'Administrador', true),
    ('manager', 'Encargado',     true),
    ('waiter',  'Camarero',      true)
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Permisos (namespace.recurso). Los de venta son sales.* (antes orders.*;
-- renombrados en la fase Administración, migración 0004 los sincroniza en
-- BDs existentes).
-- -----------------------------------------------------------------------------
INSERT INTO permissions (code, description) VALUES
    ('products.view',      'Consultar catálogo de productos y paneles'),
    ('products.edit',      'Crear y modificar productos, categorías y paneles'),
    ('customers.view',     'Consultar clientes'),
    ('customers.edit',     'Crear y modificar clientes'),
    ('sales.sell',         'Crear y cobrar ventas'),
    ('orders.discount',    'Aplicar descuentos en líneas o venta'),
    ('sales.void',         'Anular ventas (requiere motivo)'),
    ('payments.take',      'Registrar cobros'),
    ('payments.refund',    'Registrar devoluciones de importe'),
    ('cash.open',          'Abrir sesión de caja'),
    ('cash.close',         'Cerrar sesión de caja con arqueo'),
    ('cash.movements',     'Registrar entradas/salidas de efectivo'),
    ('tickets.reprint',    'Reimprimir tickets históricos'),
    ('invoices.issue',     'Emitir facturas'),
    ('invoices.void',      'Anular facturas (requiere motivo)'),
    ('reports.view',       'Consultar informes'),
    ('kds.operate',        'Operar la pantalla de cocina (KDS)'),
    ('restaurant.operate', 'Operar el plano de mesas'),
    ('admin.users',        'Gestionar usuarios y roles'),
    ('admin.terminals',    'Gestionar terminales y dispositivos'),
    ('admin.printers',     'Gestionar impresoras y cola de impresión'),
    ('admin.parameters',   'Modificar parámetros del sistema'),
    ('admin.roles',        'Gestionar roles y permisos'),
    ('admin.audit',        'Consultar la auditoría del sistema'),
    ('admin.backups',      'Ejecutar y consultar backups'),
    ('fiscal.view',        'Consultar documentos fiscales y estado del plugin'),
    ('fiscal.dispatch',    'Ejecutar la descarga fiscal (consumir y certificar)')
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Asignación de permisos por rol
-- -----------------------------------------------------------------------------
-- admin: todos los permisos.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r CROSS JOIN permissions p
WHERE r.code = 'admin'
ON CONFLICT DO NOTHING;

-- manager: operativo y de gestión, sin administración del sistema.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r JOIN permissions p
  ON p.code IN (
      'products.view', 'products.edit', 'customers.view', 'customers.edit',
      'sales.sell', 'orders.discount', 'sales.void',
      'payments.take', 'payments.refund',
      'cash.open', 'cash.close', 'cash.movements',
      'tickets.reprint', 'invoices.issue', 'invoices.void',
      'reports.view', 'kds.operate', 'restaurant.operate',
      'fiscal.view', 'fiscal.dispatch'
  )
WHERE r.code = 'manager'
ON CONFLICT DO NOTHING;

-- waiter: solo el operativo de barra/sala.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r JOIN permissions p
  ON p.code IN (
      'products.view', 'customers.view',
      'sales.sell', 'orders.discount',
      'payments.take',
      'cash.open',
      'tickets.reprint', 'kds.operate', 'restaurant.operate'
  )
WHERE r.code = 'waiter'
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- Tipos de IVA vigentes en España (vigencia desde el 2026-01-01)
-- -----------------------------------------------------------------------------
INSERT INTO tax_rates (code, name, rate, valid_from) VALUES
    ('exento',         'Exento',              0.00, DATE '2026-01-01'),
    ('superreducido',  'IVA superreducido',   4.00, DATE '2026-01-01'),
    ('reducido',       'IVA reducido',       10.00, DATE '2026-01-01'),
    ('general',        'IVA general',        21.00, DATE '2026-01-01')
ON CONFLICT (code, valid_from) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Formas de pago básicas
-- -----------------------------------------------------------------------------
INSERT INTO payment_methods (code, name, kind, opens_drawer, sort_order) VALUES
    ('CASH',   'Efectivo',         'cash',   true,  1),
    ('CARD',   'Tarjeta',          'card',   false, 2),
    ('CREDIT', 'A cuenta (crédito)', 'credit', false, 3)
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Parámetros por defecto (valores neutros; el instalador los ajusta)
-- -----------------------------------------------------------------------------
INSERT INTO parameters (key, value, description) VALUES
    ('tickets.series',        '"A"',      'Serie de numeración de tickets por defecto'),
    ('invoices.series',       '"FAC"',    'Serie de numeración de facturas'),
    ('currency.decimals',     '2',        'Decimales de redondeo del importe'),
    ('restaurant.enabled',    'false',    'Habilita el módulo de restaurante (mesas/URY-style)'),
    ('kitchen.enabled',       'false',    'Habilita el pase de comandas a cocina (KDS)'),
    ('fiscal.provider',       '"none"',   'Régimen fiscal del plugin (none | verifactu | ticketbai)')
ON CONFLICT (key) DO NOTHING;

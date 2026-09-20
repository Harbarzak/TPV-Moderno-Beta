-- =============================================================================
-- TPV Moderno — Esquema PostgreSQL (fuente de verdad DDL)
-- Fase 01 · Base de datos · 2026-09-11
-- Motor: PostgreSQL 16+. Ejecutar con un rol de instalación; la aplicación usa
-- un rol propio con privilegios mínimos (sin DDL, sin acceso a catálogos del
-- sistema; sobre audit_log: sin UPDATE/DELETE).
-- Convenciones: dinero numeric(12,2) · cantidades numeric(10,3) · tasas
-- numeric(5,2) · tiempos timestamptz UTC · PK uuid gen_random_uuid().
-- =============================================================================

-- ----------------------------------------------------------------------------
-- Extensiones y funciones auxiliares
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid (core >=13, se declara por si acaso)

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------------------
-- Enums
-- ----------------------------------------------------------------------------
CREATE TYPE order_status    AS ENUM ('draft', 'paid', 'voided');
CREATE TYPE order_type      AS ENUM ('bar', 'restaurant', 'takeaway');
CREATE TYPE payment_kind    AS ENUM ('cash', 'card', 'voucher', 'credit', 'other');
CREATE TYPE payment_status  AS ENUM ('pending', 'confirmed', 'failed', 'reversed');
CREATE TYPE cash_move_kind  AS ENUM ('in', 'out');
CREATE TYPE kitchen_status  AS ENUM ('pending', 'preparing', 'ready', 'served', 'cancelled');
CREATE TYPE device_kind     AS ENUM ('agent', 'printer', 'pinpad', 'cashdrawer', 'display');
CREATE TYPE printer_kind    AS ENUM ('receipt', 'kitchen', 'invoice');
CREATE TYPE printer_conn    AS ENUM ('network', 'agent');
CREATE TYPE print_job_kind  AS ENUM ('ticket', 'invoice', 'kitchen', 'report_x', 'report_z', 'test');
CREATE TYPE print_job_status AS ENUM ('queued', 'sent', 'printed', 'failed', 'cancelled');
CREATE TYPE sequence_scope  AS ENUM ('ticket', 'invoice');
CREATE TYPE invoice_status  AS ENUM ('issued', 'voided');
CREATE TYPE sale_event_type AS ENUM ('sale_closed', 'sale_voided', 'refund_issued');
CREATE TYPE fiscal_document_type   AS ENUM ('sale', 'void', 'refund');
CREATE TYPE fiscal_document_status AS ENUM ('pending', 'sent', 'accepted', 'rejected', 'cancelled');
CREATE TYPE fiscal_event_kind      AS ENUM ('queued', 'dispatched', 'accepted', 'rejected', 'cancelled');

-- =============================================================================
-- DOMINIO: Seguridad y personal
-- =============================================================================
CREATE TABLE roles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        text NOT NULL UNIQUE,
    name        text NOT NULL,
    is_system   boolean NOT NULL DEFAULT false,   -- los roles base no se borran
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE permissions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code        text NOT NULL UNIQUE,             -- p.ej. 'orders.void'
    description text NOT NULL
);

CREATE TABLE role_permissions (
    role_id       uuid NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    permission_id uuid NOT NULL REFERENCES permissions (id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username     text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    pin_hash     text,                             -- PIN corto de terminal (opcional)
    full_name    text NOT NULL,
    role_id      uuid NOT NULL REFERENCES roles (id),
    active       boolean NOT NULL DEFAULT true,    -- soft delete
    last_login_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_users_pin_xor_password_len CHECK (char_length(username) BETWEEN 2 AND 64)
);
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE user_sessions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,               -- hash del refresh token, nunca el token
    ip         inet,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CONSTRAINT ck_user_sessions_expiry CHECK (expires_at > created_at)
);
CREATE INDEX ix_user_sessions_user ON user_sessions (user_id);

-- =============================================================================
-- DOMINIO: Terminales y dispositivos
-- =============================================================================
CREATE TABLE terminals (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code       text NOT NULL UNIQUE,               -- 'TPV-1'
    name       text NOT NULL,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_terminals_updated BEFORE UPDATE ON terminals
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE devices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    terminal_id  uuid REFERENCES terminals (id),   -- null = dispositivo del servidor
    kind         device_kind NOT NULL,
    name         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,             -- autenticación del agente/dispositivo
    last_seen_at timestamptz,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_devices_terminal ON devices (terminal_id);

-- =============================================================================
-- DOMINIO: Catálogo (Productos)
-- =============================================================================
CREATE TABLE departments (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code       text NOT NULL UNIQUE,
    name       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_departments_updated BEFORE UPDATE ON departments
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE categories (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id uuid REFERENCES departments (id),
    name          text NOT NULL,
    sort_order    integer NOT NULL DEFAULT 0,
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_categories_department ON categories (department_id);
CREATE TRIGGER trg_categories_updated BEFORE UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE tax_rates (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code       text NOT NULL,                      -- 'general', 'reducido', 'superreducido'
    name       text NOT NULL,
    rate       numeric(5,2) NOT NULL CHECK (rate >= 0 AND rate <= 100),
    valid_from date NOT NULL,
    valid_to   date,                               -- null = vigente
    CONSTRAINT uq_tax_rates_code_valid_from UNIQUE (code, valid_from),
    CONSTRAINT ck_tax_rates_period CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX ix_tax_rates_current ON tax_rates (code) WHERE valid_to IS NULL;

CREATE TABLE products (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sku         text UNIQUE,                       -- opcional
    name        text NOT NULL,
    short_name  text,                              -- etiqueta corta para la rejilla
    category_id uuid REFERENCES categories (id),
    tax_rate_id uuid NOT NULL REFERENCES tax_rates (id),   -- IVA vigente por defecto
    price       numeric(12,2) NOT NULL CHECK (price >= 0), -- precio actual (denormalizado)
    weighable   boolean NOT NULL DEFAULT false,    -- venta por peso
    kitchen     boolean NOT NULL DEFAULT false,    -- requiere preparación (va a KDS)
    sort_order  integer NOT NULL DEFAULT 0,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_products_name_len CHECK (char_length(name) BETWEEN 1 AND 120)
);
CREATE INDEX ix_products_category ON products (category_id);
CREATE INDEX ix_products_name ON products (name);
CREATE TRIGGER trg_products_updated BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Histórico inmutable de precios; al cambiar precio: cerrar vigente + insertar nuevo.
CREATE TABLE product_prices (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    price      numeric(12,2) NOT NULL CHECK (price >= 0),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to   timestamptz
);
-- Un solo precio vigente por producto (parcial):
CREATE UNIQUE INDEX uq_product_prices_current ON product_prices (product_id)
    WHERE valid_to IS NULL;
CREATE INDEX ix_product_prices_product ON product_prices (product_id, valid_from);

-- Tarifas de precios (fase 04 · Productos): contextos con precio propio
-- ('bar', 'terraza', 'hotel'…); el precio general es products.price.
CREATE TABLE price_tiers (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code       text NOT NULL UNIQUE,
    name       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_price_tiers_updated BEFORE UPDATE ON price_tiers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Precio del producto en cada tarifa (override); sin fila aplica el general.
CREATE TABLE product_tier_prices (
    product_id uuid NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    tier_id    uuid NOT NULL REFERENCES price_tiers (id),
    price      numeric(12,2) NOT NULL CHECK (price >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, tier_id)
);

-- Códigos de barras del producto (varios posibles; único en todo el catálogo).
CREATE TABLE product_barcodes (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    barcode    text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Imágenes del producto (ruta/referencia; sort_order 0 = principal).
CREATE TABLE product_images (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    path       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_product_images_product ON product_images (product_id);

CREATE TABLE panels (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_panels_updated BEFORE UPDATE ON panels
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE subpanels (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    panel_id   uuid NOT NULL REFERENCES panels (id),
    name       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_subpanels_panel ON subpanels (panel_id);
CREATE TRIGGER trg_subpanels_updated BEFORE UPDATE ON subpanels
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Botones de rejilla: pertenecen a un panel o a un subpanel (xor).
CREATE TABLE panel_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    panel_id    uuid REFERENCES panels (id),
    subpanel_id uuid REFERENCES subpanels (id),
    product_id  uuid NOT NULL REFERENCES products (id),
    label       text,                              -- override del nombre
    color       text,
    grid_row    integer NOT NULL DEFAULT 0,
    grid_col    integer NOT NULL DEFAULT 0,
    sort_order  integer NOT NULL DEFAULT 0,
    CONSTRAINT ck_panel_items_parent CHECK (
        (panel_id IS NOT NULL AND subpanel_id IS NULL) OR
        (panel_id IS NULL AND subpanel_id IS NOT NULL)
    )
);
CREATE INDEX ix_panel_items_panel ON panel_items (panel_id);
CREATE INDEX ix_panel_items_subpanel ON panel_items (subpanel_id);
CREATE INDEX ix_panel_items_product ON panel_items (product_id);

-- =============================================================================
-- DOMINIO: Clientes
-- =============================================================================
CREATE TABLE customers (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tax_id       text UNIQUE,                      -- NIF/CIF (para facturas)
    name         text NOT NULL,
    address      text,
    city         text,
    postal_code  text,
    email        text,
    phone        text,
    discount_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
    notes        text,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_customers_name ON customers (name);
CREATE TRIGGER trg_customers_updated BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- DOMINIO: Formas de pago
-- =============================================================================
CREATE TABLE payment_methods (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE,             -- 'CASH', 'CARD'
    name         text NOT NULL,
    kind         payment_kind NOT NULL,
    opens_drawer boolean NOT NULL DEFAULT false,
    sort_order   integer NOT NULL DEFAULT 0,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_payment_methods_updated BEFORE UPDATE ON payment_methods
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =============================================================================
-- DOMINIO: Caja
-- =============================================================================
CREATE TABLE cash_sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    terminal_id     uuid NOT NULL REFERENCES terminals (id),
    opened_by       uuid NOT NULL REFERENCES users (id),
    closed_by       uuid REFERENCES users (id),
    opening_amount  numeric(12,2) NOT NULL CHECK (opening_amount >= 0),
    expected_amount numeric(12,2),                -- al cierre
    counted_amount  numeric(12,2),                -- recuento físico
    difference      numeric(12,2),                -- counted - expected
    opened_at       timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,
    -- Un cierre exige recuento completo:
    CONSTRAINT ck_cash_sessions_close_complete CHECK (
        closed_at IS NULL OR
        (expected_amount IS NOT NULL AND counted_amount IS NOT NULL AND difference IS NOT NULL)
    ),
    CONSTRAINT ck_cash_sessions_order CHECK (closed_at IS NULL OR closed_at > opened_at)
);
-- Una sola sesión abierta por terminal (concurrencia resuelta en BD):
CREATE UNIQUE INDEX uq_cash_sessions_open_per_terminal ON cash_sessions (terminal_id)
    WHERE closed_at IS NULL;
CREATE INDEX ix_cash_sessions_opened ON cash_sessions (opened_at);

CREATE TABLE cash_counts (                       -- arqueos (puede haber varios por sesión)
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cash_session_id uuid NOT NULL REFERENCES cash_sessions (id) ON DELETE CASCADE,
    counted_by      uuid NOT NULL REFERENCES users (id),
    counted_amount  numeric(12,2) NOT NULL CHECK (counted_amount >= 0),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_cash_counts_session ON cash_counts (cash_session_id);

CREATE TABLE cash_count_lines (
    cash_count_id uuid NOT NULL REFERENCES cash_counts (id) ON DELETE CASCADE,
    denomination  numeric(10,2) NOT NULL CHECK (denomination > 0),
    quantity      integer NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (cash_count_id, denomination)
);

CREATE TABLE cash_movements (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    cash_session_id uuid NOT NULL REFERENCES cash_sessions (id),
    payment_id      uuid,                          -- FK diferida a payments (abajo)
    kind            cash_move_kind NOT NULL,
    amount          numeric(12,2) NOT NULL CHECK (amount > 0),
    reason          text NOT NULL,
    user_id         uuid NOT NULL REFERENCES users (id),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_cash_movements_session ON cash_movements (cash_session_id);

-- =============================================================================
-- DOMINIO: Numeración (concurrencia por SELECT … FOR UPDATE)
-- =============================================================================
CREATE TABLE document_sequences (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope         sequence_scope NOT NULL,
    terminal_id   uuid REFERENCES terminals (id),  -- tickets: por terminal; facturas: null
    series        text NOT NULL,
    year          smallint,                        -- facturas: por año; tickets: null
    current_value bigint NOT NULL DEFAULT 0 CHECK (current_value >= 0),
    CONSTRAINT uq_document_sequences UNIQUE NULLS NOT DISTINCT (scope, terminal_id, year, series)
);

-- =============================================================================
-- DOMINIO: Ventas
-- =============================================================================
CREATE TABLE zones (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    active     boolean NOT NULL DEFAULT true
);

CREATE TABLE dining_tables (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id    uuid NOT NULL REFERENCES zones (id),
    name       text NOT NULL,
    seats      smallint NOT NULL CHECK (seats > 0),
    sort_order integer NOT NULL DEFAULT 0,
    pos_x      numeric(7,2),                 -- plano 2D (fase 30); NULL = sin colocar
    pos_y      numeric(7,2),
    active     boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_dining_tables_zone ON dining_tables (zone_id);
CREATE UNIQUE INDEX uq_dining_tables_zone_name ON dining_tables (zone_id, name);
CREATE TRIGGER trg_dining_tables_updated BEFORE UPDATE ON dining_tables
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE orders (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    terminal_id     uuid NOT NULL REFERENCES terminals (id),
    user_id         uuid NOT NULL REFERENCES users (id),
    cash_session_id uuid REFERENCES cash_sessions (id),
    customer_id     uuid REFERENCES customers (id),
    dining_table_id uuid REFERENCES dining_tables (id),
    status          order_status NOT NULL DEFAULT 'draft',
    order_type      order_type NOT NULL DEFAULT 'bar',
    guest_count     smallint CHECK (guest_count > 0),
    note            text,
    bill_requested_at timestamptz,              -- «cuenta pedida» (fase 30)
    total_base      numeric(12,2),
    total_tax       numeric(12,2),
    total_amount    numeric(12,2),
    tax_summary     jsonb,                        -- [{rate, base, amount}]
    created_at      timestamptz NOT NULL DEFAULT now(),
    paid_at         timestamptz,
    voided_at       timestamptz,
    voided_by       uuid REFERENCES users (id),
    void_reason     text,
    -- Un cobrado siempre tiene sesión y hora de cobro:
    CONSTRAINT ck_orders_paid_complete CHECK (
        status <> 'paid' OR (cash_session_id IS NOT NULL AND paid_at IS NOT NULL
                             AND total_amount IS NOT NULL)
    ),
    -- Una anulación exige motivo y autor:
    CONSTRAINT ck_orders_void_complete CHECK (
        status <> 'voided' OR (voided_at IS NOT NULL AND voided_by IS NOT NULL
                               AND void_reason IS NOT NULL)
    ),
    -- Un draft nunca tiene totales (una venta anulada sí los conserva):
    CONSTRAINT ck_orders_draft_no_totals CHECK (status IN ('paid', 'voided') OR total_amount IS NULL)
);
-- Una sola comanda abierta por mesa:
CREATE UNIQUE INDEX uq_orders_open_per_table ON orders (dining_table_id)
    WHERE status = 'draft' AND dining_table_id IS NOT NULL;
CREATE INDEX ix_orders_status_created ON orders (status, created_at);
CREATE INDEX ix_orders_cash_session ON orders (cash_session_id);
CREATE INDEX ix_orders_terminal_created ON orders (terminal_id, created_at);
CREATE INDEX ix_orders_user_created ON orders (user_id, created_at);

-- Líneas con SNAPSHOT de nombre/precio/IVA (inmutables tras el cobro).
CREATE TABLE order_lines (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           uuid NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id         uuid REFERENCES products (id),   -- null = artículo libre
    name               text NOT NULL,                   -- snapshot
    unit_price         numeric(12,2) NOT NULL CHECK (unit_price >= 0),  -- snapshot
    tax_rate           numeric(5,2) NOT NULL CHECK (tax_rate BETWEEN 0 AND 100), -- snapshot
    quantity           numeric(10,3) NOT NULL CHECK (quantity <> 0),    -- negativo en devoluciones
    discount_pct       numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
    line_base          numeric(12,2) NOT NULL,
    line_total         numeric(12,2) NOT NULL,
    notes              text,
    sort_order         integer NOT NULL DEFAULT 0,
    created_at         timestamptz NOT NULL DEFAULT now(),
    voided_at          timestamptz,                     -- anulación de línea (draft restaurante)
    voided_by          uuid REFERENCES users (id),
    CONSTRAINT ck_order_lines_void CHECK (
        voided_at IS NULL OR (voided_by IS NOT NULL)
    )
);
CREATE INDEX ix_order_lines_order ON order_lines (order_id);
CREATE INDEX ix_order_lines_product ON order_lines (product_id);

CREATE TABLE payments (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           uuid NOT NULL REFERENCES orders (id),
    payment_method_id  uuid NOT NULL REFERENCES payment_methods (id),
    terminal_id        uuid REFERENCES terminals (id),
    device_id          uuid REFERENCES devices (id),   -- pinpad que ejecutó el cobro
    amount             numeric(12,2) NOT NULL CHECK (amount > 0),
    status             payment_status NOT NULL DEFAULT 'confirmed',
    external_ref       text,                            -- referencia del TPV físico
    created_at         timestamptz NOT NULL DEFAULT now(),
    confirmed_at       timestamptz
);
CREATE INDEX ix_payments_order ON payments (order_id);
CREATE INDEX ix_payments_method_created ON payments (payment_method_id, created_at);

ALTER TABLE cash_movements
    ADD CONSTRAINT fk_cash_movements_payment FOREIGN KEY (payment_id) REFERENCES payments (id);

CREATE TABLE tickets (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id      uuid NOT NULL UNIQUE REFERENCES orders (id),
    terminal_id   uuid NOT NULL REFERENCES terminals (id),
    series        text NOT NULL,
    number        bigint NOT NULL CHECK (number > 0),
    payload       jsonb NOT NULL,                  -- render congelado para reimpresión
    printed_at    timestamptz,
    reprint_count integer NOT NULL DEFAULT 0 CHECK (reprint_count >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_tickets_terminal_series_number UNIQUE (terminal_id, series, number)
);
CREATE INDEX ix_tickets_created ON tickets (created_at);

CREATE TABLE invoices (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id uuid NOT NULL REFERENCES customers (id),
    series      text NOT NULL,
    year        smallint NOT NULL,
    number      bigint NOT NULL CHECK (number > 0),
    status      invoice_status NOT NULL DEFAULT 'issued',
    issue_date  date NOT NULL,
    total_base  numeric(12,2) NOT NULL,
    total_tax   numeric(12,2) NOT NULL,
    total_amount numeric(12,2) NOT NULL,
    tax_summary jsonb NOT NULL,
    payload     jsonb NOT NULL,
    voided_at   timestamptz,
    void_reason text,
    rectified_invoice_id uuid REFERENCES invoices (id),  -- factura que rectifica (fase 09)
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_invoices_series_year_number UNIQUE (series, year, number),
    CONSTRAINT ck_invoices_void CHECK (status <> 'voided' OR (voided_at IS NOT NULL AND void_reason IS NOT NULL))
);
CREATE INDEX ix_invoices_customer ON invoices (customer_id);
CREATE INDEX ix_invoices_issue_date ON invoices (year, issue_date);
CREATE INDEX ix_invoices_rectified ON invoices (rectified_invoice_id);

CREATE TABLE invoice_lines (
    invoice_id uuid NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    order_id   uuid NOT NULL UNIQUE REFERENCES orders (id),  -- una venta solo en una factura
    PRIMARY KEY (invoice_id, order_id)
);

CREATE TABLE refunds (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    original_order_id uuid NOT NULL UNIQUE REFERENCES orders (id),
    refund_order_id   uuid NOT NULL UNIQUE REFERENCES orders (id),  -- venta negativa generada
    user_id           uuid NOT NULL REFERENCES users (id),
    amount            numeric(12,2) NOT NULL CHECK (amount > 0),
    reason            text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Eventos genéricos de venta para el FUTURO adaptador fiscal (sin lógica fiscal aquí).
CREATE TABLE sale_events (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id      uuid NOT NULL REFERENCES orders (id),
    event_type    sale_event_type NOT NULL,
    payload       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    dispatched_at timestamptz                      -- consumido por el adaptador fiscal
);
CREATE INDEX ix_sale_events_pending ON sale_events (created_at) WHERE dispatched_at IS NULL;
CREATE INDEX ix_sale_events_order ON sale_events (order_id);

-- =============================================================================
-- DOMINIO: Fiscalidad (plugin desacoplado · fase 34 · ADR-010)
-- =============================================================================
-- El motor de ventas solo emite sale_events; este plugin los consume y
-- certifica vía FiscalAdapter (none | verifactu | ticketbai). El histórico de
-- cambios ES fiscal_events (append-only): fiscal_documents no lleva updated_at.

CREATE TABLE fiscal_documents (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sale_event_id  uuid NOT NULL UNIQUE REFERENCES sale_events (id),  -- transformación idempotente
    order_id       uuid NOT NULL REFERENCES orders (id),
    doc_type       fiscal_document_type NOT NULL,        -- sale | void | refund
    provider       text NOT NULL,                        -- régimen que lo certifica
    status         fiscal_document_status NOT NULL DEFAULT 'pending',
    payload        jsonb NOT NULL,                       -- snapshot fiscal (dinero string)
    external_ref   text,                                 -- CSV AEAT / código TBAI…
    error_code     text,
    error_message  text,
    attempts       integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_fiscal_documents_status ON fiscal_documents (status, created_at);
CREATE INDEX ix_fiscal_documents_order ON fiscal_documents (order_id);

CREATE TABLE fiscal_events (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL REFERENCES fiscal_documents (id),
    kind          fiscal_event_kind NOT NULL,   -- queued | dispatched | accepted | rejected | cancelled
    detail        jsonb,
    actor_user_id uuid REFERENCES users (id),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_fiscal_events_document ON fiscal_events (document_id, created_at);

-- =============================================================================
-- DOMINIO: Restaurante / KDS
-- =============================================================================
CREATE TABLE kitchen_orders (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   uuid NOT NULL UNIQUE REFERENCES orders (id),
    status     kitchen_status NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at   timestamptz,
    served_at  timestamptz,
    CONSTRAINT ck_kitchen_orders_flow CHECK (
        (ready_at IS NULL OR created_at <= ready_at) AND
        (served_at IS NULL OR ready_at IS NOT NULL AND served_at >= ready_at)
    )
);

CREATE TABLE kitchen_order_lines (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kitchen_order_id uuid NOT NULL REFERENCES kitchen_orders (id) ON DELETE CASCADE,
    order_line_id    uuid NOT NULL REFERENCES order_lines (id),
    name             text NOT NULL,               -- snapshot
    quantity         numeric(10,3) NOT NULL CHECK (quantity > 0),
    notes            text,
    status           kitchen_status NOT NULL DEFAULT 'pending',
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_kitchen_lines_order ON kitchen_order_lines (kitchen_order_id);

-- =============================================================================
-- DOMINIO: Impresión
-- =============================================================================
CREATE TABLE printers (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    kind         printer_kind NOT NULL,
    connection   printer_conn NOT NULL,
    address      text,                            -- 'host:port' (network)
    device_id    uuid REFERENCES devices (id),    -- impresora colgada de un tpv-agent
    width_chars  smallint NOT NULL DEFAULT 42 CHECK (width_chars IN (32, 42, 48)),
    is_default   boolean NOT NULL DEFAULT false,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_printers_connection CHECK (
        (connection = 'network' AND address IS NOT NULL AND device_id IS NULL) OR
        (connection = 'agent'   AND address IS NULL     AND device_id IS NOT NULL)
    )
);
-- Un solo default por tipo de impresora:
CREATE UNIQUE INDEX uq_printers_default_per_kind ON printers (kind) WHERE is_default AND active;
CREATE TRIGGER trg_printers_updated BEFORE UPDATE ON printers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE print_jobs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    printer_id  uuid NOT NULL REFERENCES printers (id),
    kind        print_job_kind NOT NULL,
    payload     jsonb NOT NULL,
    status      print_job_status NOT NULL DEFAULT 'queued',
    dedupe_key  text UNIQUE,                     -- idempotencia ante replay WS
    attempts    integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    sent_at     timestamptz,
    printed_at  timestamptz
);
CREATE INDEX ix_print_jobs_queue ON print_jobs (printer_id, created_at)
    WHERE status IN ('queued', 'sent');

-- =============================================================================
-- DOMINIO: Sistema
-- =============================================================================
-- Auditoría APPEND-ONLY: al crear el rol de aplicación ejecutar
--   REVOKE UPDATE, DELETE ON audit_log FROM <rol_app>;
CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    user_id     uuid REFERENCES users (id),      -- null = acción del sistema
    terminal_id uuid REFERENCES terminals (id),
    action      text NOT NULL,                   -- 'orders.void', 'cash.movement', …
    entity      text NOT NULL,
    entity_id   uuid,
    before_data jsonb,
    after_data  jsonb,
    ip          inet
);
CREATE INDEX ix_audit_log_entity ON audit_log (entity, entity_id);
CREATE INDEX ix_audit_log_user_time ON audit_log (user_id, occurred_at);
CREATE INDEX ix_audit_log_time ON audit_log (occurred_at);

-- Bus de eventos WebSocket: PK bigserial = cursor de replay.
CREATE TABLE event_log (
    id            bigserial PRIMARY KEY,
    event_id      uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    topic         text NOT NULL,                 -- 'sales', 'kds', 'terminal:<id>', …
    type          text NOT NULL,                 -- 'sales.created', 'printer.down', …
    payload       jsonb,
    actor_user_id uuid REFERENCES users (id),
    occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_event_log_topic_id ON event_log (topic, id);

CREATE TABLE parameters (
    key                text PRIMARY KEY,
    value              jsonb NOT NULL,
    description        text,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    updated_by_user_id uuid REFERENCES users (id)
);

CREATE TABLE idempotency_keys (
    key                 text PRIMARY KEY,
    endpoint            text NOT NULL,
    request_fingerprint text,
    response_status     integer,
    response_body       jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    CONSTRAINT ck_idempotency_expiry_window CHECK (expires_at > created_at)
);
CREATE INDEX ix_idempotency_expiry ON idempotency_keys (expires_at);

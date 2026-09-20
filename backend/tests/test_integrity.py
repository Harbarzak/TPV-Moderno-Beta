"""Tests de integridad del esquema TPV (fase 01 · Base de datos).

Validan contra PostgreSQL real (migración aplicada por conftest):
claves ajenas, unicidades (totales, parciales y NULLS NOT DISTINCT),
constraints CHECK de coherencia económica, triggers y la idempotencia
de los datos iniciales. No prueban funcionalidad de aplicación.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import exc, text

from tests.conftest import (
    apply_seed, new_cash_session, new_order, new_order_line, new_product,
    new_role, new_sequence, new_tax_rate, new_terminal, new_user,
    new_zone_and_table,
)


def _integrity_error(conn, sql, **params):
    with pytest.raises(exc.IntegrityError):
        conn.execute(text(sql), params)
    conn.rollback()


# ---------------------------------------------------------------------------
# Integridad referencial
# ---------------------------------------------------------------------------
def test_fk_violation_order_bad_terminal(db):
    _integrity_error(
        db,
        "INSERT INTO orders (terminal_id, user_id) VALUES (:t, :u)",
        t=uuid4(), u=uuid4(),
    )


def test_soft_deleted_product_keeps_fk(db):
    """El soft delete del catálogo no rompe las ventas: la línea puede referenciar
    a un producto desactivado (la FK sigue intacta)."""
    t, u = new_terminal(db), new_user(db, new_role(db))
    p = new_product(db, new_tax_rate(db), active=False)
    o = new_order(db, t, u)
    db.commit()
    db.execute(
        text("INSERT INTO order_lines (order_id, product_id, name, unit_price, tax_rate, "
             "quantity, line_base, line_total) VALUES (:o, :p, 'Café', 1.5, 21, 1, 1.5, 1.5)"),
        {"o": o, "p": p},
    )
    db.commit()
    n = db.execute(
        text("SELECT count(*) FROM order_lines WHERE product_id = :p"), {"p": p}
    ).scalar_one()
    assert n == 1


# ---------------------------------------------------------------------------
# Numeración documental
# ---------------------------------------------------------------------------
def test_tickets_unique_per_terminal(db):
    t1, t2 = new_terminal(db, "T1"), new_terminal(db, "T2")
    u = new_user(db, new_role(db))
    sql = ("INSERT INTO tickets (order_id, terminal_id, series, number, payload) "
           "VALUES (:o, :t, 'A', 1, '{}')")
    o1, o2, o3 = (new_order(db, t1, u) for _ in range(3))
    db.execute(text(sql), {"o": o1, "t": t1})
    db.commit()
    _integrity_error(db, sql, o=o2, t=t1)          # mismo terminal/serie/número
    db.execute(text(sql), {"o": o3, "t": t2})       # otro terminal: número repetido OK
    db.commit()


def test_document_sequence_concurrent_for_update(db, engine):
    """La reserva de número serializa con SELECT … FOR UPDATE: nadie obtiene
    el mismo número y el contador avanza exactamente lo asignado."""
    seq = new_sequence(db, terminal_id=new_terminal(db))
    stmt = text(
        "SELECT current_value FROM document_sequences WHERE id = :i FOR UPDATE"
    )
    with engine.connect() as a:
        va = a.execute(stmt, {"i": seq}).scalar_one()
        a.execute(
            text("UPDATE document_sequences SET current_value = :v WHERE id = :i"),
            {"v": va + 1, "i": seq},
        )
        # Otra conexión no puede reservar mientras A mantiene el bloqueo.
        with pytest.raises(exc.DBAPIError):
            with engine.connect() as b:
                b.execute(text(stmt.text + " NOWAIT"), {"i": seq})
        a.commit()

    with engine.connect() as b:
        vb = b.execute(stmt, {"i": seq}).scalar_one()
        b.execute(
            text("UPDATE document_sequences SET current_value = :v WHERE id = :i"),
            {"v": vb + 1, "i": seq},
        )
        b.commit()
    final = db.execute(
        text("SELECT current_value FROM document_sequences WHERE id = :i"), {"i": seq}
    ).scalar_one()
    db.commit()
    assert final == 2


# ---------------------------------------------------------------------------
# Caja
# ---------------------------------------------------------------------------
def test_one_open_cash_session_per_terminal(db):
    t = new_terminal(db)
    u = new_user(db, new_role(db))
    s1 = new_cash_session(db, t, u)
    _integrity_error(
        db,
        "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount) "
        "VALUES (:t, :u, 50.00)",
        t=t, u=u,
    )
    db.execute(
        text("UPDATE cash_sessions SET closed_at = now(), expected_amount = 100, "
             "counted_amount = 100, difference = 0 WHERE id = :s"),
        {"s": s1},
    )
    db.commit()
    s2 = new_cash_session(db, t, u)  # cerrada la anterior, se puede abrir otra
    db.commit()
    assert s2


def test_cash_session_close_requires_counts(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    s = new_cash_session(db, t, u)
    db.commit()
    _integrity_error(
        db,
        "UPDATE cash_sessions SET closed_at = now() WHERE id = :s", s=s,
    )


# ---------------------------------------------------------------------------
# Órdenes y líneas (coherencia económica)
# ---------------------------------------------------------------------------
def test_paid_order_requires_cash_session_and_totals(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    cs = new_cash_session(db, t, u)
    db.commit()
    _integrity_error(
        db,
        "INSERT INTO orders (terminal_id, user_id, status, paid_at, total_base, "
        "total_tax, total_amount) VALUES (:t, :u, 'paid', now(), 10, 2.1, 12.1)",
        t=t, u=u,                       # sin cash_session_id
    )
    o = new_order(db, t, u, cash_session_id=cs, status="paid")
    db.commit()
    assert o


def test_order_line_snapshots_not_null(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    o = new_order(db, t, u)
    db.commit()
    _integrity_error(
        db,
        "INSERT INTO order_lines (order_id, name, unit_price, tax_rate, quantity, "
        "line_base, line_total) VALUES (:o, NULL, 1.5, 21, 1, 1.5, 1.5)",
        o=o,                            # snapshot de nombre obligatorio
    )


def test_order_line_discount_bounds(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    o = new_order(db, t, u)
    db.commit()
    _integrity_error(
        db,
        "INSERT INTO order_lines (order_id, name, unit_price, tax_rate, quantity, "
        "discount_pct, line_base, line_total) VALUES (:o, 'X', 1.5, 21, 1, 150, 1.5, 1.5)",
        o=o,
    )


def test_order_line_quantity_not_zero(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    o = new_order(db, t, u)
    db.commit()
    _integrity_error(
        db,
        "INSERT INTO order_lines (order_id, name, unit_price, tax_rate, quantity, "
        "line_base, line_total) VALUES (:o, 'X', 1.5, 21, 0, 0, 0)",
        o=o,
    )


def test_one_draft_per_dining_table(db):
    _, table = new_zone_and_table(db)
    t, u = new_terminal(db), new_user(db, new_role(db))
    cs = new_cash_session(db, t, u)
    db.commit()
    o1 = new_order(db, t, u, cash_session_id=cs, table_id=table, order_type="restaurant")
    db.commit()
    assert o1
    _integrity_error(
        db,
        "INSERT INTO orders (terminal_id, user_id, dining_table_id, order_type) "
        "VALUES (:t, :u, :dt, 'restaurant')",
        t=t, u=u, dt=table,             # segunda borrador abierta en la mesa
    )
    # Cobrada la primera, la mesa queda libre para un nuevo borrador.
    db.execute(
        text("UPDATE orders SET status = 'paid', paid_at = now(), total_base = 10, "
             "total_tax = 2.1, total_amount = 12.1 WHERE id = :o"),
        {"o": o1},
    )
    db.commit()
    o2 = new_order(db, t, u, cash_session_id=cs, table_id=table, order_type="restaurant")
    db.commit()
    assert o2


# ---------------------------------------------------------------------------
# Trigger de auditoría updated_at
# ---------------------------------------------------------------------------
def test_updated_at_trigger_fires(db):
    p = new_product(db, new_tax_rate(db))
    db.commit()
    before = db.execute(
        text("SELECT updated_at FROM products WHERE id = :p"), {"p": p}
    ).scalar_one()
    db.execute(text("UPDATE products SET name = 'Té' WHERE id = :p"), {"p": p})
    db.commit()
    after = db.execute(
        text("SELECT updated_at FROM products WHERE id = :p"), {"p": p}
    ).scalar_one()
    assert after > before


# ---------------------------------------------------------------------------
# Sistema: idempotencia y event log
# ---------------------------------------------------------------------------
def test_idempotency_key_dedup(db):
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    sql = ("INSERT INTO idempotency_keys (key, endpoint, expires_at) "
           "VALUES (:k, 'POST /api/orders', :e)")
    db.execute(text(sql), {"k": "abc", "e": exp})
    db.commit()
    _integrity_error(db, sql, k="abc", e=exp)


def test_event_log_ids_monotonic(db):
    ids = []
    for _ in range(5):
        i = db.execute(
            text("INSERT INTO event_log (topic, type) VALUES ('sales', 'sale_closed') "
                 "RETURNING id")
        ).scalar_one()
        db.commit()
        ids.append(i)
    assert ids == sorted(ids) and len(set(ids)) == 5


# ---------------------------------------------------------------------------
# Facturas y devoluciones
# ---------------------------------------------------------------------------
def test_invoice_series_unique(db):
    cust = db.execute(
        text("INSERT INTO customers (name) VALUES ('Cliente') RETURNING id")
    ).scalar_one()
    db.commit()
    sql = ("INSERT INTO invoices (customer_id, series, year, number, issue_date, "
           "total_base, total_tax, total_amount, tax_summary, payload) "
           "VALUES (:c, 'FAC', 2026, 1, CURRENT_DATE, 10, 2.1, 12.1, '{}', '{}')")
    db.execute(text(sql), {"c": cust})
    db.commit()
    _integrity_error(db, sql, c=cust)


def test_refund_validation(db):
    t, u = new_terminal(db), new_user(db, new_role(db))
    # Una orden paid exige caja (ck_orders_paid_complete: cash_session_id NOT NULL).
    original = new_order(db, t, u, cash_session_id=new_cash_session(db, t, u),
                         status="paid")
    refund = new_order(db, t, u)
    db.commit()
    sql = ("INSERT INTO refunds (original_order_id, refund_order_id, user_id, amount, "
           "reason) VALUES (:o, :r, :u, :a, 'error de caja')")
    db.execute(text(sql), {"o": original, "r": refund, "u": u, "a": 5.0})
    db.commit()
    _integrity_error(db, sql, o=original, r=refund, u=u, a=5.0)  # ya enlazada
    _integrity_error(db, sql, o=original, r=uuid4(), u=u, a=-1)  # importe <= 0 / FK


def test_print_job_dedupe_key(db):
    printer = db.execute(
        text("INSERT INTO printers (name, kind, connection, address) "
             "VALUES ('TPV', 'receipt', 'network', '192.168.1.50:9100') RETURNING id")
    ).scalar_one()
    db.commit()
    sql = ("INSERT INTO print_jobs (printer_id, kind, payload, dedupe_key) "
           "VALUES (:p, 'ticket', '{}', :d)")
    db.execute(text(sql), {"p": printer, "d": "ticket-1"})
    db.commit()
    _integrity_error(db, sql, p=printer, d="ticket-1")
    db.execute(text(sql), {"p": printer, "d": None})
    db.execute(text(sql), {"p": printer, "d": None})  # NULL repetido permitido
    db.commit()


# ---------------------------------------------------------------------------
# Datos iniciales (seed.sql)
# ---------------------------------------------------------------------------
def test_seed_sql_applies_and_is_idempotent(db):
    for _ in range(2):  # aplicarla dos veces no duplica nada
        apply_seed(db)

    counts = dict(db.execute(text(
        "SELECT 'roles', count(*) FROM roles UNION ALL "
        "SELECT 'permissions', count(*) FROM permissions UNION ALL "
        "SELECT 'role_permissions', count(*) FROM role_permissions UNION ALL "
        "SELECT 'tax_rates', count(*) FROM tax_rates UNION ALL "
        "SELECT 'payment_methods', count(*) FROM payment_methods UNION ALL "
        "SELECT 'parameters', count(*) FROM parameters"
    )).all())

    assert counts["roles"] == 3
    assert counts["permissions"] == 27
    assert counts["tax_rates"] == 4
    assert counts["payment_methods"] == 3
    assert counts["parameters"] == 6
    # admin tiene todos los permisos; nadie más usa permisos de admin.*.
    total_perms, admin_perms = db.execute(text(
        "SELECT (SELECT count(*) FROM permissions), "
        "(SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
        " WHERE r.code = 'admin')"
    )).one()
    assert admin_perms == total_perms
    assert db.execute(text(
        "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
        "JOIN permissions p ON p.id = rp.permission_id "
        "WHERE r.code <> 'admin' AND p.code LIKE 'admin.%'"
    )).scalar_one() == 0

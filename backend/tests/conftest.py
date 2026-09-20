"""Fixtures de los tests de integridad (fase 01 · Base de datos).

Ejecutan la migración Alembic real contra una base de PostgreSQL de pruebas,
de modo que lo que se valida es exactamente el esquema que se despliega.

Requiere la variable de entorno TPV_TEST_DATABASE_URL, por ejemplo:
    postgresql+psycopg://tpv:***@localhost:5432/tpv_test
Sin esa variable, todos los tests de integración se marcan como skip.
Nunca se escriben credenciales en el código (ADR-008).
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text

if sys.platform == "win32":
    # psycopg async exige un SelectorEventLoop: no funciona sobre el Proactor
    # que Windows elige por defecto. Los portales de TestClient crean el loop
    # con events.new_event_loop(), que sigue la política aquí fijada.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOCS_SEED_SQL = BACKEND_DIR.parent / "docs" / "database" / "seed.sql"


# ---------------------------------------------------------------------------
# Esquema
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("TPV_TEST_DATABASE_URL")
    if not url:
        pytest.skip("TPV_TEST_DATABASE_URL no definida: se omiten los tests de integración")

    engine = create_engine(url, future=True)
    engine.connect().close()  # falla rápido si no hay servidor

    # Aplica la migración inicial real (alembic env.py lee TPV_DATABASE_URL).
    os.environ["TPV_DATABASE_URL"] = url
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine):
    """Conexión limpia por test: vacía todas las tablas antes de empezar.

    En AUTOCOMMIT: los INSERT de las fábricas quedan visibles de inmediato para
    la app que prueba el TestClient (otra conexión); sin esto, los datos de las
    fábricas morirían con la transacción abierta del propio test.
    """
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    tables = [t for t in inspect(engine).get_table_names() if t != "alembic_version"]
    if tables:
        quoted = ", ".join(f'"{t}"' for t in tables)
        conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
        conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Fábricas de datos mínimas (insertan y confirman; devuelven el id)
# ---------------------------------------------------------------------------
def _insert(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar_one()


def apply_seed(conn) -> None:
    """Aplica docs/database/seed.sql (roles, permisos, IVA, formas de pago...).

    El fixture ``db`` vacía TODAS las tablas en cada test, también el catálogo
    base que insertan las migraciones de datos: los tests que lo asumen deben
    re-sembrar. Idempotente (ON CONFLICT DO NOTHING). Se eliminan antes las
    líneas de comentario: alguna lleva «;» dentro y partiría el SQL.
    """
    script = DOCS_SEED_SQL.read_text(encoding="utf-8")
    lines = [ln for ln in script.splitlines() if not ln.lstrip().startswith("--")]
    for stmt in (s.strip() for s in "\n".join(lines).split(";")):
        if stmt:
            conn.execute(text(stmt))
    conn.commit()


def new_role(conn, code="waiter", name="Camarero"):
    return _insert(
        conn,
        "INSERT INTO roles (code, name) VALUES (:c, :n) RETURNING id",
        c=code, n=name,
    )


def new_user(conn, role_id, username=None):
    return _insert(
        conn,
        "INSERT INTO users (username, password_hash, full_name, role_id) "
        "VALUES (:u, 'test-hash', 'Usuario Test', :r) RETURNING id",
        u=username or f"user-{uuid4().hex[:8]}", r=role_id,
    )


def new_terminal(conn, code=None):
    return _insert(
        conn,
        "INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id",
        c=code or f"T-{uuid4().hex[:6]}", n="Terminal test",
    )


def new_cash_session(conn, terminal_id, user_id):
    return _insert(
        conn,
        "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount) "
        "VALUES (:t, :u, 100.00) RETURNING id",
        t=terminal_id, u=user_id,
    )


def new_zone_and_table(conn):
    zone = _insert(conn, "INSERT INTO zones (name) VALUES ('Sala') RETURNING id")
    table = _insert(
        conn,
        "INSERT INTO dining_tables (zone_id, name, seats) VALUES (:z, 'M1', 4) RETURNING id",
        z=zone,
    )
    return zone, table


def new_tax_rate(conn, code="general", rate=21.00):
    return _insert(
        conn,
        "INSERT INTO tax_rates (code, name, rate, valid_from) "
        "VALUES (:c, :n, :r, DATE '2026-01-01') RETURNING id",
        c=code, n=f"IVA {rate}", r=rate,
    )


def new_product(conn, tax_rate_id, name="Café", price=1.50, active=True):
    return _insert(
        conn,
        "INSERT INTO products (name, tax_rate_id, price, active) "
        "VALUES (:n, :t, :p, :a) RETURNING id",
        n=name, t=tax_rate_id, p=price, a=active,
    )


def new_order(conn, terminal_id, user_id, cash_session_id=None, table_id=None,
              status="draft", order_type="bar"):
    sql = (
        "INSERT INTO orders (terminal_id, user_id, cash_session_id, dining_table_id, "
        "status, order_type{extra}) VALUES (:t, :u, :cs, :dt, :s, :ot{extra_vals}) RETURNING id"
    )
    params = dict(t=terminal_id, u=user_id, cs=cash_session_id, dt=table_id,
                  s=status, ot=order_type)
    extra = extra_vals = ""
    if status == "paid":
        extra = ", paid_at, total_base, total_tax, total_amount"
        extra_vals = ", now(), 10.00, 2.10, 12.10"
    return _insert(conn, sql.format(extra=extra, extra_vals=extra_vals), **params)


def new_order_line(conn, order_id, name="Café", unit_price=1.50, tax_rate=21.00,
                   quantity=1.0, discount_pct=0):
    return _insert(
        conn,
        "INSERT INTO order_lines (order_id, name, unit_price, tax_rate, quantity, "
        "discount_pct, line_base, line_total) "
        "VALUES (:o, :n, :p, :tr, :q, :d, :b, :tot) RETURNING id",
        o=order_id, n=name, p=unit_price, tr=tax_rate, q=quantity, d=discount_pct,
        b=unit_price * quantity, tot=unit_price * quantity * (1 - discount_pct / 100),
    )


def new_sequence(conn, scope="ticket", series="A", terminal_id=None):
    return _insert(
        conn,
        "INSERT INTO document_sequences (scope, terminal_id, series, year, current_value) "
        "VALUES (:s, :t, :se, 2026, 0) RETURNING id",
        s=scope, t=terminal_id, se=series,
    )

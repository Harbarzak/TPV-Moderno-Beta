"""Tests E2E de hardware (fase 11) contra PostgreSQL real.

Cubren FASE_11 desde el motor de ventas: el cierre del pedido abre el cajón
SOLO cuando algún pago usa una forma con ``opens_drawer``, SIEMPRE después del
commit (una venta cobrada nunca se revierte por un periférico) y de forma
best-effort (un cajón atascado se registra en el log pero el cobro responde
200 con su ticket). La sustitución del adaptador en ``app.state.hardware``
demostrará lo que exige el prompt: cambiar la integración no toca el motor.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella).
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.hardware import FailingCashDrawerAdapter, NullCashDrawerAdapter
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
SALES = f"{V1}/sales"
AUTH = f"{V1}/auth"
CASH = f"{V1}/cash"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

BOSS_PERMS = ("products.view", "products.edit", "sales.sell",
              "payments.take", "cash.open")


def _settings(**overrides) -> Settings:
    base = dict(
        env="test",
        log_level="WARNING",
        database_url=TEST_DB_URL,
        db_null_pool=True,  # TestClient: un event loop por petición
        jwt_secret=SECRET,
        auth_rate_limit_attempts=50,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
def app():
    if not TEST_DB_URL:
        pytest.skip("TPV_TEST_DATABASE_URL no definida: se omiten los tests de integración")
    return create_app(_settings())


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def drawer(app):
    """Sustituye SOLO el cajón registrado y lo restaura al salir (el fixture
    ``app`` es session-scoped: no podemos filtrar estado)."""

    original = app.state.hardware.drawer
    app.state.hardware.drawer = NullCashDrawerAdapter()
    yield app.state.hardware.drawer
    app.state.hardware.drawer = original


# ---------------------------------------------------------------------------
# Fábricas mínimas (mismo patrón que test_printing_api.py)
# ---------------------------------------------------------------------------
def _mk_role(db, code: str, permissions: tuple[str, ...] = ()) -> int:
    role_id = db.execute(
        text("INSERT INTO roles (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": code},
    ).scalar_one()
    for perm in permissions:
        perm_id = db.execute(
            text(
                "INSERT INTO permissions (code, description) VALUES (:c, 'permiso de prueba') "
                "ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code RETURNING id"
            ),
            {"c": perm},
        ).scalar_one()
        db.execute(
            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
            {"r": role_id, "p": perm_id},
        )
    db.commit()
    return role_id


def _mk_user(db, role_id, username="cajero", password=CLAVE) -> int:
    return db.execute(
        text(
            "INSERT INTO users (username, password_hash, full_name, role_id) "
            "VALUES (:u, :ph, :fn, :r) RETURNING id"
        ),
        {"u": username, "ph": hash_password(password), "fn": "Usuario de Prueba", "r": role_id},
    ).scalar_one()


def _login(client, username="cajero", password=CLAVE):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


def _headers(db, client, perms=BOSS_PERMS, username="cajero") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return {"Authorization": f"Bearer {_login(client, username).json()['access_token']}"}


def _mk_terminal(db, code="T1") -> str:
    return str(db.execute(
        text("INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": f"Terminal {code}"},
    ).scalar_one())


def _mk_payment_method(db, code="CASH", name="Efectivo", kind="cash") -> str:
    return str(db.execute(
        text(
            "INSERT INTO payment_methods (code, name, kind, opens_drawer) "
            "VALUES (:c, :n, :k, :o) RETURNING id"
        ),
        {"c": code, "n": name, "k": kind, "o": kind == "cash"},
    ).scalar_one())


def _tax_and_product(client, h) -> dict:
    tax = client.post(f"{V1}/catalog/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    return client.post(f"{V1}/catalog/products", headers=h, json={
        "name": "Café solo", "tax_rate_id": tax["id"], "price": "1.50",
    }).json()


def _pay(method_id: str, amount: str) -> dict:
    return {"payment_method_id": method_id, "amount": amount}


def _close_sale(client, h, order_id, cash_session_id, payments) -> "object":
    return client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})


def _cash_session(client, h, terminal_id) -> str:
    resp = client.post(f"{CASH}/sessions", headers=h,
                       json={"terminal_id": terminal_id, "opening_amount": "50.00"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _sale_and_close(client, h, db, terminal_id, method_id, *, amount="3.00"):
    """Crea y cobra 2 × 1.50; devuelve la respuesta (cruda) del cierre."""
    product = _tax_and_product(client, h)
    order = client.post(f"{SALES}/orders", headers=h,
                        json={"terminal_id": terminal_id}).json()
    client.post(f"{SALES}/orders/{order['id']}/lines", headers=h,
                json={"product_id": product["id"], "quantity": "2"})
    return _close_sale(
        client, h, order["id"], _cash_session(client, h, terminal_id),
        [_pay(method_id, amount)],
    )


# ---------------------------------------------------------------------------
# 1 · Efectivo (opens_drawer=True) → el cajón se abre con el terminal del pedido
# ---------------------------------------------------------------------------
def test_el_efectivo_abre_el_cajon(db, client, drawer):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)  # cash → opens_drawer=True

    resp = _sale_and_close(client, h, db, terminal, method)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ticket"]["doc_number"] == "A-000001"
    assert [str(t) for t in drawer.opens] == [terminal]  # la apertura llega DESPUÉS del commit


# ---------------------------------------------------------------------------
# 2 · Tarjeta (opens_drawer=False) → el cajón no se toca
# ---------------------------------------------------------------------------
def test_la_tarjeta_no_abre_el_cajon(db, client, drawer):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")

    resp = _sale_and_close(client, h, db, terminal, method)
    assert resp.status_code == 200, resp.text
    assert drawer.opens == []


# ---------------------------------------------------------------------------
# 3 · Cajón atascado: el fallo es del hardware, la venta SIGUE cobrada
# ---------------------------------------------------------------------------
def test_cajon_atascado_no_revienta_la_venta(db, client, app, drawer):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    stuck = FailingCashDrawerAdapter("El cajón no responde")
    app.state.hardware.drawer = stuck
    try:
        resp = _sale_and_close(client, h, db, terminal, method)
    finally:
        app.state.hardware.drawer = drawer

    assert resp.status_code == 200, resp.text  # el cobro sobrevive al periférico
    closed = resp.json()
    assert closed["ticket"]["doc_number"] == "A-000001"  # y su ticket existe
    assert closed["payments"][0]["amount"] == "3.00"
    assert stuck.opens == []  # el kick falló antes de abrir

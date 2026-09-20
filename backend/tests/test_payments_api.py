"""Tests E2E de pagos (fase 07) contra PostgreSQL real.

Cubren los casos de FASE_07: pago exacto, pago superior con cambio, cobro
parcial rechazado (422 ``PAYMENT_INSUFFICIENT`` y rollback total), pago mixto,
pago duplicado (409 ``SALE_ALREADY_PAID``), pago concurrente (``FOR UPDATE``:
un ganador), exceso sin efectivo (422 ``PAYMENT_EXCESS``) y devolución con sus
pagos. Además el CRUD de formas de pago (``admin.parameters``) y los permisos
``payments.take``/``payments.refund``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías. El dinero viaja SIEMPRE como string en el JSON (§3).
"""

import os
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
CAT = f"{V1}/catalog"
SALES = f"{V1}/sales"
ADMIN = f"{V1}/admin"
AUTH = f"{V1}/auth"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

PAY_PERMS = ("products.view", "products.edit", "sales.sell",
             "payments.take", "payments.refund")


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


# ---------------------------------------------------------------------------
# Fábricas mínimas (mismo patrón que test_sales_api.py)
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


def _mk_user(db, role_id, username="jefe", password=CLAVE) -> int:
    return db.execute(
        text(
            "INSERT INTO users (username, password_hash, full_name, role_id) "
            "VALUES (:u, :ph, :fn, :r) RETURNING id"
        ),
        {"u": username, "ph": hash_password(password), "fn": "Usuario de Prueba", "r": role_id},
    ).scalar_one()


def _login(client, username="jefe", password=CLAVE):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_terminal(db, code="T1") -> str:
    return str(db.execute(
        text("INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": f"Terminal {code}"},
    ).scalar_one())


def _mk_cash_session(db, terminal_id, user_id) -> str:
    return str(db.execute(
        text(
            "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount) "
            "VALUES (:t, :u, 50.00) RETURNING id"
        ),
        {"t": terminal_id, "u": user_id},
    ).scalar_one())


def _mk_payment_method(db, code="CASH", name="Efectivo", kind="cash") -> str:
    return str(db.execute(
        text(
            "INSERT INTO payment_methods (code, name, kind, opens_drawer) "
            "VALUES (:c, :n, :k, :o) RETURNING id"
        ),
        {"c": code, "n": name, "k": kind, "o": kind == "cash"},
    ).scalar_one())


def _sales_headers(db, client, perms=PAY_PERMS, username="jefe") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _tax_and_product(client, h, *, name="Café solo", price="1.50") -> tuple[dict, dict]:
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{CAT}/products", headers=h, json={
        "name": name, "tax_rate_id": tax["id"], "price": price,
    }).json()
    return tax, product


def _mk_order(client, h, terminal_id) -> dict:
    resp = client.post(f"{SALES}/orders", headers=h, json={"terminal_id": terminal_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_line(client, h, order_id, payload) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/lines", headers=h, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pay(method_id: str, amount: str, tendered: str | None = None) -> dict:
    payment = {"payment_method_id": method_id, "amount": amount}
    if tendered is not None:
        payment["tendered"] = tendered
    return payment


def _close(client, h, order_id, cash_session_id, payments) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _escenario(db, client, h, *, quantity="2"):
    """Terminal + caja abierta + método CASH + venta con una línea de 1.50.

    Devuelve (terminal, cash_session, método, orden, línea): con quantity=2 el
    total a cobrar es 3.00.
    """

    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)
    order = _mk_order(client, h, terminal)
    line = _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": quantity})
    return terminal, cash, cash_method, order, line


# ---------------------------------------------------------------------------
# 1 · Formas de pago configurables (CRUD con admin.parameters)
# ---------------------------------------------------------------------------
def test_formas_de_pago_configurables(db, client):
    h = _sales_headers(db, client, perms=("products.view", "admin.parameters"),
                       username="gestor")
    seller = _sales_headers(db, client, perms=("sales.sell",), username="vendedor")

    created = client.post(f"{ADMIN}/payment-methods", headers=h, json={
        "code": "VOUCHER", "name": "Vale de comida", "kind": "voucher",
        "opens_drawer": False, "sort_order": 5,
    })
    assert created.status_code == 201, created.text
    method = created.json()
    assert (method["code"], method["name"], method["kind"]) == ("VOUCHER", "Vale de comida", "voucher")
    assert (method["opens_drawer"], method["sort_order"], method["active"]) == (False, 5, True)

    # Código duplicado → 409.
    dup = client.post(f"{ADMIN}/payment-methods", headers=h, json={
        "code": "VOUCHER", "name": "Otro vale", "kind": "voucher"})
    assert dup.status_code == 409 and dup.json()["code"] == "CONFLICT"

    patched = client.patch(f"{ADMIN}/payment-methods/{method['id']}", headers=h,
                           json={"name": "Vale restaurante", "opens_drawer": True})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Vale restaurante"
    assert (patched.json()["code"], patched.json()["opens_drawer"]) == ("VOUCHER", True)

    # Lectura: es operación de venta (sales.sell), sin admin.parameters.
    listed = client.get(f"{ADMIN}/payment-methods", headers=seller)
    assert listed.status_code == 200
    assert [m["code"] for m in listed.json()["items"]] == ["VOUCHER"]

    # Baja lógica: fuera del listado activo, visible con include_inactive.
    # (las lecturas van con «seller»: leer exige sales.sell, y gestor no lo tiene)
    assert client.delete(f"{ADMIN}/payment-methods/{method['id']}", headers=h).status_code == 204
    assert client.get(f"{ADMIN}/payment-methods", headers=seller).json()["items"] == []
    inactive = client.get(f"{ADMIN}/payment-methods", headers=seller,
                          params={"include_inactive": "true"}).json()["items"]
    assert [m["code"] for m in inactive] == ["VOUCHER"] and inactive[0]["active"] is False
    again = client.delete(f"{ADMIN}/payment-methods/{method['id']}", headers=h)
    assert again.status_code == 409

    # Escritura exige admin.parameters; lectura anónima, 401.
    denied = client.post(f"{ADMIN}/payment-methods", headers=seller, json={
        "code": "X", "name": "X", "kind": "other"})
    assert denied.status_code == 403 and denied.json()["code"] == "PERMISSION_DENIED"
    assert client.get(f"{ADMIN}/payment-methods").status_code == 401

    actions = db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'payments.%' ORDER BY id")
    ).scalars().all()
    assert actions == [
        "payments.method_created", "payments.method_updated", "payments.method_deactivated",
    ]


# ---------------------------------------------------------------------------
# 2 · Pago exacto
# ---------------------------------------------------------------------------
def test_pago_exacto(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)

    closed = _close(client, h, order["id"], cash, [_pay(cash_method, "3.00")])
    assert closed["status"] == "paid"
    assert closed["total_amount"] == "3.00" and closed["change_total"] == "0.00"
    [payment] = closed["payments"]
    assert (payment["code"], payment["kind"], payment["amount"],
            payment["status"], payment["external_ref"]) == (
        "CASH", "cash", "3.00", "confirmed", None,
    )

    row = db.execute(text("SELECT status, amount FROM payments")).one()
    assert row[0] == "confirmed" and format(row[1], "f") == "3.00"


# ---------------------------------------------------------------------------
# 3 · Pago superior: cambio a devolver (congelado en el evento, no en la tabla)
# ---------------------------------------------------------------------------
def test_pago_superior_devuelve_cambio(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)

    closed = _close(client, h, order["id"], cash, [_pay(cash_method, "3.00", "10.00")])
    assert closed["total_amount"] == "3.00"   # el total no se infla con lo entregado
    assert closed["change_total"] == "7.00"
    [payment] = closed["payments"]
    assert payment["amount"] == "3.00"

    payload = db.execute(text("SELECT payload FROM sale_events")).scalar_one()
    assert payload["change_total"] == "7.00"
    assert payload["payments"][0]["tendered"] == "10.00"
    assert payload["payments"][0]["change"] == "7.00"
    columns = db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'payments'")
    ).scalars().all()
    assert "tendered" not in columns and "change" not in columns


# ---------------------------------------------------------------------------
# 4 · Cobro parcial: rechazado y SIN efectos (el backend no fía la venta)
# ---------------------------------------------------------------------------
def test_pago_parcial_rechazado_y_sin_efectos(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)

    partial = client.post(f"{SALES}/orders/{order['id']}/close",
                          headers={**h, "Idempotency-Key": str(uuid4())},
                          json={"cash_session_id": cash,
                                "payments": [_pay(cash_method, "1.00")]})
    assert partial.status_code == 422
    assert partial.json()["code"] == "PAYMENT_INSUFFICIENT"

    status = db.execute(text("SELECT status, total_amount FROM orders")).one()
    assert status[0] == "draft" and status[1] is None
    assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM sale_events")).scalar_one() == 0


# ---------------------------------------------------------------------------
# 5 · Pago mixto: efectivo (con cambio) + tarjeta, suma exacta
# ---------------------------------------------------------------------------
def test_pago_mixto(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)
    card_method = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")

    closed = _close(client, h, order["id"], cash,
                    [_pay(cash_method, "1.00", "2.00"), _pay(card_method, "2.00")])
    assert closed["status"] == "paid" and closed["total_amount"] == "3.00"
    assert closed["change_total"] == "1.00"
    assert [(p["code"], p["amount"]) for p in closed["payments"]] == [
        ("CASH", "1.00"), ("CARD", "2.00"),
    ]
    kinds = db.execute(
        text("SELECT count(*) FROM payments")
    ).scalar_one()
    assert kinds == 2


# ---------------------------------------------------------------------------
# 6 · Exceso sin efectivo que lo absorba: 422 PAYMENT_EXCESS
# ---------------------------------------------------------------------------
def test_exceso_sin_efectivo_rechazado(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)
    card_method = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")

    over = client.post(f"{SALES}/orders/{order['id']}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash,
                             "payments": [_pay(card_method, "2.00"),
                                          _pay(cash_method, "2.00")]})
    assert over.status_code == 422 and over.json()["code"] == "PAYMENT_EXCESS"
    assert db.execute(text("SELECT status FROM orders")).scalar_one() == "draft"


# ---------------------------------------------------------------------------
# 7 · «Entregado» inválido: en tarjeta o menor que el importe → 422
# ---------------------------------------------------------------------------
def test_entregado_invalido_rechazado(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)
    card_method = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")

    # Cambio en tarjeta: no existe (el exceso debe ir como efectivo entregado).
    on_card = client.post(f"{SALES}/orders/{order['id']}/close",
                          headers={**h, "Idempotency-Key": str(uuid4())},
                          json={"cash_session_id": cash,
                                "payments": [_pay(card_method, "3.00", "5.00")]})
    assert on_card.status_code == 422 and on_card.json()["code"] == "VALIDATION_ERROR"

    # Entregado menor que el importe: ni cubre ni hay cambio.
    short = client.post(f"{SALES}/orders/{order['id']}/close",
                        headers={**h, "Idempotency-Key": str(uuid4())},
                        json={"cash_session_id": cash,
                              "payments": [_pay(cash_method, "3.00", "2.00")]})
    assert short.status_code == 422 and short.json()["code"] == "VALIDATION_ERROR"

    assert db.execute(text("SELECT status FROM orders")).scalar_one() == "draft"
    assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 0


# ---------------------------------------------------------------------------
# 8 · Forma de pago desconocida (404) o inactiva (409)
# ---------------------------------------------------------------------------
def test_forma_de_pago_desconocida_o_inactiva(db, client):
    h = _sales_headers(db, client)
    _, cash, _, order, _ = _escenario(db, client, h)

    missing = client.post(f"{SALES}/orders/{order['id']}/close",
                          headers={**h, "Idempotency-Key": str(uuid4())},
                          json={"cash_session_id": cash,
                                "payments": [_pay(str(uuid4()), "3.00")]})
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"

    method_id = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")
    db.execute(text("UPDATE payment_methods SET active = false WHERE id = :i"),
               {"i": method_id})
    db.commit()
    inactive = client.post(f"{SALES}/orders/{order['id']}/close",
                           headers={**h, "Idempotency-Key": str(uuid4())},
                           json={"cash_session_id": cash,
                                 "payments": [_pay(method_id, "3.00")]})
    assert inactive.status_code == 409 and inactive.json()["code"] == "CONFLICT"


# ---------------------------------------------------------------------------
# 9 · Pago duplicado: cobrar dos veces la misma venta → 409
# ---------------------------------------------------------------------------
def test_pago_duplicado_rechazado(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)
    _close(client, h, order["id"], cash, [_pay(cash_method, "3.00")])

    dup = client.post(f"{SALES}/orders/{order['id']}/close",
                      headers={**h, "Idempotency-Key": str(uuid4())},
                      json={"cash_session_id": cash,
                            "payments": [_pay(cash_method, "3.00")]})
    assert dup.status_code == 409 and dup.json()["code"] == "SALE_ALREADY_PAID"
    assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 1


# ---------------------------------------------------------------------------
# 10 · Pago concurrente: FOR UPDATE serializa, un solo ganador
# ---------------------------------------------------------------------------
def test_pago_concurrente_un_solo_ganador(db, client, app):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, _ = _escenario(db, client, h)
    payload = {"cash_session_id": cash, "payments": [_pay(cash_method, "3.00")]}

    outcomes: list[int] = []

    def _attempt() -> None:
        # Sin context manager: un event loop por petición (db_null_pool).
        probe = TestClient(app, raise_server_exceptions=False)
        outcomes.append(probe.post(
            f"{SALES}/orders/{order['id']}/close",
            headers={**h, "Idempotency-Key": str(uuid4())}, json=payload,
        ).status_code)

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [200, 409]
    assert db.execute(
        text("SELECT count(*) FROM payments WHERE order_id = :o"), {"o": order["id"]},
    ).scalar_one() == 1
    assert db.execute(
        text("SELECT count(*) FROM sale_events WHERE order_id = :o"), {"o": order["id"]},
    ).scalar_one() == 1


# ---------------------------------------------------------------------------
# 11 · Devolución con pagos: suma exacta, sin «entregado» ni cambio
# ---------------------------------------------------------------------------
def test_devolucion_con_pagos(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, line = _escenario(db, client, h)
    card_method = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")
    _close(client, h, order["id"], cash,
           [_pay(cash_method, "1.00", "2.00"), _pay(card_method, "2.00")])

    refund = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "Cambio de opinión",
        "lines": [{"line_id": line["id"], "quantity": "2"}],
        "payments": [_pay(cash_method, "1.00"), _pay(card_method, "2.00")],
    })
    assert refund.status_code == 201, refund.text
    assert refund.json()["total_amount"] == "-3.00"

    # Lo devuelto queda como pagos positivos en la orden negativa.
    # (sin orden: payments.id es UUID, no guarda orden de inserción)
    returned = db.execute(
        text(
            "SELECT p.amount FROM payments p JOIN orders o ON o.id = p.order_id "
            "WHERE o.total_amount < 0"
        )
    ).scalars().all()
    assert sorted(format(a, "f") for a in returned) == ["1.00", "2.00"]


def test_devolucion_rechaza_desajuste_y_entregado(db, client):
    h = _sales_headers(db, client)
    _, cash, cash_method, order, line = _escenario(db, client, h)
    _close(client, h, order["id"], cash, [_pay(cash_method, "3.00")])

    # Devolver 3.00 «pagando» solo 1.00: 422 y sin fila en refunds.
    mismatch = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": line["id"], "quantity": "2"}],
        "payments": [_pay(cash_method, "1.00")],
    })
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "PAYMENT_INSUFFICIENT"
    assert db.execute(text("SELECT count(*) FROM refunds")).scalar_one() == 0

    # El cambio no aplica en una devolución: nada de «entregado».
    tendered = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": line["id"], "quantity": "2"}],
        "payments": [_pay(cash_method, "3.00", "5.00")],
    })
    assert tendered.status_code == 422 and tendered.json()["code"] == "VALIDATION_ERROR"

    # Con la suma exacta sí procede y deja la devolución registrada.
    ok = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "Avería",
        "lines": [{"line_id": line["id"], "quantity": "2"}],
        "payments": [_pay(cash_method, "3.00")],
    })
    assert ok.status_code == 201, ok.text
    assert db.execute(text("SELECT count(*) FROM refunds")).scalar_one() == 1


# ---------------------------------------------------------------------------
# 12 · Permisos: cobrar y devolver exigen payments.take / payments.refund
# ---------------------------------------------------------------------------
def test_permisos_de_cobro(db, client):
    seller = _sales_headers(db, client,
                            perms=("products.view", "products.edit", "sales.sell"),
                            username="vendedor")
    user_id = db.execute(
        text("SELECT id FROM users WHERE username = 'vendedor'")
    ).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, seller)
    order = _mk_order(client, seller, terminal)
    line = _add_line(client, seller, order["id"], {"product_id": cafe["id"], "quantity": "1"})

    denied_close = client.post(f"{SALES}/orders/{order['id']}/close", headers=seller, json={
        "cash_session_id": cash, "payments": [_pay(cash_method, "1.50")]})
    assert denied_close.status_code == 403
    assert denied_close.json()["code"] == "PERMISSION_DENIED"

    denied_refund = client.post(f"{SALES}/orders/{order['id']}/refund", headers=seller, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert denied_refund.status_code == 403

    assert client.get(f"{SALES}/orders").status_code == 401

"""Tests E2E del motor de ventas (fases 06-07) contra PostgreSQL real.

Cubren el ciclo completo de venta (borrador → líneas → cobro con pagos), la
edición de líneas (cantidad, descuento con permiso, notas, quitar), la
anulación con motivo, la devolución como orden negativa enlazada (con sus
pagos), los eventos genéricos de venta (``sale_events``) y los permisos
``sales.*``/``payments.*``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías. El dinero viaja SIEMPRE como string en el JSON (§3).
"""

import os
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
AUTH = f"{V1}/auth"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

ALL_SALES = ("products.view", "products.edit", "sales.sell", "sales.void",
             "sales.refund", "orders.discount", "payments.take", "payments.refund")


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
# Fábricas mínimas (mismo patrón que test_panels_api.py)
# ---------------------------------------------------------------------------
def _mk_role(db, code: str, permissions: tuple[str, ...] = ()) -> int:
    role_id = db.execute(
        text("INSERT INTO roles (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": code},
    ).scalar_one()
    for perm in permissions:
        # ON CONFLICT: dos roles de prueba pueden compartir permiso.
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


def _mk_cash_session(db, terminal_id, user_id, *, closed=False) -> str:
    params = {"t": terminal_id, "u": user_id}
    sql = "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount) "
    sql += "VALUES (:t, :u, 100.00) "
    if closed:
        # Cerrada con cuadre exacto (CHECKs: close_complete y closed_at > opened_at).
        sql = (
            "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount, "
            "closed_at, expected_amount, counted_amount, difference) "
            "VALUES (:t, :u, 50.00, NOW() + interval '1 second', 40.00, 40.00, 0.00) "
        )
    return str(db.execute(
        text(sql + "RETURNING id"), params
    ).scalar_one())


def _mk_table(db) -> str:
    zone_id = db.execute(
        text("INSERT INTO zones (name) VALUES ('Sala') RETURNING id")
    ).scalar_one()
    return str(db.execute(
        text("INSERT INTO dining_tables (zone_id, name, seats) VALUES (:z, 'M1', 4) RETURNING id"),
        {"z": zone_id},
    ).scalar_one())


def _sales_headers(db, client, perms=ALL_SALES, username="jefe") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _tax_and_product(client, h, *, name="Café solo", price="1.50", rate="21.00") -> tuple[dict, dict]:
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": rate, "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{CAT}/products", headers=h, json={
        "name": name, "tax_rate_id": tax["id"], "price": price,
    }).json()
    return tax, product


def _mk_order(client, h, terminal_id, **extra) -> dict:
    payload = {"terminal_id": terminal_id}
    payload.update(extra)
    resp = client.post(f"{SALES}/orders", headers=h, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_line(client, h, order_id, payload) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/lines", headers=h, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mk_payment_method(db, code="CASH", name="Efectivo", kind="cash") -> str:
    return str(db.execute(
        text(
            "INSERT INTO payment_methods (code, name, kind, opens_drawer) "
            "VALUES (:c, :n, :k, :o) RETURNING id"
        ),
        {"c": code, "n": name, "k": kind, "o": kind == "cash"},
    ).scalar_one())


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


def _sales_actions(db) -> list[str]:
    return db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'sales.%' ORDER BY id")
    ).scalars().all()


def _sale_events(db, order_id=None) -> list[tuple[str, dict]]:
    sql = "SELECT event_type, payload FROM sale_events"
    params: dict = {}
    if order_id is not None:
        sql += " WHERE order_id = :o"
        params["o"] = order_id
    sql += " ORDER BY created_at"
    rows = db.execute(text(sql), params).all()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------------------
# 1 · Ciclo completo: borrador → líneas → cobro, con eventos y auditoría
# ---------------------------------------------------------------------------
def test_ciclo_completo_de_venta(db, client):
    h = _sales_headers(db, client)
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)

    order = _mk_order(client, h, terminal)
    assert order["status"] == "draft"
    assert order["total_amount"] is None  # draft sin totales (ck_orders_draft_no_totals)

    # Línea de catálogo: snapshot 2 × 1.50 con IVA 21 % incluido.
    line = _add_line(client, h, order["id"], {
        "product_id": cafe["id"], "quantity": "2",
    })
    assert line["name"] == "Café solo"
    assert line["unit_price"] == "1.50" and line["tax_rate"] == "21.00"
    assert line["quantity"] == "2.000"
    assert (line["base"], line["total"]) == ("2.48", "3.00")  # 3.00 / 1.21 = 2.4793… → 2.48

    # Artículo libre con descuento: 10.00 al 10 % → 9.00 con IVA incluido
    # (base 9.00 / 1.10 = 8.1818… → 8.18, cuota 0.82).
    free = _add_line(client, h, order["id"], {
        "name": "Copa aparte", "unit_price": "10.00", "tax_rate": "10.00",
        "quantity": "1", "discount_pct": "10.00",
    })
    assert (free["base"], free["total"]) == ("8.18", "9.00")

    # Cambiar cantidad de la línea de catálogo (3 × 1.50 → 4.50 / base 3.72).
    patched = client.patch(f"{SALES}/orders/{order['id']}/lines/{line['id']}",
                           headers=h, json={"quantity": "3"})
    assert patched.status_code == 200
    assert (patched.json()["base"], patched.json()["total"]) == ("3.72", "4.50")

    # Quitar la línea libre.
    removed = client.delete(f"{SALES}/orders/{order['id']}/lines/{free['id']}", headers=h)
    assert removed.status_code == 204

    detail = client.get(f"{SALES}/orders/{order['id']}", headers=h).json()
    assert [l["id"] for l in detail["lines"]] == [line["id"]]

    closed = _close(client, h, order["id"], cash, [_pay(cash_method, "4.50")])
    assert closed["status"] == "paid"
    assert closed["cash_session_id"] == cash
    # 4.50 (base 3.72 + 0.78) + 0 €: el artículo libre se quitó antes de cobrar.
    assert (closed["total_base"], closed["total_tax"], closed["total_amount"]) == (
        "3.72", "0.78", "4.50",
    )
    summary = closed["tax_summary"]
    assert summary["slices"] == [{
        "rate_bp": 2100, "base": "3.72", "tax": "0.78", "total": "4.50",
    }]
    assert closed["paid_at"] is not None

    # El cobro: un pago de efectivo exacto, sin cambio (fase 07).
    assert closed["change_total"] == "0.00"
    [payment] = closed["payments"]
    assert (payment["code"], payment["kind"], payment["amount"],
            payment["status"], payment["external_ref"]) == (
        "CASH", "cash", "4.50", "confirmed", None,
    )

    # Recuperar la venta cobrada por listado y por detalle.
    listed = client.get(f"{SALES}/orders", headers=h, params={"status": "paid"}).json()
    assert [o["id"] for o in listed["items"]] == [order["id"]]
    assert client.get(f"{SALES}/orders", headers=h,
                      params={"status": "draft"}).json()["items"] == []

    # Evento genérico de venta para el futuro FiscalAdapter: dinero como string.
    events = _sale_events(db)
    assert [t for t, _ in events] == ["sale_closed"]
    payload = events[0][1]
    assert payload["totals"] == summary
    assert payload["lines"][0]["unit_price"] == "1.50"
    assert payload["change_total"] == "0.00"
    assert payload["payments"] == [{
        "payment_method_id": cash_method, "code": "CASH", "kind": "cash",
        "amount": "4.50", "tendered": None, "change": "0.00",
    }]

    assert _sales_actions(db) == [
        "sales.order_created", "sales.line_added", "sales.line_added",
        "sales.line_updated", "sales.line_removed", "sales.order_closed",
    ]


# ---------------------------------------------------------------------------
# 2 · Guardas: un cobrado ya no se edita, cierre sin líneas y sin caja válida
# ---------------------------------------------------------------------------
def test_guardas_de_cierre(db, client):
    h = _sales_headers(db, client)
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    other_terminal = _mk_terminal(db, code="T2")
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    closed_cash = _mk_cash_session(db, terminal, str(user_id), closed=True)
    _, cafe = _tax_and_product(client, h)

    order = _mk_order(client, h, terminal)

    empty = client.post(f"{SALES}/orders/{order['id']}/close",
                        headers={**h, "Idempotency-Key": str(uuid4())},
                        json={"cash_session_id": cash,
                              "payments": [_pay(cash_method, "10.00")]})
    assert empty.status_code == 409 and empty.json()["code"] == "CONFLICT"

    _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": "1"})

    # Sin pagos no hay cobro (fase 07): 422 del contrato.
    none = client.post(f"{SALES}/orders/{order['id']}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash, "payments": []})
    assert none.status_code == 422 and none.json()["code"] == "VALIDATION_ERROR"

    assert client.post(f"{SALES}/orders/{order['id']}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": str(uuid4()),
                             "payments": [_pay(cash_method, "1.50")]}).status_code == 404
    assert client.post(f"{SALES}/orders/{order['id']}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": closed_cash,
                             "payments": [_pay(cash_method, "1.50")]}).status_code == 409
    cross = client.post(f"{SALES}/orders/{order['id']}/close",
                        headers={**h, "Idempotency-Key": str(uuid4())},
                        json={"cash_session_id": _mk_cash_session(db, other_terminal, str(user_id)),
                              "payments": [_pay(cash_method, "1.50")]})
    assert cross.status_code == 409

    _close(client, h, order["id"], cash, [_pay(cash_method, "1.50")])

    again = client.post(f"{SALES}/orders/{order['id']}/close",
                        headers={**h, "Idempotency-Key": str(uuid4())},
                        json={"cash_session_id": cash,
                              "payments": [_pay(cash_method, "1.50")]})
    assert again.status_code == 409 and again.json()["code"] == "SALE_ALREADY_PAID"

    edit_paid = client.post(f"{SALES}/orders/{order['id']}/lines", headers=h,
                            json={"product_id": cafe["id"], "quantity": "1"})
    assert edit_paid.status_code == 409
    assert edit_paid.json()["code"] == "SALE_ALREADY_PAID"


# ---------------------------------------------------------------------------
# 3 · Cancelar un borrador: anulación con motivo, sin totales
# ---------------------------------------------------------------------------
def test_anular_borrador_y_borrador_cobrado(db, client):
    h = _sales_headers(db, client)
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)

    draft = _mk_order(client, h, terminal)
    _add_line(client, h, draft["id"], {"product_id": cafe["id"], "quantity": "1"})

    voided = client.post(f"{SALES}/orders/{draft['id']}/void", headers=h,
                         json={"reason": "Cliente se fue"})
    assert voided.status_code == 200
    body = voided.json()
    assert body["status"] == "voided" and body["void_reason"] == "Cliente se fue"
    assert body["total_amount"] is None  # el draft nunca tuvo totales
    assert body["voided_at"] is not None

    again = client.post(f"{SALES}/orders/{draft['id']}/void", headers=h,
                        json={"reason": "Otra vez"})
    assert again.status_code == 409 and again.json()["code"] == "CONFLICT"

    # Un cobrado también se anula, conservando sus totales (nunca se borra).
    paid = _mk_order(client, h, terminal)
    _add_line(client, h, paid["id"], {"product_id": cafe["id"], "quantity": "2"})
    _close(client, h, paid["id"], cash, [_pay(cash_method, "3.00")])
    voided_paid = client.post(f"{SALES}/orders/{paid['id']}/void", headers=h,
                              json={"reason": "Error de caja"}).json()
    assert voided_paid["status"] == "voided"
    assert voided_paid["total_amount"] == "3.00"  # totales conservados

    # Eventos de la ORDEN cobrada (el void del draft anterior no pinta aquí).
    event_types = [t for t, _ in _sale_events(db, paid["id"])]
    assert event_types == ["sale_closed", "sale_voided"]
    assert "sales.order_voided" in _sales_actions(db)


# ---------------------------------------------------------------------------
# 4 · Devolución: orden negativa enlazada, una sola por venta
# ---------------------------------------------------------------------------
def test_devolucion(db, client):
    h = _sales_headers(db, client)
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)

    order = _mk_order(client, h, terminal)
    line = _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": "2"})
    _close(client, h, order["id"], cash, [_pay(cash_method, "3.00")])

    # Más de lo vendido → 422.
    over = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "Producto en mal estado",
        "lines": [{"line_id": line["id"], "quantity": "3"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert over.status_code == 422 and over.json()["code"] == "VALIDATION_ERROR"

    # Línea ajena → 422.
    alien = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": str(uuid4()), "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert alien.status_code == 422

    # Devolución parcial: 1 de las 2 unidades.
    refund = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "Producto en mal estado",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert refund.status_code == 201, refund.text
    body = refund.json()
    assert body["status"] == "paid" and body["dining_table_id"] is None
    assert body["total_amount"] == "-1.50"  # orden negativa exacta
    assert body["total_base"] == "-1.24" and body["total_tax"] == "-0.26"

    # El dinero devuelto: pago positivo en la orden negativa (fase 07).
    amounts = db.execute(
        text("SELECT o.status, p.amount FROM payments p JOIN orders o ON o.id = p.order_id")
    ).all()
    assert sorted((r[0], format(r[1], "f")) for r in amounts) == [
        ("paid", "1.50"), ("paid", "3.00"),
    ]

    row = db.execute(
        text("SELECT original_order_id, refund_order_id, amount, reason FROM refunds")
    ).one()
    assert str(row[0]) == order["id"] and str(row[1]) == body["id"]
    assert format(row[2], "f") == "1.50" and row[3] == "Producto en mal estado"

    # Segunda devolución de la misma venta → 409 (UNIQUE en refunds).
    second = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "Reintento",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert second.status_code == 409 and second.json()["code"] == "CONFLICT"

    # Devolución de un borrador y de una anulada → 409.
    draft = _mk_order(client, h, terminal)
    assert client.post(f"{SALES}/orders/{draft['id']}/refund", headers=h, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    }).status_code == 409

    types = [t for t, _ in _sale_events(db)]
    assert types == ["sale_closed", "refund_issued"]
    refund_payload = _sale_events(db)[1][1]
    assert refund_payload["original_order_id"] == order["id"]
    assert refund_payload["totals"]["total"] == "-1.50"
    # Devolución sin cambio: importe devuelto, sin «entregado».
    assert refund_payload["payments"] == [{
        "payment_method_id": cash_method, "code": "CASH", "kind": "cash",
        "amount": "1.50", "tendered": None, "change": "0.00",
    }]
    assert "sales.refund_issued" in _sales_actions(db)


# ---------------------------------------------------------------------------
# 5 · Descuento: exige orders.discount (comprobación dinámica)
# ---------------------------------------------------------------------------
def test_descuento_requiere_permiso(db, client):
    h = _sales_headers(db, client, perms=("products.view", "products.edit", "sales.sell"),
                       username="vendedor")
    user_id = db.execute(
        text("SELECT id FROM users WHERE username = 'vendedor'")
    ).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    _, cafe = _tax_and_product(client, h)

    order = _mk_order(client, h, terminal)

    denied = client.post(f"{SALES}/orders/{order['id']}/lines", headers=h, json={
        "product_id": cafe["id"], "quantity": "1", "discount_pct": "5.00",
    })
    assert denied.status_code == 403 and denied.json()["code"] == "PERMISSION_DENIED"

    # Sin descuento el vendedor puede vender.
    line = _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": "2"})

    denied_patch = client.patch(
        f"{SALES}/orders/{order['id']}/lines/{line['id']}", headers=h,
        json={"discount_pct": "10.00"},
    )
    assert denied_patch.status_code == 403

    # Cambiar notas sin tocar el descuento sí está permitido.
    notes = client.patch(f"{SALES}/orders/{order['id']}/lines/{line['id']}",
                         headers=h, json={"notes": "sin hielo"})
    assert notes.status_code == 200 and notes.json()["notes"] == "sin hielo"

    # Con el permiso, el descuento cambia los importes: 2 × 1.50 − 50 % = 1.50.
    boss = _sales_headers(db, client, perms=ALL_SALES, username="jefe")
    discounted = client.patch(
        f"{SALES}/orders/{order['id']}/lines/{line['id']}", headers=boss,
        json={"discount_pct": "50.00"},
    )
    assert discounted.status_code == 200
    assert (discounted.json()["base"], discounted.json()["total"]) == ("1.24", "1.50")


# ---------------------------------------------------------------------------
# 6 · Mesa: una sola comanda abierta por mesa
# ---------------------------------------------------------------------------
def test_una_comanda_abierta_por_mesa(db, client):
    h = _sales_headers(db, client)
    terminal = _mk_terminal(db)
    table = _mk_table(db)

    first = _mk_order(client, h, terminal, dining_table_id=table, order_type="restaurant",
                      guest_count=4)
    assert first["dining_table_id"] == table

    clash = client.post(f"{SALES}/orders", headers=h,
                        json={"terminal_id": terminal, "dining_table_id": table})
    assert clash.status_code == 409 and clash.json()["code"] == "CONFLICT"

    # Cerrada la primera, la mesa queda libre.
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)
    _add_line(client, h, first["id"], {"product_id": cafe["id"], "quantity": "1"})
    _close(client, h, first["id"], cash, [_pay(cash_method, "1.50")])

    second = client.post(f"{SALES}/orders", headers=h,
                         json={"terminal_id": terminal, "dining_table_id": table})
    assert second.status_code == 201


# ---------------------------------------------------------------------------
# 7 · Permisos: vender no es anular ni devolver
# ---------------------------------------------------------------------------
def test_permisos_de_venta(db, client):
    # Vende (y monta el catálogo) pero no cobra, anula ni devuelve.
    seller = _sales_headers(db, client,
                            perms=("products.view", "products.edit", "sales.sell"),
                            username="vendedor")
    user_id = db.execute(
        text("SELECT id FROM users WHERE username = 'vendedor'")
    ).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, seller, name="Café", price="1.00")

    order = _mk_order(client, seller, terminal)
    _add_line(client, seller, order["id"], {"product_id": cafe["id"], "quantity": "1"})

    # Cobrar exige payments.take (el vendedor no lo tiene).
    denied_close = client.post(f"{SALES}/orders/{order['id']}/close", headers=seller, json={
        "cash_session_id": cash, "payments": [_pay(cash_method, "1.00")]})
    assert denied_close.status_code == 403
    assert denied_close.json()["code"] == "PERMISSION_DENIED"

    denied_void = client.post(f"{SALES}/orders/{order['id']}/void", headers=seller,
                              json={"reason": "porque sí"})
    assert denied_void.status_code == 403
    assert denied_void.json()["code"] == "PERMISSION_DENIED"

    denied_refund = client.post(f"{SALES}/orders/{order['id']}/refund", headers=seller, json={
        "cash_session_id": cash, "reason": "x",
        "lines": [{"line_id": str(uuid4()), "quantity": "1"}],
        "payments": [_pay(cash_method, "1.00")],
    })
    assert denied_refund.status_code == 403

    anonymous = client.get(f"{SALES}/orders")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

    # Cantidades imposibles → 422 de validación del contrato.
    assert client.post(f"{SALES}/orders/{order['id']}/lines", headers=seller, json={
        "product_id": cafe["id"], "quantity": "0",
    }).status_code == 422
    assert client.post(f"{SALES}/orders/{order['id']}/lines", headers=seller, json={
        "product_id": cafe["id"], "quantity": "2.0005",
    }).status_code == 422
    # El dinero como float se rechaza (§3).
    assert client.post(f"{SALES}/orders/{order['id']}/lines", headers=seller, json={
        "name": "Libre", "unit_price": 1.5, "tax_rate": "10.00", "quantity": "1",
    }).status_code == 422

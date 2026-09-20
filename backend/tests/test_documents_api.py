"""Tests E2E de documentos (fase 09) contra PostgreSQL real.

Cubren FASE_09: ticket automático en el cobro (numeración segura por
terminal, también bajo concurrencia), ticket negativo de devolución que
referencia al original, factura de 1..N ventas del mismo cliente con NIF
(serie por año), rectificativas parcial y total con importes negativos,
reimpresión con contador, logo de cabecera como fichero en volumen con
snapshot embebido en el payload, datos fiscales en Parámetros, y permisos
``tickets.reprint``/``invoices.issue``/``invoices.void``/``admin.parameters``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte
de tablas vacías. El dinero viaja SIEMPRE como string en el JSON (§3).
"""

import base64
import os
import threading
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
CAT = f"{V1}/catalog"
SALES = f"{V1}/sales"
AUTH = f"{V1}/auth"
CASH = f"{V1}/cash"
DOCS = f"{V1}/documents"
ADMIN = f"{V1}/admin"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

YEAR = date.today().year

# El jefe de sala: vende, cobra, devuelve y además gobierna documentos.
BOSS_PERMS = ("products.view", "products.edit", "sales.sell",
              "payments.take", "payments.refund", "cash.open",
              "tickets.reprint", "invoices.issue", "invoices.void",
              "admin.parameters")
# El camarero reimprime tickets, pero no emite ni anula facturas ni toca
# la configuración del negocio.
WAITER_PERMS = ("products.view", "products.edit", "sales.sell",
                "payments.take", "payments.refund", "cash.open",
                "tickets.reprint")


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
def _data_dir(tmp_path, monkeypatch):
    """Volumen de ficheros (logos) aislado en tmp; el caché de settings se
    limpia al entrar y al salir para no filtrar la ruta a otros tests."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TPV_DATA_DIR", str(data_dir))
    get_settings.cache_clear()
    yield data_dir
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fábricas mínimas (mismo patrón que test_cash_api.py)
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


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


def _mk_customer(db, *, tax_id="B12345678", name="Cliente SL") -> str:
    """No hay API de clientes (fase futura): insert directo."""
    return str(db.execute(
        text("INSERT INTO customers (name, tax_id) VALUES (:n, :t) RETURNING id"),
        {"n": name, "t": tax_id},
    ).scalar_one())


def _headers(db, client, perms=BOSS_PERMS, username="cajero") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _tax_and_product(client, h, *, name="Café solo", price="1.50") -> tuple[dict, dict]:
    # Código único por llamada: un test puede vender varias veces, y el código
    # del tipo de IVA es único (segunda «general» sería 409).
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": f"general-{uuid4().hex[:8]}", "name": "IVA general",
        "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{CAT}/products", headers=h, json={
        "name": name, "tax_rate_id": tax["id"], "price": price,
    }).json()
    return tax, product


def _mk_order(client, h, terminal_id, customer_id=None) -> dict:
    payload: dict = {"terminal_id": terminal_id}
    if customer_id is not None:
        payload["customer_id"] = customer_id
    resp = client.post(f"{SALES}/orders", headers=h, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_line(client, h, order_id, product_id, quantity="2") -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/lines", headers=h,
                       json={"product_id": product_id, "quantity": quantity})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pay(method_id: str, amount: str) -> dict:
    return {"payment_method_id": method_id, "amount": amount}


def _open(client, h, terminal_id, opening="50.00"):
    resp = client.post(f"{CASH}/sessions", headers=h,
                       json={"terminal_id": terminal_id, "opening_amount": opening})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _close_sale(client, h, order_id, cash_session_id, payments) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refund(client, h, order_id, cash_session_id, line, method_id, amount) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/refund", headers=h, json={
        "cash_session_id": cash_session_id, "reason": "Devolución de prueba",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(method_id, amount)],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _ticket_of(db, order_id) -> dict:
    """Ticket de una orden (p. ej. la orden negativa de una devolución, cuyo
    ticket no viaja en la respuesta del refund)."""
    row = db.execute(
        text("SELECT id, series, number, payload FROM tickets WHERE order_id = :o"),
        {"o": order_id},
    ).one()
    return {"id": str(row[0]), "series": row[1], "number": row[2], "payload": row[3]}


def _audit_actions(db, like="documents.%") -> set[str]:
    return {
        row[0] for row in db.execute(
            text("SELECT DISTINCT action FROM audit_log WHERE action LIKE :p"), {"p": like}
        )
    }


def _sale(client, h, db, terminal_id, method_id, *, customer_id=None,
          quantity="2") -> tuple[dict, dict, dict]:
    """Venta cobrada de ``quantity × 1.50``: (orden, línea, respuesta de cobro)."""
    _, product = _tax_and_product(client, h)
    order = _mk_order(client, h, terminal_id, customer_id=customer_id)
    line = _add_line(client, h, order["id"], product["id"], quantity=quantity)
    closed = _close_sale(
        client, h, order["id"],
        cash_session_id=_cash_session_id(client, h, terminal_id),
        payments=[_pay(method_id, format(Decimal("1.50") * Decimal(quantity), "f"))],
    )
    return order, line, closed


_cash_sessions: dict[str, str] = {}


def _cash_session_id(client, h, terminal_id) -> str:
    """Abre (o reutiliza) la sesión de caja abierta del terminal en este test."""
    if terminal_id not in _cash_sessions:
        _cash_sessions[terminal_id] = _open(client, h, terminal_id)["id"]
    return _cash_sessions[terminal_id]


@pytest.fixture(autouse=True)
def _reset_cash_sessions():
    _cash_sessions.clear()
    yield
    _cash_sessions.clear()


# ---------------------------------------------------------------------------
# 1 · El cobro emite el ticket automáticamente (numeración por terminal)
# ---------------------------------------------------------------------------
def test_el_cobro_emite_ticket_automatico(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)

    order, line, closed = _sale(client, h, db, terminal, method)
    # El ticket viaja en la MISMA respuesta del cobro: una venta cobrada
    # sin ticket no existe.
    assert closed["ticket"]["doc_number"] == "A-000001"
    ticket_id = closed["ticket"]["id"]

    detail = client.get(f"{DOCS}/tickets/{ticket_id}", headers=h)
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["order_id"] == order["id"]
    assert payload["series"] == "A" and payload["number"] == 1
    assert payload["reprint_count"] == 0
    body = payload["payload"]
    assert body["kind"] == "ticket"
    assert body["doc_number"] == "A-000001"
    assert body["order"] == {
        "id": order["id"], "type": "sale", "original_order_id": None,
        "original_doc_number": None, "customer_id": None,
    }
    assert [l["name"] for l in body["lines"]] == ["Café solo"]
    assert Decimal(body["totals"]["total"]) == Decimal("3.00")
    assert body["payments"] == [{"code": "CASH", "kind": "cash", "amount": "3.00"}]
    assert Decimal(body["change_total"]) == 0
    # Sin logo ni datos fiscales configurados: cabecera vacía (documento sin él).
    assert body["header"] == {"business": None, "logo": None}

    # Segunda venta en el MISMO terminal: correlativo.
    _, _, closed2 = _sale(client, h, db, terminal, method, quantity="1")
    assert closed2["ticket"]["doc_number"] == "A-000002"

    # Otro terminal: su propia secuencia arranca en 1.
    other = _mk_terminal(db, "T2")
    _, _, closed3 = _sale(client, h, db, other, method, quantity="1")
    assert closed3["ticket"]["doc_number"] == "A-000001"

    # La secuencia de cada terminal quedó en su valor; auditoría emitida.
    assert "documents.ticket_issued" in _audit_actions(db)


# ---------------------------------------------------------------------------
# 2 · Numeración concurrente en el mismo terminal: sin huecos ni duplicados
# ---------------------------------------------------------------------------
def test_cierre_concurrente_emite_numeros_distintos(db, client, app):
    boss = _headers(db, client, username="jefe")
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _, product = _tax_and_product(client, boss)

    cash_id = _cash_session_id(client, boss, terminal)
    orders = []
    for _ in range(2):
        order = _mk_order(client, boss, terminal)
        _add_line(client, boss, order["id"], product["id"], quantity="1")
        orders.append(order["id"])

    results: list[str] = ["", ""]

    def _close(index: int, order_id: str) -> None:
        # Sin context manager: cada hilo crea su event loop por petición.
        client = TestClient(app)
        token = _login(client, "jefe").json()["access_token"]
        resp = client.post(f"{SALES}/orders/{order_id}/close",
                           headers={**_bearer(token), "Idempotency-Key": str(uuid4())},
                           json={"cash_session_id": cash_id,
                                 "payments": [_pay(method, "1.50")]})
        assert resp.status_code == 200, resp.text
        results[index] = resp.json()["ticket"]["doc_number"]

    threads = [threading.Thread(target=_close, args=(i, oid)) for i, oid in enumerate(orders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Dos cobros concurrentes, dos números distintos, sin duplicado.
    assert sorted(results) == ["A-000001", "A-000002"]
    numbers = [row[0] for row in db.execute(text("SELECT number FROM tickets"))]
    assert sorted(numbers) == [1, 2]


# ---------------------------------------------------------------------------
# 3 · Devolución: orden negativa → ticket que referencia al original
# ---------------------------------------------------------------------------
def test_la_devolucion_emite_ticket_negativo_con_referencia(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)

    order, line, closed = _sale(client, h, db, terminal, method)
    assert closed["ticket"]["doc_number"] == "A-000001"

    refund = _refund(client, h, order["id"], _cash_session_id(client, h, terminal),
                     line, method, "1.50")
    ticket = _ticket_of(db, refund["id"])
    payload = ticket["payload"]
    assert payload["kind"] == "ticket"
    assert payload["doc_number"] == "A-000002"  # misma secuencia del terminal
    assert payload["order"]["type"] == "refund"
    assert payload["order"]["original_order_id"] == order["id"]
    assert payload["order"]["original_doc_number"] == "A-000001"
    assert Decimal(payload["totals"]["total"]) == Decimal("-1.50")
    assert payload["lines"][0]["quantity"] == "-1.000"


# ---------------------------------------------------------------------------
# 4 · Factura de 1..N ventas del mismo cliente con NIF (serie por año)
# ---------------------------------------------------------------------------
def test_factura_de_dos_ventas_del_mismo_cliente(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678", name="Cliente SL")

    order1, _, closed1 = _sale(client, h, db, terminal, method, customer_id=customer)
    order2, _, _ = _sale(client, h, db, terminal, method, customer_id=customer)

    resp = client.post(f"{DOCS}/invoices", headers=h,
                       json={"order_ids": [order1["id"], order2["id"]]})
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    assert invoice["series"] == "FAC" and invoice["year"] == YEAR and invoice["number"] == 1
    assert invoice["doc_number"] == f"FAC {YEAR}/000001"
    assert invoice["status"] == "issued"
    assert invoice["customer_id"] == customer
    assert {line["order_id"] for line in invoice["lines"]} == {order1["id"], order2["id"]}
    assert Decimal(invoice["total_amount"]) == Decimal("6.00")
    assert Decimal(invoice["total_base"]) + Decimal(invoice["total_tax"]) == Decimal("6.00")

    body = invoice["payload"]
    assert body["kind"] == "invoice"
    assert body["customer"] == {
        "id": customer, "name": "Cliente SL", "tax_id": "B12345678",
        "address": "", "city": "", "postal_code": "",
    }
    # Fusiona los desgloses de IVA de ambas ventas en uno solo.
    assert body["totals"]["slices"] == [{
        "rate_bp": 2100, "base": "4.96", "tax": "1.04", "total": "6.00",
    }]
    # Cada venta embebida referencia su ticket emitido en el cobro.
    assert [o["doc_number"] for o in body["orders"]] == [
        closed1["ticket"]["doc_number"], "A-000002",
    ]

    # Lectura por id: mismo documento con sus líneas.
    fetched = client.get(f"{DOCS}/invoices/{invoice['id']}", headers=h)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["doc_number"] == f"FAC {YEAR}/000001"

    # Las ventas ya facturadas no se re-facturan (UNIQUE por venta).
    again = client.post(f"{DOCS}/invoices", headers=h,
                        json={"order_ids": [order1["id"]]})
    assert again.status_code == 409, again.text


def test_reglas_de_facturacion_rechazadas(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    with_nif = _mk_customer(db, tax_id="B11111111")
    without_nif = _mk_customer(db, tax_id=None, name="Consumidor final")

    paid_nif, _, _ = _sale(client, h, db, terminal, method, customer_id=with_nif)
    paid_nonif, _, _ = _sale(client, h, db, terminal, method, customer_id=without_nif)
    paid_none, _, _ = _sale(client, h, db, terminal, method)
    draft = _mk_order(client, h, terminal, customer_id=with_nif)
    _add_line(client, h, draft["id"], _tax_and_product(client, h)[1]["id"], quantity="1")

    def _invoice(*order_ids):
        return client.post(f"{DOCS}/invoices", headers=h,
                           json={"order_ids": list(order_ids)})

    # Venta sin cliente asignado → 409.
    assert _invoice(paid_none["id"]).status_code == 409
    # Venta sin cobrar (borrador) → 409.
    assert _invoice(draft["id"]).status_code == 409
    # Cliente sin NIF → 422.
    assert _invoice(paid_nonif["id"]).status_code == 422
    # Clientes distintos en la misma factura → 409.
    assert _invoice(paid_nif["id"], paid_nonif["id"]).status_code == 409
    # Lista vacía o duplicada → 422.
    assert client.post(f"{DOCS}/invoices", headers=h, json={"order_ids": []}).status_code == 422
    duplicate = str(paid_nif["id"])
    assert client.post(f"{DOCS}/invoices", headers=h,
                       json={"order_ids": [duplicate, duplicate]}).status_code == 422
    # Venta inexistente → 404.
    assert _invoice(str(uuid4())).status_code == 404

    # Ninguna factura fue emitida: la secuencia de facturas sigue en cero.
    assert db.execute(text("SELECT count(*) FROM invoices")).scalar_one() == 0


def test_no_se_facturan_devoluciones_ni_ventas_negativas(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678")

    order, line, _ = _sale(client, h, db, terminal, method, customer_id=customer)
    refund = _refund(client, h, order["id"], _cash_session_id(client, h, terminal),
                     line, method, "1.50")

    # Una devolución (orden negativa) no se factura: se rectifica.
    resp = client.post(f"{DOCS}/invoices", headers=h, json={"order_ids": [refund["id"]]})
    assert resp.status_code == 409, resp.text
    assert "rectific" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5 · Rectificativas: parcial (por devolución) y total (importe invertido)
# ---------------------------------------------------------------------------
def _invoiced_sale(db, client, h, terminal, method, customer):
    order, line, closed = _sale(client, h, db, terminal, method, customer_id=customer)
    resp = client.post(f"{DOCS}/invoices", headers=h, json={"order_ids": [order["id"]]})
    assert resp.status_code == 201, resp.text
    return order, line, closed, resp.json()


def test_rectificativa_parcial_por_devolucion(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678")

    order, line, closed, invoice = _invoiced_sale(db, client, h, terminal, method, customer)
    refund = _refund(client, h, order["id"], _cash_session_id(client, h, terminal),
                     line, method, "1.50")

    resp = client.post(f"{DOCS}/invoices/{invoice['id']}/rectifications", headers=h, json={
        "mode": "partial", "refund_order_id": refund["id"], "reason": "Devolución parcial",
    })
    assert resp.status_code == 201, resp.text
    rect = resp.json()
    assert rect["series"] == "R" and rect["number"] == 1
    assert rect["doc_number"] == f"R {YEAR}/000001"
    assert rect["rectified_invoice_id"] == invoice["id"]
    assert rect["status"] == "issued"
    # Importes negativos: los de la devolución, no de la factura completa.
    assert Decimal(rect["total_amount"]) == Decimal("-1.50")
    assert Decimal(rect["total_base"]) + Decimal(rect["total_tax"]) == Decimal("-1.50")
    # La parcial sí enlaza la orden de devolución (UNIQUE una rectificativa por venta).
    assert [line["order_id"] for line in rect["lines"]] == [refund["id"]]
    assert rect["payload"]["rectifies"] == {
        "invoice_id": invoice["id"], "doc_number": invoice["doc_number"],
        "mode": "partial", "reason": "Devolución parcial",
    }
    assert Decimal(rect["payload"]["totals"]["total"]) == Decimal("-1.50")
    # La factura original permanece emitida: la parcial NO la anula.
    assert client.get(f"{DOCS}/invoices/{invoice['id']}", headers=h).json()["status"] == "issued"
    assert "documents.invoice_rectified" in _audit_actions(db)


def test_rectificativa_total_anula_el_importe_completo(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678")

    order, _, closed, invoice = _invoiced_sale(db, client, h, terminal, method, customer)

    resp = client.post(f"{DOCS}/invoices/{invoice['id']}/rectifications", headers=h, json={
        "mode": "total", "reason": "Anulación por error en la factura",
    })
    assert resp.status_code == 201, resp.text
    rect = resp.json()
    assert rect["doc_number"] == f"R {YEAR}/000001"
    assert rect["rectified_invoice_id"] == invoice["id"]
    # Invierte el desglose completo de la original (base/tax/total, no el tipo).
    assert rect["total_base"] == format(-Decimal(invoice["total_base"]), "f")
    assert rect["total_tax"] == format(-Decimal(invoice["total_tax"]), "f")
    assert Decimal(rect["total_amount"]) == Decimal("-3.00")
    assert rect["payload"]["totals"]["slices"][0]["rate_bp"] == 2100
    # Sin invoice_lines: esas ventas ya están en la original (UNIQUE por venta).
    assert rect["lines"] == []
    # La venta embebida aparece con importes invertidos, precio unitario intacto.
    block = rect["payload"]["orders"][0]
    assert block["order_id"] == order["id"]
    assert block["total"] == "-3.00"
    assert block["lines"][0]["unit_price"] == "1.50"
    assert block["lines"][0]["quantity"] == "-2.000"
    assert rect["payload"]["rectifies"]["mode"] == "total"


def test_reglas_de_rectificacion_rechazadas(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer1 = _mk_customer(db, tax_id="B11111111", name="Cliente Uno SL")
    customer2 = _mk_customer(db, tax_id="B22222222", name="Cliente Dos SL")

    order1, line1, _, invoice1 = _invoiced_sale(db, client, h, terminal, method, customer1)
    order2, _, _, invoice2 = _invoiced_sale(db, client, h, terminal, method, customer2)

    def _rect(invoice_id, **payload):
        return client.post(f"{DOCS}/invoices/{invoice_id}/rectifications",
                           headers=h, json=payload)

    # Parcial sin orden de devolución → 422.
    assert _rect(invoice1["id"], mode="partial", reason="x").status_code == 422
    # Total con orden de devolución → 422.
    assert _rect(invoice1["id"], mode="total", refund_order_id=order1["id"],
                 reason="x").status_code == 422
    # Modo desconocido → 422.
    assert _rect(invoice1["id"], mode="cuadratura", reason="x").status_code == 422
    # Orden de devolución inexistente → 404.
    assert _rect(invoice1["id"], mode="partial", refund_order_id=str(uuid4()),
                 reason="x").status_code == 404

    # La devolución debe corresponder a una venta de ESA factura → 422.
    refund1 = _refund(client, h, order1["id"], _cash_session_id(client, h, terminal),
                      line1, method, "1.50")
    assert _rect(invoice2["id"], mode="partial", refund_order_id=refund1["id"],
                 reason="x").status_code == 422

    # Factura inexistente → 404.
    assert _rect(str(uuid4()), mode="total", reason="x").status_code == 404


# ---------------------------------------------------------------------------
# 6 · Anulación de factura y reimpresión de ticket
# ---------------------------------------------------------------------------
def test_anulacion_de_factura_con_motivo(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678")

    _, _, _, invoice = _invoiced_sale(db, client, h, terminal, method, customer)

    resp = client.post(f"{DOCS}/invoices/{invoice['id']}/void", headers=h,
                       json={"reason": "Factura emitida por error"})
    assert resp.status_code == 200, resp.text
    voided = resp.json()
    assert voided["status"] == "voided"
    assert voided["void_reason"] == "Factura emitida por error"
    assert voided["voided_at"] is not None

    # Nunca se borra: sigue legible, y no se anula dos veces.
    assert client.get(f"{DOCS}/invoices/{invoice['id']}",
                      headers=h).json()["status"] == "voided"
    again = client.post(f"{DOCS}/invoices/{invoice['id']}/void", headers=h,
                        json={"reason": "segunda vez"})
    assert again.status_code == 409, again.text
    # Y una factura anulada no se rectifica.
    rect = client.post(f"{DOCS}/invoices/{invoice['id']}/rectifications", headers=h,
                       json={"mode": "total", "reason": "x"})
    assert rect.status_code == 409, rect.text
    assert "documents.invoice_voided" in _audit_actions(db)


def test_reimpresion_de_ticket_incrementa_el_contador(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)

    _, _, closed = _sale(client, h, db, terminal, method)
    ticket_id = closed["ticket"]["id"]
    printed_at = client.get(f"{DOCS}/tickets/{ticket_id}", headers=h).json()["printed_at"]

    first = client.post(f"{DOCS}/tickets/{ticket_id}/reprint", headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["reprint_count"] == 1
    assert first.json()["printed_at"] >= printed_at

    second = client.post(f"{DOCS}/tickets/{ticket_id}/reprint", headers=h)
    assert second.json()["reprint_count"] == 2
    # El payload NO se regenera: es el snapshot congelado de la emisión.
    assert second.json()["payload"]["doc_number"] == closed["ticket"]["doc_number"]
    assert client.get(f"{DOCS}/tickets/{ticket_id}",
                      headers=h).json()["reprint_count"] == 2
    assert "documents.ticket_reprinted" in _audit_actions(db)

    # Ticket inexistente → 404.
    assert client.get(f"{DOCS}/tickets/{uuid4()}", headers=h).status_code == 404
    assert client.post(f"{DOCS}/tickets/{uuid4()}/reprint", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# 7 · Datos fiscales y logo de cabecera (Configuración/Parámetros + volumen)
# ---------------------------------------------------------------------------
PNG_FALSO = b"\x89PNG\r\n\x1a\n logo-de-prueba"


def test_configuracion_de_negocio_y_logo_con_snapshot(db, client, _data_dir):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)

    # Estado inicial: sin datos fiscales ni logo; series por defecto.
    settings = client.get(f"{ADMIN}/business-settings", headers=h)
    assert settings.status_code == 200, settings.text
    assert settings.json() == {
        "business": {"name": "", "tax_id": "", "address": "", "phone": ""},
        "logo": None,
        "series": {"tickets": "A", "invoices": "FAC", "rectifications": "R"},
        "currency": {"decimals": "2"},
    }

    # PATCH parcial: solo los campos enviados.
    patched = client.patch(f"{ADMIN}/business-settings", headers=h, json={
        "name": "Bar Ejemplo SL", "tax_id": "B00000000",
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["business"] == {
        "name": "Bar Ejemplo SL", "tax_id": "B00000000", "address": "", "phone": "",
    }

    # Venta ANTES del logo: ticket sin logo.
    _, _, closed_sin = _sale(client, h, db, terminal, method)

    # PUT logo: fichero en el volumen + referencia en parámetros.
    data = client.put(f"{ADMIN}/logo", headers=h, json={
        "mime": "image/png", "data": base64.b64encode(PNG_FALSO).decode("ascii"),
    })
    assert data.status_code == 200, data.text
    logo = data.json()["logo"]
    assert logo["mime"] == "image/png" and logo["size"] == len(PNG_FALSO)
    assert UUID(logo["uploaded_by"])
    stored = _data_dir / "logos" / "logo.png"
    assert stored.read_bytes() == PNG_FALSO

    # Venta DESPUÉS del logo: el ticket embebe el logo como data URI.
    _, _, closed_con = _sale(client, h, db, terminal, method)
    body = client.get(f"{DOCS}/tickets/{closed_con['ticket']['id']}", headers=h).json()["payload"]
    expected_uri = "data:image/png;base64," + base64.b64encode(PNG_FALSO).decode("ascii")
    assert body["header"]["logo"] == expected_uri
    assert body["header"]["business"] == {
        "name": "Bar Ejemplo SL", "tax_id": "B00000000",
    }

    # Snapshot histórico: el ticket ANTERIOR conserva su cabecera sin logo
    # aunque el logo cambie después (el payload está congelado).
    body_sin = client.get(f"{DOCS}/tickets/{closed_sin['ticket']['id']}",
                          headers=h).json()["payload"]
    assert body_sin["header"]["logo"] is None

    # DELETE logo: fuera fichero y referencia; los emitidos conservan el suyo.
    assert client.delete(f"{ADMIN}/logo", headers=h).status_code == 204
    assert not stored.exists()
    assert client.get(f"{ADMIN}/business-settings", headers=h).json()["logo"] is None
    assert client.get(f"{DOCS}/tickets/{closed_con['ticket']['id']}",
                      headers=h).json()["payload"]["header"]["logo"] == expected_uri

    # Y sin logo configurado, DELETE de nuevo → 404.
    assert client.delete(f"{ADMIN}/logo", headers=h).status_code == 404
    actions = _audit_actions(db)
    assert {"documents.business_settings_updated", "documents.logo_updated",
            "documents.logo_removed"} <= actions


def test_validaciones_del_logo(db, client, _data_dir):
    h = _headers(db, client)

    def _put(data: str, mime: str = "image/png"):
        return client.put(f"{ADMIN}/logo", headers=h, json={"mime": mime, "data": data})

    # base64 inválido y logo vacío → 422.
    assert _put("no-es-base64!!!").status_code == 422
    assert _put("").status_code == 422
    # Mime no admitido → 422 (se rechaza en la petición).
    assert _put(base64.b64encode(b"x").decode("ascii"), mime="image/gif").status_code == 422
    # Por encima del máximo de 512 KiB → 422.
    assert _put("A" * 700_000).status_code == 422

    # Nada quedó configurado ni escrito en el volumen.
    assert client.get(f"{ADMIN}/business-settings", headers=h).json()["logo"] is None
    assert not (_data_dir / "logos").exists()


# ---------------------------------------------------------------------------
# 8 · Permisos: reimprimir todos, facturar/anular/configurar solo el jefe
# ---------------------------------------------------------------------------
def test_permisos_de_documentos(db, client):
    boss = _headers(db, client, perms=BOSS_PERMS, username="jefe")
    waiter_role = _mk_role(db, "role-camarero", WAITER_PERMS)
    _mk_user(db, waiter_role, "camarero")
    waiter = _bearer(_login(client, "camarero").json()["access_token"])

    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db, tax_id="B12345678")
    order, line, closed = _sale(client, boss, db, terminal, method, customer_id=customer)
    invoice = client.post(f"{DOCS}/invoices", headers=boss,
                          json={"order_ids": [order["id"]]}).json()

    # El camarero consulta y reimprime tickets (tickets.reprint)…
    assert client.get(f"{DOCS}/tickets/{closed['ticket']['id']}",
                      headers=waiter).status_code == 200
    assert client.post(f"{DOCS}/tickets/{closed['ticket']['id']}/reprint",
                       headers=waiter).status_code == 200
    # …pero no emite, lee, anula ni rectifica facturas.
    assert client.post(f"{DOCS}/invoices", headers=waiter,
                       json={"order_ids": [order["id"]]}).status_code == 403
    assert client.get(f"{DOCS}/invoices/{invoice['id']}", headers=waiter).status_code == 403
    assert client.post(f"{DOCS}/invoices/{invoice['id']}/void", headers=waiter,
                       json={"reason": "x"}).status_code == 403
    assert client.post(f"{DOCS}/invoices/{invoice['id']}/rectifications", headers=waiter,
                       json={"mode": "total", "reason": "x"}).status_code == 403
    # …ni toca la configuración del negocio.
    assert client.get(f"{ADMIN}/business-settings", headers=waiter).status_code == 403
    assert client.patch(f"{ADMIN}/business-settings", headers=waiter,
                        json={"name": "x"}).status_code == 403
    assert client.put(f"{ADMIN}/logo", headers=waiter,
                      json={"mime": "image/png", "data": "aG9sYQ=="}).status_code == 403
    assert client.delete(f"{ADMIN}/logo", headers=waiter).status_code == 403

    # El jefe sí gobierna facturas y configuración.
    assert client.post(f"{DOCS}/invoices/{invoice['id']}/void", headers=boss,
                       json={"reason": "cierre del ejercicio"}).status_code == 200

    # Anónimo: nada.
    assert client.get(f"{DOCS}/tickets/{closed['ticket']['id']}").status_code == 401
    assert client.get(f"{ADMIN}/business-settings").status_code == 401

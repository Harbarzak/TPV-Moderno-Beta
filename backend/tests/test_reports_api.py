"""Tests E2E de informes (fase 15) contra PostgreSQL real.

Cubren los casos de FASE_15: tickets emitidos, facturas (listado + detalle),
cierres Z pasados y las estadísticas del periodo (resumen con desglose de IVA,
por producto, categoría, camarero, forma de pago y tramo temporal), más la
disciplina anti-carga: ``from``/``to`` obligatorios, rango ≤ 366 días (422),
páginas acotadas y ``reports.view`` como permiso de entrada (403 sin él).

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías. El dinero viaja SIEMPRE como string en el JSON (§3).
"""

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

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
REPORTS = f"{V1}/reports"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# El encargado: vende, cobra, devuelve, anula, factura y consulta informes.
REPORTS_PERMS = ("products.view", "products.edit", "sales.sell", "sales.void",
                 "payments.take", "payments.refund", "cash.open", "cash.close",
                 "invoices.issue", "reports.view")
# Sin reports.view no se ve NINGÚN informe (403).
NO_REPORTS_PERMS = ("products.view", "products.edit", "sales.sell",
                    "payments.take", "cash.open")

# IVA reducido de prueba: con precio 1.10 y 2.20 las bases son exactas.
RATE = "10.00"


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
# Fábricas mínimas (mismo patrón que test_documents_api.py)
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


def _headers(db, client, perms=REPORTS_PERMS, username="cajero") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _tax_and_products(client, h) -> tuple[dict, dict, dict]:
    """IVA 10 % + dos productos con precio final exacto (1.10 y 2.20)."""
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "reducido", "name": "IVA reducido", "rate": RATE,
        "valid_from": "2026-01-01",
    }).json()
    p1 = client.post(f"{CAT}/products", headers=h, json={
        "name": "Café solo", "tax_rate_id": tax["id"], "price": "1.10",
    }).json()
    p2 = client.post(f"{CAT}/products", headers=h, json={
        "name": "Té verde", "tax_rate_id": tax["id"], "price": "2.20",
    }).json()
    return tax, p1, p2


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


def _open(client, h, terminal_id, opening="50.00") -> dict:
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


def _window() -> dict:
    """Rango ``from``/``to`` válido: ±2 h alrededor de ahora (ISO con zona)."""
    now = datetime.now(timezone.utc)
    return {
        "from": (now - timedelta(hours=2)).isoformat(),
        "to": (now + timedelta(hours=2)).isoformat(),
    }


def _get(client, h, path: str, params: dict | None = None):
    return client.get(f"{REPORTS}/{path}", headers=h, params=params)


# ---------------------------------------------------------------------------
# Tickets emitidos
# ---------------------------------------------------------------------------
def test_tickets_listado_con_total_y_numero_de_documento(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _, p1, _ = _tax_and_products(client, h)
    cash = _cash_session_id(client, h, terminal)

    order = _mk_order(client, h, terminal)
    _add_line(client, h, order["id"], p1["id"], quantity="2")
    _close_sale(client, h, order["id"], cash, [_pay(method, "2.20")])

    resp = _get(client, h, "tickets", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    ticket = body["items"][0]
    assert ticket["order_id"] == order["id"]
    assert ticket["terminal_id"] == terminal
    assert ticket["doc_number"]  # extraído en SQL del payload congelado
    assert ticket["series"] and ticket["number"] == 1
    assert Decimal(ticket["total_amount"]) == Decimal("2.20")
    # printed_at = momento de emisión (el ticket nace impreso en el cobro);
    # la confirmación del job físico es otro ciclo (agentes, fase 14).
    assert ticket["printed_at"] is not None and ticket["reprint_count"] == 0

    # Filtro por terminal: otro terminal no ve nada.
    resp = _get(client, h, "tickets", {**_window(), "terminal_id": str(uuid4())})
    assert resp.status_code == 200 and resp.json()["total"] == 0


def test_tickets_requiere_rango_acotado(db, client):
    h = _headers(db, client)
    # Sin «from» o sin «to» no se sirve nada: 422 de validación de entrada.
    for params in ({}, {"to": _window()["to"]}, {"from": _window()["from"]}):
        resp = _get(client, h, "tickets", params)
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "VALIDATION_ERROR"

    now = datetime.now(timezone.utc)
    # Rango invertido.
    resp = _get(client, h, "tickets", {
        "from": (now + timedelta(hours=1)).isoformat(),
        "to": now.isoformat(),
    })
    assert resp.status_code == 422 and resp.json()["code"] == "VALIDATION_ERROR"
    # Rango por encima del techo de 366 días.
    resp = _get(client, h, "tickets", {
        "from": (now - timedelta(days=400)).isoformat(),
        "to": now.isoformat(),
    })
    assert resp.status_code == 422 and resp.json()["code"] == "VALIDATION_ERROR"


def test_informes_sin_permiso_403(db, client):
    h = _headers(db, client, perms=NO_REPORTS_PERMS)
    resp = _get(client, h, "tickets", _window())
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "PERMISSION_DENIED"
    assert _get(client, h, "stats/summary", _window()).status_code == 403


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------
def test_facturas_listado_y_detalle(db, client, _data_dir):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _, p1, _ = _tax_and_products(client, h)
    customer = _mk_customer(db)
    cash = _cash_session_id(client, h, terminal)

    order = _mk_order(client, h, terminal, customer_id=customer)
    _add_line(client, h, order["id"], p1["id"], quantity="2")
    _close_sale(client, h, order["id"], cash, [_pay(method, "2.20")])
    issued = client.post(f"{DOCS}/invoices", headers=h,
                         json={"order_ids": [order["id"]]})
    assert issued.status_code == 201, issued.text
    invoice = issued.json()

    # Listado: SIN payload, con número legible y dinero string.
    resp = _get(client, h, "invoices", {
        "from": date.today().isoformat(), "to": date.today().isoformat(),
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    row = body["items"][0]
    assert row["id"] == invoice["id"]
    assert row["doc_number"] == invoice["doc_number"]
    assert row["status"] == "issued"
    assert Decimal(row["total_base"]) == Decimal("2.00")
    assert Decimal(row["total_tax"]) == Decimal("0.20")
    assert Decimal(row["total_amount"]) == Decimal("2.20")
    assert "payload" not in row  # el detalle es otra consulta

    # Filtros por serie y estado.
    resp = _get(client, h, "invoices", {
        "from": date.today().isoformat(), "to": date.today().isoformat(),
        "series": invoice["series"],
    })
    assert resp.json()["total"] == 1
    resp = _get(client, h, "invoices", {
        "from": date.today().isoformat(), "to": date.today().isoformat(),
        "status": "voided",
    })
    assert resp.json()["total"] == 0

    # Detalle: el snapshot congelado, igual que en /documents.
    detail = _get(client, h, f"invoices/{invoice['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["doc_number"] == invoice["doc_number"]
    assert body["payload"]["totals"]["total"] == "2.20"
    assert [line["order_id"] for line in body["lines"]] == [order["id"]]

    # Desconocida: 404.
    assert _get(client, h, f"invoices/{uuid4()}").status_code == 404


# ---------------------------------------------------------------------------
# Cierres pasados (Z)
# ---------------------------------------------------------------------------
def test_cierres_pasados_con_cuadre_congelado(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _, p1, _ = _tax_and_products(client, h)

    session = _open(client, h, terminal)  # fondo 50.00
    order = _mk_order(client, h, terminal)
    _add_line(client, h, order["id"], p1["id"], quantity="2")
    _close_sale(client, h, order["id"], session["id"], [_pay(method, "2.20")])

    # Cierre Z contando 52.20 exacto → diferencia cero, congelada en la fila.
    closed = client.post(f"{CASH}/sessions/{session['id']}/close", headers=h, json={
        # Denominación = valor facial en € (50 + 2 + 0.20 = 52.20).
        "lines": [{"denomination": "50.00", "quantity": 1},
                  {"denomination": "2.00", "quantity": 1},
                  {"denomination": "0.20", "quantity": 1}],
    })
    assert closed.status_code == 200, closed.text

    resp = _get(client, h, "cash-closures", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1 and len(body["items"]) == 1
    row = body["items"][0]
    assert row["id"] == session["id"]
    assert row["status"] == "closed"
    assert row["closed_at"] is not None
    assert row["opening_amount"] == "50.00"
    assert (row["expected_amount"], row["counted_amount"], row["difference"]) == (
        "52.20", "52.20", "0.00")


# ---------------------------------------------------------------------------
# Estadísticas del periodo
# ---------------------------------------------------------------------------
def _escenario_completo(db, client, h):
    """A: 2×1.10 cash · B: 1×2.20 card · devolución de 1×A · draft anulado.

    Ventas 4.40, devoluciones −1.10, neta 3.30; una sola base (10 %) con
    base 3.00; 2 ventas + 1 devolución cobradas y 1 anulada.
    """
    terminal = _mk_terminal(db)
    cash_m = _mk_payment_method(db)
    card_m = _mk_payment_method(db, code="CARD", name="Tarjeta", kind="card")
    _, p1, p2 = _tax_and_products(client, h)
    cash = _cash_session_id(client, h, terminal)

    a = _mk_order(client, h, terminal)
    line_a = _add_line(client, h, a["id"], p1["id"], quantity="2")
    _close_sale(client, h, a["id"], cash, [_pay(cash_m, "2.20")])

    b = _mk_order(client, h, terminal)
    _add_line(client, h, b["id"], p2["id"], quantity="1")
    _close_sale(client, h, b["id"], cash, [_pay(card_m, "2.20")])

    refund = _refund(client, h, a["id"], cash, line_a, cash_m, "1.10")

    draft = _mk_order(client, h, terminal)
    _add_line(client, h, draft["id"], p2["id"], quantity="1")
    voided = client.post(f"{SALES}/orders/{draft['id']}/void", headers=h,
                         json={"reason": "Cliente se fue"})
    assert voided.status_code == 200, voided.text
    return {"a": a, "b": b, "refund": refund, "voided": draft}


def test_resumen_del_periodo_con_desglose_de_iva(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/summary", _window())
    assert resp.status_code == 200, resp.text
    s = resp.json()
    assert s["sales_count"] == 2
    assert Decimal(s["sales_amount"]) == Decimal("4.40")
    assert s["refunds_count"] == 1
    assert Decimal(s["refunds_amount"]) == Decimal("-1.10")
    assert s["voided_count"] == 1
    assert Decimal(s["net_amount"]) == Decimal("3.30")
    assert Decimal(s["average_ticket"]) == Decimal("2.20")  # 4.40 / 2
    # Desglose de IVA agrupado desde las líneas: base 3.00, cuota 0.30.
    assert len(s["tax_breakdown"]) == 1
    line = s["tax_breakdown"][0]
    assert Decimal(line["tax_rate"]) == Decimal("10.00")
    assert Decimal(line["base"]) == Decimal("3.00")
    assert Decimal(line["total"]) == Decimal("3.30")


def test_estadistica_por_producto_netando_devoluciones(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/by-product", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # Orden por total neto descendente: Té (2.20) primero, Café (1.10) después.
    te, cafe = body["items"]
    assert te["name"] == "Té verde" and cafe["name"] == "Café solo"
    assert te["orders"] == 1 and Decimal(te["total"]) == Decimal("2.20")
    assert cafe["orders"] == 2  # venta + devolución
    assert Decimal(cafe["quantity"]) == Decimal("1.000")  # 2 vendidos − 1 devuelto
    assert Decimal(cafe["total"]) == Decimal("1.10")


def test_estadistica_por_categoria_sin_categoria(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/by-category", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Los productos no tienen categoría: una sola fila «(sin categoría)».
    assert body["total"] == 1
    row = body["items"][0]
    assert row["name"] == "(sin categoría)"
    assert row["category_id"] is None
    assert row["orders"] == 3
    assert Decimal(row["base"]) == Decimal("3.00")
    assert Decimal(row["total"]) == Decimal("3.30")


def test_estadistica_por_camarero(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/by-waiter", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["username"] == "cajero"
    assert row["sales_count"] == 2 and row["refunds_count"] == 1
    assert Decimal(row["total"]) == Decimal("3.30")


def test_estadistica_por_forma_de_pago(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/by-payment-method", _window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    by_code = {row["code"]: row for row in body["items"]}
    # Devoluciones en positivo (convención del informe Z: se restan del total).
    assert Decimal(by_code["CASH"]["sales_amount"]) == Decimal("2.20")
    assert by_code["CASH"]["sales_count"] == 1
    assert Decimal(by_code["CASH"]["refunds_amount"]) == Decimal("1.10")
    assert by_code["CASH"]["refunds_count"] == 1
    assert by_code["CASH"]["kind"] == "cash"
    assert Decimal(by_code["CARD"]["sales_amount"]) == Decimal("2.20")
    assert Decimal(by_code["CARD"]["refunds_amount"]) == Decimal("0.00")
    assert by_code["CARD"]["kind"] == "card"


def test_estadistica_por_periodo_por_dias_y_horas(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    resp = _get(client, h, "stats/by-period", {**_window(), "interval": "day"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1  # todo cabe en el tramo de hoy
    row = body["items"][0]
    assert row["bucket"].startswith(date.today().isoformat())
    assert row["sales_count"] == 2
    assert Decimal(row["sales_amount"]) == Decimal("4.40")
    assert row["refunds_count"] == 1
    assert Decimal(row["refunds_amount"]) == Decimal("-1.10")

    # Otro tramo admitido: agrupación por hora.
    resp = _get(client, h, "stats/by-period", {**_window(), "interval": "hour"})
    assert resp.status_code == 200 and resp.json()["total"] >= 1

    # Tramo no admitido: 422.
    resp = _get(client, h, "stats/by-period", {**_window(), "interval": "year"})
    assert resp.status_code == 422


def test_paginacion_de_informes(db, client):
    h = _headers(db, client)
    _escenario_completo(db, client, h)

    page1 = _get(client, h, "stats/by-product", {**_window(), "limit": 1})
    assert page1.status_code == 200, page1.text
    body = page1.json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert body["limit"] == 1 and body["offset"] == 0

    page2 = _get(client, h, "stats/by-product",
                 {**_window(), "limit": 1, "offset": 1})
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["name"] != body["items"][0]["name"]

    # Límites de página: 422 fuera de rango.
    assert _get(client, h, "stats/by-product", {**_window(), "limit": 0}).status_code == 422
    assert _get(client, h, "stats/by-product", {**_window(), "limit": 201}).status_code == 422
    assert _get(client, h, "stats/by-product", {**_window(), "offset": -1}).status_code == 422

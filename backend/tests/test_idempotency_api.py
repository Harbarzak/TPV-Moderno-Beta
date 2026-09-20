"""Tests E2E de idempotencia (fase 14 · Offline) contra PostgreSQL real.

Cubren el objetivo del prompt «usar idempotency keys / evitar ventas
duplicadas» de punta a punta: el cobro EXIGE la cabecera ``Idempotency-Key``
(422 sin ella), el reintento con la misma clave y contenido devuelve la MISMA
respuesta congelada sin repetir pagos, eventos, ticket ni auditoría (§4.1),
y reutilizar la clave con otro contenido u otra operación es 409
``IDEMPOTENCY_KEY_REUSED``. También el replay de abrir pedido, añadir línea
y caja (apertura y movimiento, clave opcional).

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías; las claves de prueba son ``uuid4`` (nuevas en cada ejecución).
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
CASH = f"{V1}/cash"
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
# Fábricas mínimas (mismo patrón que test_sales_api.py)
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
    if closed:
        # Cerrada con cuadre exacto (CHECK ck_cash_sessions_close_complete).
        sql = (
            "INSERT INTO cash_sessions (terminal_id, opened_by, opening_amount, "
            "closed_at, expected_amount, counted_amount, difference) "
            "VALUES (:t, :u, 50.00, NOW(), 40.00, 40.00, 0.00) "
        )
    else:
        sql += "VALUES (:t, :u, 100.00) "
    return str(db.execute(
        text(sql + "RETURNING id"), params
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


def _venta_en_curso(db, client, h):
    """Terminal + caja abierta + forma CASH + pedido con una línea de 1.50."""
    terminal = _mk_terminal(db)
    user_id = db.execute(
        text("SELECT id FROM users WHERE username = 'jefe'")
    ).scalar_one()
    cash = _mk_cash_session(db, terminal, str(user_id))
    method = _mk_payment_method(db)
    order = _mk_order(client, h, terminal)
    _, cafe = _tax_and_product(client, h)
    _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": "1"})
    return cash, method, order


def _close_payload(cash: str, method: str, amount: str = "1.50") -> dict:
    return {"cash_session_id": cash,
            "payments": [{"payment_method_id": method, "amount": amount}]}


def _post_close(client, h, order_id, payload, *, key=None):
    headers = {**h}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(f"{SALES}/orders/{order_id}/close", headers=headers, json=payload)


# ---------------------------------------------------------------------------
# 1 · El cobro EXIGE la clave (§4.1): sin cabecera, 422 y sin efectos
# ---------------------------------------------------------------------------
def test_cobro_sin_clave_rechazado(db, client):
    h = _sales_headers(db, client)
    cash, method, order = _venta_en_curso(db, client, h)

    resp = _post_close(client, h, order["id"], _close_payload(cash, method))
    assert resp.status_code == 422
    assert resp.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    # Sin efectos: la venta sigue en borrador y no hay rastro de pagos.
    assert db.execute(text("SELECT status FROM orders")).scalar_one() == "draft"
    assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM idempotency_keys")).scalar_one() == 0


# ---------------------------------------------------------------------------
# 2 · Reintento del cobro con la misma clave: MISMA respuesta, NINGÚN efecto
# ---------------------------------------------------------------------------
def test_cobro_reintento_misma_clave_mismo_ticket(db, client):
    h = _sales_headers(db, client)
    cash, method, order = _venta_en_curso(db, client, h)
    payload = _close_payload(cash, method)
    key = str(uuid4())

    first = _post_close(client, h, order["id"], payload, key=key)
    assert first.status_code == 200, first.text
    second = _post_close(client, h, order["id"], payload, key=key)
    assert second.status_code == 200, second.text

    # Respuesta congelada: mismo cuerpo, mismo ticket (§4.1 «misma key →
    # mismo ticket»); ni un pago, evento, ticket ni auditoría repetidos.
    assert second.json() == first.json()
    assert first.json()["ticket"]["doc_number"]
    assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 1
    assert db.execute(text(
        "SELECT count(*) FROM sale_events WHERE event_type = 'sale_closed'"
    )).scalar_one() == 1
    assert db.execute(text(
        "SELECT count(*) FROM audit_log WHERE action = 'sales.order_closed'"
    )).scalar_one() == 1
    assert db.execute(text("SELECT count(*) FROM idempotency_keys")).scalar_one() == 1


# ---------------------------------------------------------------------------
# 3 · La misma clave con OTRO contenido: 409 (nunca hereda la respuesta)
# ---------------------------------------------------------------------------
def test_cobro_misma_clave_otro_contenido_409(db, client):
    h = _sales_headers(db, client)
    cash, method, order = _venta_en_curso(db, client, h)
    key = str(uuid4())

    first = _post_close(client, h, order["id"], _close_payload(cash, method), key=key)
    assert first.status_code == 200, first.text

    # El importe cambió: la clave NO puede devolver la respuesta anterior.
    # Gana el rechazo de idempotencia incluso sobre el 409 de venta pagada.
    changed = _post_close(client, h, order["id"],
                          _close_payload(cash, method, amount="9.99"), key=key)
    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


# ---------------------------------------------------------------------------
# 4 · La clave no se comparte entre operaciones distintas
# ---------------------------------------------------------------------------
def test_clave_no_reutilizable_entre_operaciones(db, client):
    h = _sales_headers(db, client)
    terminal = _mk_terminal(db)
    key = "clave-compartida"

    first = client.post(f"{SALES}/orders", headers={**h, "Idempotency-Key": key},
                        json={"terminal_id": terminal})
    assert first.status_code == 201, first.text

    # La misma clave para «añadir línea» (otro endpoint lógico): 409.
    _, cafe = _tax_and_product(client, h)
    clash = client.post(f"{SALES}/orders/{first.json()['id']}/lines",
                        headers={**h, "Idempotency-Key": key},
                        json={"product_id": cafe["id"], "quantity": "1"})
    assert clash.status_code == 409
    assert clash.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


# ---------------------------------------------------------------------------
# 5-7 · Replay sin duplicar efectos: abrir pedido, añadir línea
# ---------------------------------------------------------------------------
def test_abrir_pedido_reintento_no_duplica(db, client):
    h = _sales_headers(db, client)
    terminal = _mk_terminal(db)
    payload = {"terminal_id": terminal}
    key = str(uuid4())

    first = client.post(f"{SALES}/orders", headers={**h, "Idempotency-Key": key},
                        json=payload)
    second = client.post(f"{SALES}/orders", headers={**h, "Idempotency-Key": key},
                         json=payload)
    assert first.status_code == 201 and second.status_code == 201
    assert second.json() == first.json()
    assert db.execute(text("SELECT count(*) FROM orders")).scalar_one() == 1
    assert db.execute(text(
        "SELECT count(*) FROM audit_log WHERE action = 'sales.order_created'"
    )).scalar_one() == 1


def test_annadir_linea_reintento_no_duplica(db, client):
    h = _sales_headers(db, client)
    order = _mk_order(client, h, _mk_terminal(db))
    _, cafe = _tax_and_product(client, h)
    payload = {"product_id": cafe["id"], "quantity": "1"}
    key = str(uuid4())

    first = client.post(f"{SALES}/orders/{order['id']}/lines",
                        headers={**h, "Idempotency-Key": key}, json=payload)
    second = client.post(f"{SALES}/orders/{order['id']}/lines",
                         headers={**h, "Idempotency-Key": key}, json=payload)
    assert first.status_code == 201 and second.status_code == 201
    assert second.json() == first.json()
    assert db.execute(text("SELECT count(*) FROM order_lines")).scalar_one() == 1


# ---------------------------------------------------------------------------
# 8-9 · Caja (clave opcional): apertura y movimiento sin duplicar
# ---------------------------------------------------------------------------
def test_apertura_de_caja_reintento_no_duplica(db, client):
    h = _sales_headers(db, client, perms=("cash.open",), username="cajero")
    terminal = _mk_terminal(db)
    payload = {"terminal_id": terminal, "opening_amount": "50.00"}
    key = str(uuid4())

    first = client.post(f"{CASH}/sessions", headers={**h, "Idempotency-Key": key},
                        json=payload)
    second = client.post(f"{CASH}/sessions", headers={**h, "Idempotency-Key": key},
                         json=payload)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json() == first.json()
    assert db.execute(text("SELECT count(*) FROM cash_sessions")).scalar_one() == 1


def test_movimiento_de_caja_reintento_no_duplica(db, client):
    h = _sales_headers(db, client, perms=("cash.open", "cash.movements"),
                       username="cajero")
    terminal = _mk_terminal(db)
    cash_id = client.post(f"{CASH}/sessions", headers=h, json={
        "terminal_id": terminal, "opening_amount": "50.00",
    }).json()["id"]
    payload = {"kind": "in", "amount": "10.00", "reason": "reposición de cambio"}
    key = str(uuid4())

    first = client.post(f"{CASH}/sessions/{cash_id}/movements",
                        headers={**h, "Idempotency-Key": key}, json=payload)
    second = client.post(f"{CASH}/sessions/{cash_id}/movements",
                         headers={**h, "Idempotency-Key": key}, json=payload)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json() == first.json()
    assert db.execute(text("SELECT count(*) FROM cash_movements")).scalar_one() == 1

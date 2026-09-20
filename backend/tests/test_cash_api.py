"""Tests E2E de caja (fase 08) contra PostgreSQL real.

Cubren los casos de FASE_08: apertura con fondo inicial, única sesión abierta
por terminal (409 en duplicado y en la carrera concurrente), movimientos de
entrada/salida, ventas y devoluciones en efectivo alimentando el esperado,
listado X sin cerrar, arqueo parcial, cierre Z que congela
esperado/contado/diferencia, informe Z del histórico, operaciones sobre sesión
cerrada (409) y permisos ``cash.open``/``cash.movements``/``cash.close``/
``reports.view``.

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
AUTH = f"{V1}/auth"
CASH = f"{V1}/cash"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# El cajero de los ciclos: vende, cobra, devuelve y gobierna su caja.
CASHIER_PERMS = ("products.view", "products.edit", "sales.sell",
                 "payments.take", "payments.refund",
                 "cash.open", "cash.movements", "cash.close", "reports.view")
# El camarero solo puede abrir y consultar la caja de su terminal.
WAITER_PERMS = ("cash.open",)


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
# Fábricas mínimas (mismo patrón que test_payments_api.py)
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


def _mk_terminal(db, code=None) -> str:
    # Código único por defecto: dos terminales de prueba no pueden pisarse.
    if code is None:
        code = f"T{uuid4().hex[:6].upper()}"
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


def _headers(db, client, perms=CASHIER_PERMS, username="cajero") -> dict:
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


def _pay(method_id: str, amount: str) -> dict:
    return {"payment_method_id": method_id, "amount": amount}


def _close_sale(client, h, order_id, cash_session_id, payments) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _open(client, h, terminal_id, opening="50.00"):
    return client.post(f"{CASH}/sessions", headers=h,
                       json={"terminal_id": terminal_id, "opening_amount": opening})


def _lines(*pairs) -> list[dict]:
    """Denominaciones del recuento como valor facial (€): ``("50.00", 1)`` es
    UN billete de 50; ``("1.00", 3)`` son tres monedas de 1 €."""

    return [{"denomination": denomination, "quantity": quantity}
            for denomination, quantity in pairs]


# ---------------------------------------------------------------------------
# 1 · Ciclo completo: apertura → venta y devolución en efectivo → movimientos
#     → X → arqueo parcial → cierre Z con diferencia congelada
# ---------------------------------------------------------------------------
def test_ciclo_completo_de_caja(db, client):
    h = _headers(db, client)
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'cajero'")).scalar_one()

    # Apertura con fondo inicial.
    opened = _open(client, h, terminal_id := _mk_terminal(db))
    assert opened.status_code == 201, opened.text
    cash = opened.json()
    assert cash["status"] == "open" and cash["opening_amount"] == "50.00"
    assert (cash["expected_amount"], cash["counted_amount"],
            cash["difference"], cash["closed_at"]) == (None, None, None, None)
    assert cash["opened_by"] == str(user_id)
    cash_id = cash["id"]

    # Venta 3.00 en efectivo y devolución de 1.50 en efectivo.
    cash_method = _mk_payment_method(db)
    _, cafe = _tax_and_product(client, h)
    order = _mk_order(client, h, terminal_id)
    line = _add_line(client, h, order["id"], {"product_id": cafe["id"], "quantity": "2"})
    closed = _close_sale(client, h, order["id"], cash_id, [_pay(cash_method, "3.00")])
    assert closed["status"] == "paid"
    refund = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": cash_id, "reason": "Un café estropeado",
        "lines": [{"line_id": line["id"], "quantity": "1"}],
        "payments": [_pay(cash_method, "1.50")],
    })
    assert refund.status_code == 201, refund.text

    # Entrada y salida de efectivo con motivo.
    entry = client.post(f"{CASH}/sessions/{cash_id}/movements", headers=h, json={
        "kind": "in", "amount": "10.00", "reason": "Cambio recogido de otro terminal"})
    assert entry.status_code == 201, entry.text
    assert entry.json()["kind"] == "in" and entry.json()["amount"] == "10.00"
    out = client.post(f"{CASH}/sessions/{cash_id}/movements", headers=h, json={
        "kind": "out", "amount": "5.00", "reason": "Retirada a caja fuerte"})
    assert out.status_code == 201, out.text

    # Listado X (sesión abierta): el esperado lo calcula el backend.
    report = client.get(f"{CASH}/sessions/{cash_id}/report", headers=h)
    assert report.status_code == 200
    x = report.json()
    assert x["difference"] is None  # sin cerrar, nada congelado
    assert (x["opening_amount"], x["cash_sales"], x["cash_refunds"],
            x["cash_in"], x["cash_out"]) == ("50.00", "3.00", "1.50", "10.00", "5.00")
    assert x["expected_cash"] == "56.50"  # 50 + 3 − 1,5 + 10 − 5
    [total] = x["method_totals"]
    assert (total["code"], total["kind"], total["sales_total"], total["sales_count"],
            total["refunds_total"], total["refunds_count"]) == (
        "CASH", "cash", "3.00", 1, "1.50", 1)
    assert [m["reason"] for m in x["movements"]] == [
        "Cambio recogido de otro terminal", "Retirada a caja fuerte"]

    # Arqueo parcial: 56,50 contados → cuadra; la sesión NO se cierra.
    partial = client.post(f"{CASH}/sessions/{cash_id}/counts", headers=h, json={
        "lines": _lines(("50.00", 1), ("5.00", 1), ("1.00", 1), ("0.50", 1))})
    assert partial.status_code == 201, partial.text
    assert partial.json()["counted_amount"] == "56.50"
    assert partial.json()["expected_amount"] == "56.50"
    assert partial.json()["difference"] == "0.00"
    still_open = client.get(f"{CASH}/sessions/{cash_id}", headers=h).json()
    assert still_open["status"] == "open" and still_open["expected_amount"] is None

    # Sessión abierta del terminal (la que muestra el TPV al arrancar).
    current = client.get(f"{CASH}/sessions/current", headers=h,
                         params={"terminal_id": terminal_id})
    assert current.status_code == 200 and current.json()["id"] == cash_id

    # Cierre Z contando 58,50 → sobra 2,00; el cuadre queda congelado.
    closed_z = client.post(f"{CASH}/sessions/{cash_id}/close", headers=h, json={
        "lines": _lines(("50.00", 1), ("5.00", 1), ("1.00", 3), ("0.50", 1))})
    assert closed_z.status_code == 200, closed_z.text
    z = closed_z.json()
    assert z["status"] == "closed" and z["closed_by"] == str(user_id)
    assert z["closed_at"] is not None
    assert (z["expected_amount"], z["counted_amount"], z["difference"]) == (
        "56.50", "58.50", "2.00")

    # Informe Z posterior: los dos arqueos quedan persistidos e inmutables.
    z_report = client.get(f"{CASH}/sessions/{cash_id}/report", headers=h).json()
    assert z_report["difference"] == "2.00"
    assert [c["counted_amount"] for c in z_report["counts"]] == ["56.50", "58.50"]
    assert z_report["counts"][1]["difference"] is None  # el cuadre vivo es el de la sesión

    # Auditoría de toda la vida de la sesión.
    actions = db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'cash.%' ORDER BY id")
    ).scalars().all()
    assert actions == [
        "cash.session_opened", "cash.movement_created", "cash.movement_created",
        "cash.count_recorded", "cash.count_recorded", "cash.session_closed",
    ]
    rows = db.execute(
        text("SELECT counted_amount FROM cash_counts ORDER BY created_at")
    ).scalars().all()
    assert [format(amount, "f") for amount in rows] == ["56.50", "58.50"]


# ---------------------------------------------------------------------------
# 2 · Una sola sesión abierta por terminal
# ---------------------------------------------------------------------------
def test_apertura_duplicada_y_terminal_invalido(db, client):
    h = _headers(db, client)

    missing = _open(client, h, str(uuid4()))
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"

    # Dinero como float: rechazado por contrato (§3).
    floated = client.post(f"{CASH}/sessions", headers=h, json={
        "terminal_id": _mk_terminal(db), "opening_amount": 50.5})
    assert floated.status_code == 422

    terminal = _mk_terminal(db)
    assert _open(client, h, terminal).status_code == 201
    dup = _open(client, h, terminal)
    assert dup.status_code == 409 and dup.json()["code"] == "CONFLICT"
    assert "sesión de caja abierta" in dup.json()["detail"]

    # Otro terminal sí puede abrir la suya.
    assert _open(client, h, _mk_terminal(db, "T2")).status_code == 201

    # Terminal inactivo: 409 aunque nunca haya tenido sesión.
    t3 = _mk_terminal(db, "T3")
    db.execute(text("UPDATE terminals SET active = false WHERE id = :i"), {"i": t3})
    db.commit()
    inactive = _open(client, h, t3)
    assert inactive.status_code == 409 and inactive.json()["code"] == "CONFLICT"


# ---------------------------------------------------------------------------
# 3 · Carrera de aperturas: el índice parcial deja un solo ganador
# ---------------------------------------------------------------------------
def test_apertura_concurrente_un_solo_ganador(db, client, app):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    payload = {"terminal_id": terminal, "opening_amount": "50.00"}

    outcomes: list[int] = []

    def _attempt() -> None:
        # Sin context manager: un event loop por petición (db_null_pool).
        probe = TestClient(app, raise_server_exceptions=False)
        outcomes.append(probe.post(f"{CASH}/sessions", headers=h, json=payload).status_code)

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [201, 409]
    assert db.execute(
        text("SELECT count(*) FROM cash_sessions WHERE terminal_id = :t"),
        {"t": terminal},
    ).scalar_one() == 1


# ---------------------------------------------------------------------------
# 4 · Nada sobre una sesión cerrada; el cierre no se repite
# ---------------------------------------------------------------------------
def test_operaciones_sobre_sesion_cerrada_rechazadas(db, client):
    h = _headers(db, client)
    cash_id = _open(client, h, _mk_terminal(db)).json()["id"]
    closed = client.post(f"{CASH}/sessions/{cash_id}/close", headers=h,
                         json={"lines": _lines(("50.00", 1))})
    assert closed.status_code == 200

    dup_close = client.post(f"{CASH}/sessions/{cash_id}/close", headers=h,
                            json={"lines": _lines(("50.00", 1))})
    assert dup_close.status_code == 409 and dup_close.json()["code"] == "CONFLICT"

    movement = client.post(f"{CASH}/sessions/{cash_id}/movements", headers=h, json={
        "kind": "in", "amount": "10.00", "reason": "tarde"})
    assert movement.status_code == 409

    count = client.post(f"{CASH}/sessions/{cash_id}/counts", headers=h,
                        json={"lines": _lines(("50.00", 1))})
    assert count.status_code == 409

    # Y el terminal puede reabrir caja después del cierre.
    reopened = _open(client, h, _mk_terminal(db))
    assert reopened.status_code == 201


# ---------------------------------------------------------------------------
# 5 · Recuento inválido: sin líneas, denominación no positiva, cantidad negativa
# ---------------------------------------------------------------------------
def test_recuento_invalido_rechazado(db, client):
    h = _headers(db, client)
    cash_id = _open(client, h, _mk_terminal(db)).json()["id"]
    url = f"{CASH}/sessions/{cash_id}/counts"

    empty = client.post(url, headers=h, json={"lines": []})
    assert empty.status_code == 422

    zero = client.post(url, headers=h, json={"lines": _lines(("0.00", 5))})
    assert zero.status_code == 422

    negative_qty = client.post(url, headers=h, json={
        "lines": [{"denomination": "1.00", "quantity": -1}]})
    assert negative_qty.status_code == 422

    assert db.execute(text("SELECT count(*) FROM cash_counts")).scalar_one() == 0


# ---------------------------------------------------------------------------
# 6 · Histórico y cuadres: informe Z filtrable (reports.view)
# ---------------------------------------------------------------------------
def test_historico_de_sesiones(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    cash_id = _open(client, h, terminal).json()["id"]

    # Aún abierta: solo visible sin filtro o con status=open.
    listed = client.get(f"{CASH}/sessions", headers=h, params={"terminal_id": terminal})
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert listed.json()["items"][0]["status"] == "open"
    assert client.get(f"{CASH}/sessions", headers=h,
                      params={"status": "closed"}).json()["items"] == []

    client.post(f"{CASH}/sessions/{cash_id}/close", headers=h,
                json={"lines": _lines(("50.00", 1), ("1.00", 2))})  # 52,00 vs 50,00

    closed_list = client.get(f"{CASH}/sessions", headers=h, params={"status": "closed"})
    assert closed_list.status_code == 200
    [z] = closed_list.json()["items"]
    assert (z["id"], z["status"], z["expected_amount"],
            z["counted_amount"], z["difference"]) == (
        cash_id, "closed", "50.00", "52.00", "2.00")
    # El listado ordena de más reciente a más antigua.
    assert client.get(f"{CASH}/sessions", headers=h).json()["items"][0]["id"] == cash_id

    # Filtros por fecha de apertura (rango que la contiene / que ya pasó).
    within = client.get(f"{CASH}/sessions", headers=h,
                        params={"opened_from": "2020-01-01T00:00:00Z"})
    assert within.json()["total"] == 1
    past = client.get(f"{CASH}/sessions", headers=h,
                      params={"opened_to": "2020-01-01T00:00:00Z"})
    assert past.json()["items"] == []


# ---------------------------------------------------------------------------
# 7 · Permisos: abrir (cash.open) lo tiene el camarero; el resto no
# ---------------------------------------------------------------------------
def test_permisos_de_caja(db, client):
    waiter = _headers(db, client, perms=WAITER_PERMS, username="camarero")
    terminal = _mk_terminal(db)

    # Abrir y consultar la caja de su terminal: permitido con cash.open.
    opened = _open(client, waiter, terminal)
    assert opened.status_code == 201, opened.text
    cash_id = opened.json()["id"]
    assert client.get(f"{CASH}/sessions/current", headers=waiter,
                      params={"terminal_id": terminal}).status_code == 200
    assert client.get(f"{CASH}/sessions/{cash_id}/report", headers=waiter).status_code == 200

    # Movimientos exigen cash.movements.
    movement = client.post(f"{CASH}/sessions/{cash_id}/movements", headers=waiter, json={
        "kind": "in", "amount": "10.00", "reason": "sin permiso"})
    assert movement.status_code == 403 and movement.json()["code"] == "PERMISSION_DENIED"

    # Arqueos y cierre exigen cash.close.
    assert client.post(f"{CASH}/sessions/{cash_id}/counts", headers=waiter,
                       json={"lines": _lines(("50.00", 1))}).status_code == 403
    assert client.post(f"{CASH}/sessions/{cash_id}/close", headers=waiter,
                       json={"lines": _lines(("50.00", 1))}).status_code == 403

    # El histórico/cuadres exige reports.view.
    assert client.get(f"{CASH}/sessions", headers=waiter).status_code == 403

    # Y anónimo, nada.
    assert client.get(f"{CASH}/sessions/{cash_id}").status_code == 401

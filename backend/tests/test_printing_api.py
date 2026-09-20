"""Tests E2E de impresión (fase 10) contra PostgreSQL real.

Cubren FASE_10: CRUD de impresoras (red ESC/POS y agente, una default activa
por tipo), job de prueba, cola de ``PrintJob`` con despacho y confirmación,
reintentos controlados (backoff + MAX_ATTEMPTS + recuperación manual),
cancelación, encolado automático del ticket en el cobro (misma transacción,
idempotente por ``dedupe_key``; sin impresora configurada no rompe el cobro),
copias de tickets y facturas con el payload congelado, y permisos
``admin.printers``/``tickets.reprint``/``invoices.issue``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte
de tablas vacías. El adaptador se sustituye en ``app.state.print_adapter``
y se restaura al salir (el fixture ``app`` es session-scoped).
"""

import os
from decimal import Decimal
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.adapters.printing import FailingPrinterAdapter, NullPrinterAdapter
from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
SALES = f"{V1}/sales"
AUTH = f"{V1}/auth"
CASH = f"{V1}/cash"
DOCS = f"{V1}/documents"
ADMIN = f"{V1}/admin"
PRINT = f"{V1}/printing"
PRINTERS = f"{ADMIN}/printers"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

YEAR = date.today().year

# El encargado administra impresoras y cola, además de vender y facturar.
BOSS_PERMS = ("products.view", "products.edit", "sales.sell",
              "payments.take", "payments.refund", "cash.open",
              "tickets.reprint", "invoices.issue", "invoices.void",
              "admin.printers")
# El camarero reimprime tickets, pero no administra impresoras ni cola ni
# facturas.
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
def adapter(app):
    """Sustituye el adaptador de impresión registrado y lo restaura al salir
    (el fixture ``app`` es session-scoped: no podemos filtrar estado)."""

    original = app.state.print_adapter
    app.state.print_adapter = NullPrinterAdapter()
    yield app.state.print_adapter
    app.state.print_adapter = original


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


def _headers(db, client, perms=BOSS_PERMS, username="cajero") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _mk_terminal(db, code="T1") -> str:
    return str(db.execute(
        text("INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": f"Terminal {code}"},
    ).scalar_one())


def _mk_device(db, name="Agente TPV-1", terminal_id=None, active=True) -> str:
    return str(db.execute(
        text(
            "INSERT INTO devices (terminal_id, kind, name, token_hash, active) "
            "VALUES (:t, 'agent', :n, :h, :a) RETURNING id"
        ),
        {"t": terminal_id, "n": name, "h": f"hash-{name}", "a": active},
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
    return str(db.execute(
        text("INSERT INTO customers (name, tax_id) VALUES (:n, :t) RETURNING id"),
        {"n": name, "t": tax_id},
    ).scalar_one())


def _mk_printer(client, h, *, name="Tickets caja", kind="receipt", connection="network",
                address="192.168.1.50:9100", device_id=None, width=42, default=True) -> dict:
    resp = client.post(PRINTERS, headers=h, json={
        "name": name, "kind": kind, "connection": connection, "address": address,
        "device_id": device_id, "width_chars": width, "is_default": default,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mk_test_job(client, h, printer_id) -> dict:
    resp = client.post(f"{PRINTERS}/{printer_id}/test", headers=h)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _dispatch(client, h) -> list[dict]:
    resp = client.post(f"{PRINT}/dispatch", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


def _audit_actions(db, like="printing.%") -> set[str]:
    return {
        row[0] for row in db.execute(
            text("SELECT DISTINCT action FROM audit_log WHERE action LIKE :p"), {"p": like}
        )
    }


# -- venta (idéntico a test_documents_api.py) -------------------------------
def _tax_and_product(client, h, *, name="Café solo", price="1.50") -> tuple[dict, dict]:
    tax = client.post(f"{V1}/catalog/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{V1}/catalog/products", headers=h, json={
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


_cash_sessions: dict[str, str] = {}


def _cash_session_id(client, h, terminal_id) -> str:
    if terminal_id not in _cash_sessions:
        _cash_sessions[terminal_id] = _open(client, h, terminal_id)["id"]
    return _cash_sessions[terminal_id]


@pytest.fixture(autouse=True)
def _reset_cash_sessions():
    _cash_sessions.clear()
    yield
    _cash_sessions.clear()


def _close_sale(client, h, order_id, cash_session_id, payments) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})
    assert resp.status_code == 200, resp.text
    return resp.json()


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


# ---------------------------------------------------------------------------
# 1 · CRUD de impresoras: red y agente, una default activa por tipo
# ---------------------------------------------------------------------------
def test_crud_de_impresoras_red_y_agente(db, client):
    h = _headers(db, client)

    printer = _mk_printer(client, h)
    assert printer["kind"] == "receipt"
    assert printer["connection"] == "network"
    assert printer["address"] == "192.168.1.50:9100"
    assert printer["device_id"] is None
    assert printer["width_chars"] == 42
    assert printer["is_default"] is True and printer["active"] is True

    # Validaciones de conexión: la red exige address y no admite dispositivo…
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Sin address", "kind": "receipt", "connection": "network"})
    assert resp.status_code == 422
    device = _mk_device(db)
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Red con device", "kind": "receipt", "connection": "network",
        "address": "1.2.3.4:9100", "device_id": device})
    assert resp.status_code == 422
    # …y el agente exige device_id (existente y activo) sin address.
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Agente sin device", "kind": "kitchen", "connection": "agent"})
    assert resp.status_code == 422
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Agente con address", "kind": "kitchen", "connection": "agent",
        "device_id": device, "address": "1.2.3.4:9100"})
    assert resp.status_code == 422
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Agente device inactivo", "kind": "kitchen", "connection": "agent",
        "device_id": _mk_device(db, name="apagado", active=False)})
    assert resp.status_code == 422

    kitchen = _mk_printer(client, h, name="Cocina", kind="kitchen", connection="agent",
                          address=None, device_id=device, default=True)
    assert kitchen["connection"] == "agent"
    assert kitchen["address"] is None and kitchen["device_id"] == device
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Agente fantasma", "kind": "kitchen", "connection": "agent",
        "device_id": "00000000-0000-0000-0000-000000000000"})
    assert resp.status_code == 404

    # El ancho térmico solo admite 32/42/48.
    resp = client.post(PRINTERS, headers=h, json={
        "name": "Ancho raro", "kind": "receipt", "connection": "network",
        "address": "1.2.3.4:9100", "width_chars": 40})
    assert resp.status_code == 422

    # PATCH: renombrar, cambiar ancho y mover el default dentro del tipo.
    resp = client.patch(f"{PRINTERS}/{printer['id']}", headers=h,
                        json={"name": "Tickets barra", "width_chars": 48})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Tickets barra"
    assert resp.json()["width_chars"] == 48

    other = _mk_printer(client, h, name="Tickets 2", default=False)
    resp = client.patch(f"{PRINTERS}/{other['id']}", headers=h, json={"is_default": True})
    assert resp.status_code == 200
    listing = client.get(PRINTERS, headers=h).json()["items"]
    defaults = [p["name"] for p in listing if p["kind"] == "receipt" and p["is_default"]]
    assert defaults == ["Tickets 2"]  # solo una default activa por tipo

    # kind/connection inmutables: no se envían en el PATCH (se ignoran si llegan);
    # address solo tiene sentido en red.
    resp = client.patch(f"{PRINTERS}/{printer['id']}", headers=h,
                        json={"connection": "agent"})
    assert resp.status_code == 200
    assert resp.json()["connection"] == "network"  # ignorado, no mutado
    resp = client.patch(f"{PRINTERS}/{kitchen['id']}", headers=h,
                        json={"address": "1.2.3.4:9100"})
    assert resp.status_code == 422

    # DELETE = baja lógica: desaparece del listado activo, queda con include_inactive.
    resp = client.delete(f"{PRINTERS}/{other['id']}", headers=h)
    assert resp.status_code == 204
    active = client.get(PRINTERS, headers=h).json()["items"]
    assert other["id"] not in {p["id"] for p in active}
    everything = client.get(PRINTERS, headers=h, params={"include_inactive": True}).json()["items"]
    by_id = {p["id"]: p for p in everything}
    assert by_id[other["id"]]["active"] is False

    assert "printing.printer_created" in _audit_actions(db)
    assert "printing.printer_updated" in _audit_actions(db)
    assert "printing.printer_deactivated" in _audit_actions(db)


# ---------------------------------------------------------------------------
# 2 · Job de prueba y ciclo de cola: queued → sent → printed
# ---------------------------------------------------------------------------
def test_job_de_prueba_y_ciclo_de_cola(db, client, adapter):
    h = _headers(db, client)
    printer = _mk_printer(client, h)

    job = _mk_test_job(client, h, printer["id"])
    assert job["kind"] == "test"
    assert job["status"] == "queued"
    assert job["attempts"] == 0
    assert job["payload"]["printer"] == printer["name"]
    assert "printing.test_enqueued" in _audit_actions(db)

    listing = client.get(f"{PRINT}/jobs", headers=h,
                         params={"status": "queued"}).json()["items"]
    assert [j["id"] for j in listing] == [job["id"]]

    results = _dispatch(client, h)
    assert len(results) == 1
    assert results[0]["status"] == "sent"
    assert results[0]["attempts"] == 1
    # El adaptador NullPrinterAdapter registró la entrega física.
    assert [(str(d["job_id"]), str(d["printer_id"])) for d in adapter.deliveries] == \
        [(job["id"], printer["id"])]

    # Confirmación de impresión (llegará del agente en la fase 14).
    resp = client.post(f"{PRINT}/jobs/{job['id']}/confirm", headers=h)
    assert resp.status_code == 200, resp.text
    confirmed = resp.json()
    assert confirmed["status"] == "printed"
    assert confirmed["printed_at"] is not None

    # Cola vacía: despachar no hace nada y no falla.
    assert _dispatch(client, h) == []


# ---------------------------------------------------------------------------
# 3 · Reintentos controlados: backoff, MAX_ATTEMPTS y recuperación manual
# ---------------------------------------------------------------------------
def test_reintentos_controlados_con_backoff(db, client, app, adapter):
    h = _headers(db, client)
    printer = _mk_printer(client, h)
    job = _mk_test_job(client, h, printer["id"])
    job_url = f"{PRINT}/jobs/{job['id']}"

    app.state.print_adapter = FailingPrinterAdapter("Impresora no responde")
    try:
        # 1er intento: queued → sent → failed (error trazado, intento contado).
        assert _dispatch(client, h)[0]["status"] == "failed"
        failed = client.get(job_url, headers=h).json()
        assert failed["attempts"] == 1
        assert failed["last_error"] == "PRINTER_UNREACHABLE: Impresora no responde"

        # Backoff: reintentar sin esperar NO reenvía (2 s de espera para n=1).
        assert _dispatch(client, h) == []
        assert client.get(job_url, headers=h).json()["attempts"] == 1

        # Backoff vencido → reintento automático (intentos 2 y 3).
        db.execute(text(
            "UPDATE print_jobs SET sent_at = now() - interval '10 seconds' "
            "WHERE id = :i"), {"i": job["id"]})
        db.commit()
        assert _dispatch(client, h)[0]["attempts"] == 2
        db.execute(text(
            "UPDATE print_jobs SET sent_at = now() - interval '60 seconds' "
            "WHERE id = :i"), {"i": job["id"]})
        db.commit()
        assert _dispatch(client, h)[0]["attempts"] == 3

        # MAX_ATTEMPTS agotado: ningún reintento automático más, aunque pase tiempo.
        db.execute(text(
            "UPDATE print_jobs SET sent_at = now() - interval '1 hour' WHERE id = :i"),
            {"i": job["id"]})
        db.commit()
        assert _dispatch(client, h) == []
        assert client.get(job_url, headers=h).json()["status"] == "failed"
    finally:
        app.state.print_adapter = adapter  # el trabajo vuelve a entregarse

    # Recuperación manual: nuevo ciclo completo, auditado.
    resp = client.post(f"{job_url}/retry", headers=h)
    assert resp.status_code == 200, resp.text
    recovered = resp.json()
    assert recovered["status"] == "queued"
    assert recovered["attempts"] == 0

    assert _dispatch(client, h)[0]["status"] == "sent"
    assert client.post(f"{job_url}/confirm", headers=h).json()["status"] == "printed"

    actions = _audit_actions(db)
    assert "printing.job_retried" in actions
    assert "printing.test_enqueued" in actions


# ---------------------------------------------------------------------------
# 4 · Cancelación: pendientes y entregados; terminales intocables
# ---------------------------------------------------------------------------
def test_cancelacion_de_jobs(db, client, adapter):
    h = _headers(db, client)
    printer = _mk_printer(client, h)

    queued = _mk_test_job(client, h, printer["id"])
    resp = client.post(f"{PRINT}/jobs/{queued['id']}/cancel", headers=h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # Un cancelado no se despacha ni se re-cancela ni se confirma.
    assert _dispatch(client, h) == []
    assert client.post(f"{PRINT}/jobs/{queued['id']}/cancel", headers=h).status_code == 409
    assert client.post(f"{PRINT}/jobs/{queued['id']}/confirm", headers=h).status_code == 409

    sent = _mk_test_job(client, h, printer["id"])
    _dispatch(client, h)
    resp = client.post(f"{PRINT}/jobs/{sent['id']}/cancel", headers=h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Un impreso es terminal: ni cancelar ni reconfirmar.
    printed = _mk_test_job(client, h, printer["id"])
    _dispatch(client, h)
    client.post(f"{PRINT}/jobs/{printed['id']}/confirm", headers=h)
    assert client.post(f"{PRINT}/jobs/{printed['id']}/cancel", headers=h).status_code == 409

    assert "printing.job_cancelled" in _audit_actions(db)

    # 404 en operaciones sobre un job inexistente.
    ghost = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"{PRINT}/jobs/{ghost}", headers=h).status_code == 404
    assert client.post(f"{PRINT}/jobs/{ghost}/cancel", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# 5 · El cobro encola el ticket (misma transacción; sin impresora no rompe)
# ---------------------------------------------------------------------------
def test_el_cobro_encola_el_ticket_con_dedupe(db, client, adapter):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _mk_printer(client, h)  # default de tickets

    order, _, closed = _sale(client, h, db, terminal, method)
    ticket_id = closed["ticket"]["id"]

    jobs = client.get(f"{PRINT}/jobs", headers=h).json()["items"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == "ticket"
    assert job["dedupe_key"] == f"ticket:{ticket_id}"  # idempotente por documento
    assert job["payload"]["doc_number"] == "A-000001"
    assert job["payload"]["totals"]["total"] == "3.00"
    assert job["status"] == "queued"  # a la espera del despachador

    # La devolución también encola su ticket negativo (otro documento, otro job).
    resp = client.post(f"{SALES}/orders/{order['id']}/refund", headers=h, json={
        "cash_session_id": _cash_session_id(client, h, terminal),
        "reason": "Devolución de prueba",
        "lines": [{"line_id": _line_id(db, order["id"]), "quantity": "1"}],
        "payments": [_pay(method, "1.50")],
    })
    assert resp.status_code == 201, resp.text
    jobs = client.get(f"{PRINT}/jobs", headers=h).json()["items"]
    assert len(jobs) == 2  # uno por documento, sin duplicados
    assert {j["kind"] for j in jobs} == {"ticket"}


def _line_id(db, order_id) -> str:
    return str(db.execute(
        text("SELECT id FROM order_lines WHERE order_id = :o LIMIT 1"), {"o": order_id}
    ).scalar_one())


def test_sin_impresora_configurada_el_cobro_no_rompe(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)

    order, _, closed = _sale(client, h, db, terminal, method)
    assert closed["ticket"]["doc_number"] == "A-000001"  # el documento SÍ existe
    assert client.get(f"{PRINT}/jobs", headers=h).json()["items"] == []  # sin job, sin error


# ---------------------------------------------------------------------------
# 6 · Copias: payload congelado, sin dedupe; facturas anuladas no se copian
# ---------------------------------------------------------------------------
def test_copia_de_ticket_reenvia_el_payload_congelado(db, client, adapter):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    _mk_printer(client, h, name="Tickets")

    order, _, closed = _sale(client, h, db, terminal, method)
    ticket_id = closed["ticket"]["id"]
    _dispatch(client, h)  # el original ya salió; la copia es un job NUEVO

    resp = client.post(f"{PRINT}/copies/tickets/{ticket_id}", headers=h)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["kind"] == "ticket"
    assert copy["dedupe_key"] is None  # cada copia es un trabajo nuevo
    assert copy["status"] == "queued"
    assert copy["payload"]["doc_number"] == closed["ticket"]["doc_number"]
    # El close solo devuelve la referencia; el documento completo va por GET.
    full = client.get(f"{DOCS}/tickets/{ticket_id}", headers=h).json()
    assert copy["payload"] == full["payload"]  # congelado, no regenerado
    assert "printing.copy_enqueued" in _audit_actions(db)

    # Dos copias del mismo documento: dos jobs (sin dedupe).
    second = client.post(f"{PRINT}/copies/tickets/{ticket_id}", headers=h)
    assert second.status_code == 201
    assert second.json()["id"] != copy["id"]


def test_copia_de_factura_y_reglas(db, client, adapter):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    customer = _mk_customer(db)

    # Sin impresora de facturas configurada: la copia no puede hacer nada → 409.
    order, _, _ = _sale(client, h, db, terminal, method, customer_id=customer)
    resp = client.post(f"{DOCS}/invoices", headers=h, json={"order_ids": [order["id"]]})
    assert resp.status_code == 201, resp.text
    invoice = resp.json()
    resp = client.post(f"{PRINT}/copies/invoices/{invoice['id']}", headers=h)
    assert resp.status_code == 409

    invoice_printer = _mk_printer(client, h, name="Facturas", kind="invoice", default=True)
    resp = client.post(f"{PRINT}/copies/invoices/{invoice['id']}", headers=h)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["kind"] == "invoice"
    assert copy["printer_id"] == invoice_printer["id"]
    assert copy["payload"]["doc_number"] == f"FAC {YEAR}/000001"

    # Anulada: sin copias.
    resp = client.post(f"{DOCS}/invoices/{invoice['id']}/void", headers=h,
                       json={"reason": "Factura por error"})
    assert resp.status_code == 200, resp.text
    resp = client.post(f"{PRINT}/copies/invoices/{invoice['id']}", headers=h)
    assert resp.status_code == 409

    # 404 si el documento no existe.
    ghost = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"{PRINT}/copies/tickets/{ghost}", headers=h).status_code == 404
    assert client.post(f"{PRINT}/copies/invoices/{ghost}", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# 7 · Permisos: admin.printers (cola), tickets.reprint e invoices.issue (copias)
# ---------------------------------------------------------------------------
def test_permisos_de_impresion(db, client):
    boss = _headers(db, client, username="jefe")
    waiter = _headers(db, client, perms=WAITER_PERMS, username="camarero")
    printer = _mk_printer(client, boss, name="Tickets")

    # Sin admin.printers no se ve ni se mueve la cola ni se administran impresoras.
    for method, url in (
        ("get", PRINTERS), ("post", PRINTERS),
        ("get", f"{PRINT}/jobs"), ("post", f"{PRINT}/dispatch"),
        ("post", f"{PRINTERS}/{printer['id']}/test"),
        ("patch", f"{PRINTERS}/{printer['id']}"), ("delete", f"{PRINTERS}/{printer['id']}"),
    ):
        kwargs = {"headers": waiter}
        if method in ("post", "put", "patch"):  # get/delete no admiten cuerpo
            kwargs["json"] = {}
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 403, f"{method} {url}: {resp.text}"

    # Con admin.printers sí.
    assert client.get(PRINTERS, headers=boss).status_code == 200

    # Copia de ticket para quien reimprime; copia de factura exige invoices.issue.
    terminal = _mk_terminal(db)
    method_id = _mk_payment_method(db)
    order, _, closed = _sale(client, waiter, db, terminal, method_id)
    # El camarero SÍ tiene tickets.reprint y hay impresora receipt por defecto
    # (creada arriba): la copia se encola (201). El «409 sin impresora» solo
    # aplicaría sin destino configurado.
    resp = client.post(f"{PRINT}/copies/tickets/{closed['ticket']['id']}", headers=waiter)
    assert resp.status_code == 201, resp.text
    # El 403 real llega con un usuario sin tickets.reprint.
    no_reprint = _headers(db, client, perms=("products.view", "sales.sell"),
                          username="sinreprint")
    resp = client.post(f"{PRINT}/copies/tickets/{closed['ticket']['id']}", headers=no_reprint)
    assert resp.status_code == 403
    resp = client.post(f"{PRINT}/copies/invoices/{'00000000-0000-0000-0000-000000000000'}",
                       headers=waiter)
    assert resp.status_code == 403  # facturas: ni tocarlas sin invoices.issue

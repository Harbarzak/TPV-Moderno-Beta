"""Tests E2E del plugin fiscal (fase 34) contra PostgreSQL real.

Recorren el contrato ADR-010 de punta a punta: una venta cobrada en ``/sales``
deja su ``sale_event`` en el outbox y el plugin lo procesa solo vía
``POST /fiscal/dispatch`` — el motor de ventas no cambia ni conoce el plugin.

- Proveedor ``none`` (por defecto): consume eventos SIN crear documentos.
- Proveedor ``verifactu`` (esqueleto): crea documento + traza y el envío
  falla auditablemente con ``FISCAL_NOT_IMPLEMENTED``; reintentar no duplica
  documentos (UNIQUE por evento) y deja ``audit_log`` en cada descarga.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella).
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
FISCAL = f"{V1}/fiscal"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# Vender + montar el catálogo de prueba + consultar y descargar el plugin
# (manager de semilla trae lo fiscal; products.edit crea tax/product por API).
FISCAL_PERMS = ("products.view", "products.edit", "sales.sell", "payments.take",
                "fiscal.view", "fiscal.dispatch")


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


@pytest.fixture(scope="session")
def verifactu_app():
    """Segunda app con régimen «verifactu» (esqueleto) seleccionado."""
    if not TEST_DB_URL:
        pytest.skip("TPV_TEST_DATABASE_URL no definida: se omiten los tests de integración")
    return create_app(_settings(fiscal_provider="verifactu"))


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture()
def vf_client(verifactu_app):
    with TestClient(verifactu_app, raise_server_exceptions=False) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Fábricas mínimas (mismo patrón que test_kds_api.py / test_payments_api.py)
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


def _headers(db, client, perms=FISCAL_PERMS, username="jefe") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_terminal(db) -> str:
    code = f"T{uuid4().hex[:6].upper()}"
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


def _mk_payment_method(db) -> str:
    return str(db.execute(
        text(
            "INSERT INTO payment_methods (code, name, kind, opens_drawer) "
            "VALUES (:c, :n, 'cash', true) RETURNING id"
        ),
        {"c": f"CASH-{uuid4().hex[:6]}", "n": "Efectivo"},
    ).scalar_one())


def _sold_order(db, client, h) -> str:
    """Una venta cobrada: deja exactamente UN ``sale_closed`` en el outbox."""
    user_id = db.execute(text("SELECT id FROM users WHERE username = 'jefe'")).scalar_one()
    terminal = _mk_terminal(db)
    cash = _mk_cash_session(db, terminal, str(user_id))
    method = _mk_payment_method(db)
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": f"G{uuid4().hex[:8]}", "name": "IVA general",
        "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{CAT}/products", headers=h, json={
        "name": "Café solo", "tax_rate_id": tax["id"], "price": "1.50",
    }).json()
    order = client.post(f"{SALES}/orders", headers=h, json={"terminal_id": terminal}).json()
    client.post(f"{SALES}/orders/{order['id']}/lines", headers=h,
                json={"product_id": product["id"], "quantity": "2"})
    closed = client.post(
        f"{SALES}/orders/{order['id']}/close",
        headers={**h, "Idempotency-Key": str(uuid4())},
        json={"cash_session_id": cash, "payments": [{"payment_method_id": method, "amount": "3.00"}]},
    )
    assert closed.status_code == 200, closed.text
    return order["id"]


def _dispatch(client, h) -> dict:
    resp = client.post(f"{FISCAL}/dispatch", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1 · Proveedor «none»: consume sin certificar
# ---------------------------------------------------------------------------
def test_none_consume_eventos_sin_crear_documentos(db, client):
    h = _headers(db, client)
    _sold_order(db, client, h)

    status = client.get(f"{FISCAL}/status", headers=h).json()
    assert status["provider"] == "none"
    assert status["pending_sale_events"] == 1
    assert status["documents"] == {"pending": 0, "sent": 0, "accepted": 0,
                                   "rejected": 0, "cancelled": 0}

    report = _dispatch(client, h)
    assert (report["provider"], report["consumed"]) == ("none", 1)
    assert report["created"] == 0 and report["submitted"] == 0

    # La cola queda vacía y NO hay documentos: sin régimen no hay nada que certificar.
    status = client.get(f"{FISCAL}/status", headers=h).json()
    assert status["pending_sale_events"] == 0
    assert sum(status["documents"].values()) == 0
    dispatched_at = db.execute(
        text("SELECT dispatched_at FROM sale_events")
    ).scalars().all()
    assert dispatched_at and all(d is not None for d in dispatched_at)


def test_none_descarga_vacia_es_un_noop_auditable(db, client):
    h = _headers(db, client)
    report = _dispatch(client, h)
    assert report["consumed"] == 0
    rows = db.execute(
        text("SELECT after_data FROM audit_log WHERE action = 'fiscal.dispatch'")
    ).scalars().all()
    assert len(rows) == 1  # una descarga, un resumen en auditoría


# ---------------------------------------------------------------------------
# 2 · Proveedor «verifactu» (esqueleto): documento + traza + fallo auditable
# ---------------------------------------------------------------------------
def test_verifactu_crea_documento_y_rechaza_auditablemente(db, vf_client):
    h = _headers(db, vf_client)
    order_id = _sold_order(db, vf_client, h)

    report = _dispatch(vf_client, h)
    assert report["consumed"] == 1
    assert report["created"] == 1
    assert report["submitted"] == 1 and report["rejected"] == 1
    assert report["details"][0]["code"] == "FISCAL_NOT_IMPLEMENTED"

    # El documento nació, viajó y rebotó: queda «pending» (reencolable).
    docs = vf_client.get(f"{FISCAL}/documents", headers=h).json()
    assert docs["total"] == 1
    doc = docs["items"][0]
    assert (doc["doc_type"], doc["provider"]) == ("sale", "verifactu")
    assert doc["status"] == "pending"
    assert doc["attempts"] == 1
    assert doc["error_code"] == "FISCAL_NOT_IMPLEMENTED"
    assert doc["payload"]["doc_type"] == "sale"
    assert doc["payload"]["order_id"] == order_id
    assert doc["payload"]["totals"]["total"] == "3.00"   # dinero string intacto (§3)

    # Traza completa: queued → dispatched → rejected (mismo intento).
    detail = vf_client.get(f"{FISCAL}/documents/{doc['id']}", headers=h).json()
    assert [e["kind"] for e in detail["events"]] == ["queued", "dispatched", "rejected"]

    # Cada descarga deja su resumen en audit_log.
    audits = db.execute(
        text("SELECT action FROM audit_log WHERE action = 'fiscal.dispatch'")
    ).scalars().all()
    assert audits == ["fiscal.dispatch"]


def test_verifactu_reintento_no_duplica_documentos(db, vf_client):
    h = _headers(db, vf_client)
    _sold_order(db, vf_client, h)
    _dispatch(vf_client, h)

    # Segunda pasada: el evento ya está consumido (consumed=0), el documento
    # reencola por su UNIQUE y vuelve a rebotar con attempts=2.
    report = _dispatch(vf_client, h)
    assert report["consumed"] == 0 and report["created"] == 0
    assert report["submitted"] == 1 and report["rejected"] == 1

    docs = vf_client.get(f"{FISCAL}/documents", headers=h).json()
    assert docs["total"] == 1
    assert docs["items"][0]["attempts"] == 2


# ---------------------------------------------------------------------------
# 3 · Permisos
# ---------------------------------------------------------------------------
def test_sin_fiscal_view_no_se_consulta(db, client):
    h = _headers(db, client, perms=("sales.sell",), username="vendedor")
    resp = client.get(f"{FISCAL}/status", headers=h)
    assert resp.status_code == 403 and resp.json()["code"] == "PERMISSION_DENIED"


def test_sin_fiscal_dispatch_no_se_descarga(db, client):
    h = _headers(db, client, perms=("sales.sell", "fiscal.view"), username="consultor")
    resp = client.post(f"{FISCAL}/dispatch", headers=h)
    assert resp.status_code == 403 and resp.json()["code"] == "PERMISSION_DENIED"

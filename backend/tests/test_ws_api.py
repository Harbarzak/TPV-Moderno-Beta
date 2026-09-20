"""Tests E2E del hub WebSocket (fase 12, ARCHITECTURE.md §8) contra PostgreSQL real.

Cubren FASE_12: autenticación obligatoria en el primer frame (token ausente,
inválido o con la sesión revocada → close 4401; primer frame no-auth → 4400),
protocolo ready/subscribed/event/ping/error, autorización por tema (los temas
sin permiso van a ``subscribed.denied`` sin cortar la conexión), entrega en
tiempo real de eventos de venta, replay por ``since_id`` desde ``event_log``,
unsubscribe y latido ping/pong.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías. Latido y timeout de auth a 1 s para no ralentizar la suite.
"""

import os
from contextlib import contextmanager
from uuid import uuid4
from decimal import Decimal

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
SALES = f"{V1}/sales"
AUTH = f"{V1}/auth"
CASH = f"{V1}/cash"
WS = f"{V1}/ws"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# El encargado vende y abre caja (temas sales y terminal:*).
BOSS_PERMS = ("products.view", "products.edit", "sales.sell",
              "payments.take", "payments.refund", "cash.open")
# Vende pero no toca caja: el tema ``cash`` le debe llegar denegado.
SELLER_PERMS = ("products.view", "sales.sell", "payments.take")


def _settings(**overrides) -> Settings:
    base = dict(
        env="test",
        log_level="WARNING",
        database_url=TEST_DB_URL,
        db_null_pool=True,  # TestClient: un event loop por petición
        jwt_secret=SECRET,
        auth_rate_limit_attempts=50,
        ws_heartbeat_seconds=1,  # el ping de prueba llega en ~1 s
        ws_auth_timeout_seconds=1,
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
    return _bearer(_login(client, username).json()["access_token"])


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


# -- venta (idéntico a test_printing_api.py) ---------------------------------
def _tax_and_product(client, h, *, name="Café solo", price="1.50") -> tuple[dict, dict]:
    tax = client.post(f"{V1}/catalog/tax-rates", headers=h, json={
        # Código único por llamada: un test puede vender varias veces y el
        # código del tipo de IVA es único (una segunda «general» daría 409).
        "code": f"general-{uuid4().hex[:8]}", "name": "IVA general",
        "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    product = client.post(f"{V1}/catalog/products", headers=h, json={
        "name": name, "tax_rate_id": tax["id"], "price": price,
    }).json()
    return tax, product


def _mk_order(client, h, terminal_id) -> dict:
    resp = client.post(f"{SALES}/orders", headers=h, json={"terminal_id": terminal_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_line(client, h, order_id, product_id, quantity="2") -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/lines", headers=h,
                       json={"product_id": product_id, "quantity": quantity})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pay(method_id: str, amount: str) -> dict:
    return {"payment_method_id": method_id, "amount": amount}


_cash_sessions: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _reset_cash_sessions():
    _cash_sessions.clear()
    yield
    _cash_sessions.clear()


def _cash_session_id(client, h, terminal_id) -> str:
    if terminal_id not in _cash_sessions:
        resp = client.post(f"{CASH}/sessions", headers=h,
                           json={"terminal_id": terminal_id, "opening_amount": "50.00"})
        assert resp.status_code == 201, resp.text
        _cash_sessions[terminal_id] = resp.json()["id"]
    return _cash_sessions[terminal_id]


def _close_sale(client, h, order_id, cash_session_id, payments) -> dict:
    resp = client.post(f"{SALES}/orders/{order_id}/close",
                       headers={**h, "Idempotency-Key": str(uuid4())},
                       json={"cash_session_id": cash_session_id, "payments": payments})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _sale(client, h, terminal_id, method_id, *, quantity="2") -> tuple[dict, dict, dict]:
    """Venta cobrada de ``quantity × 1.50``: (orden, línea, respuesta de cobro)."""
    _, product = _tax_and_product(client, h)
    order = _mk_order(client, h, terminal_id)
    line = _add_line(client, h, order["id"], product["id"], quantity=quantity)
    closed = _close_sale(
        client, h, order["id"],
        cash_session_id=_cash_session_id(client, h, terminal_id),
        payments=[_pay(method_id, format(Decimal("1.50") * Decimal(quantity), "f"))],
    )
    return order, line, closed


# -- WebSocket ---------------------------------------------------------------
@contextmanager
def _connect(client, token=None, first_frame=None):
    """Abre el WS, autentica (o envía ``first_frame`` en su lugar) y entrega la
    sesión de prueba tras consumir el ``ready``. Uso: ``with _connect(...) as ws``."""

    with client.websocket_connect(WS) as ws:
        if first_frame is not None:
            ws.send_json(first_frame)
        else:
            ws.send_json({"type": "auth", "token": token})
        ws.receive_json()  # ready
        yield ws


def _recv(ws, want: str, tries: int = 10) -> dict:
    """Lee frames hasta encontrar el tipo pedido (los ping se saltan)."""
    for _ in range(tries):
        frame = ws.receive_json()
        if frame.get("type") == want:
            return frame
    raise AssertionError(f"No llegó ningún frame «{want}»: últimos vistos sin él")


def _close_code(ws, *, send_first: dict | None = None) -> int:
    """Espera el cierre del servidor y devuelve su código."""
    if send_first is not None:
        ws.send_json(send_first)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        while True:
            ws.receive_json()
    return exc_info.value.code


# ---------------------------------------------------------------------------
# 1 · Autenticación: sin token válido no hay socket
# ---------------------------------------------------------------------------
def test_sin_auth_o_token_invalido_cierra_4401(client):
    # Timeout de auth: conecta y no envía nada.
    with client.websocket_connect(WS) as ws:
        assert _close_code(ws) == 4401

    # Token basura: falla la firma/decodificación.
    with client.websocket_connect(WS) as ws:
        assert _close_code(ws, send_first={"type": "auth", "token": "no-es-un-jwt"}) == 4401


def test_sesion_revocada_cierra_4401(db, client):
    h = _headers(db, client)
    token = _login(client).json()["access_token"]
    db.execute(text("UPDATE user_sessions SET revoked_at = now()"))
    db.commit()

    with client.websocket_connect(WS) as ws:
        assert _close_code(ws, send_first={"type": "auth", "token": token}) == 4401


def test_primer_frame_sin_auth_cierra_4400(client):
    with client.websocket_connect(WS) as ws:
        assert _close_code(
            ws, send_first={"type": "subscribe", "topics": ["sales"]}
        ) == 4400


# ---------------------------------------------------------------------------
# 2 · Protocolo: subscribe, entrega en vivo, error y latido
# ---------------------------------------------------------------------------
def test_suscripcion_recibe_los_eventos_de_venta_en_vivo(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    token = _login(client).json()["access_token"]

    with _connect(client, token) as ws:
        ws.send_json({"type": "subscribe", "topics": ["sales"]})
        sub = ws.receive_json()
        assert sub["type"] == "subscribed"
        assert sub["topics"] == ["sales"]
        assert sub["denied"] == [] and sub["replayed"] == 0

        order, _, closed = _sale(client, h, terminal, method)

        created = _recv(ws, "event")["event"]
        assert created["topic"] == "sales" and created["type"] == "sales.created"
        assert created["payload"]["order_id"] == order["id"]
        assert created["payload"]["terminal_id"] == terminal
        assert created["payload"]["status"] == "draft"
        assert created["actor_user_id"]  # el cajero que la creó
        assert created["occurred_at"] and created["event_id"]

        updated = _recv(ws, "event")["event"]  # la línea: sales.updated
        assert updated["type"] == "sales.updated"

        closed_ev = _recv(ws, "event")["event"]
        assert closed_ev["type"] == "sales.closed"
        assert closed_ev["payload"]["total"] == "3.00"  # dinero string (§3)
        assert closed_ev["payload"]["cash_session_id"] == _cash_sessions[terminal]
        assert int(closed_ev["id"]) > int(created["id"])  # cursor creciente


def test_frame_desconocido_responde_error_y_la_conexion_sigue(db, client):
    h = _headers(db, client)
    token = _login(client).json()["access_token"]

    with _connect(client, token) as ws:
        ws.send_json({"type": "voladores"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "WS_UNKNOWN_FRAME"

        # La conexión sigue viva: el latido llega aunque no haya eventos.
        ws.send_json({"type": "ping"})  # el pong del cliente no rompe nada
        assert _recv(ws, "ping")["type"] == "ping"  # latido del servidor (1 s)


def test_unsubscribe_deja_de_entregar(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    token = _login(client).json()["access_token"]

    with _connect(client, token) as ws:
        ws.send_json({"type": "subscribe", "topics": ["sales"]})
        assert ws.receive_json()["type"] == "subscribed"
        ws.send_json({"type": "unsubscribe", "topics": ["sales"]})
        off = ws.receive_json()
        assert off["type"] == "unsubscribed" and off["topics"] == ["sales"]

        _sale(client, h, terminal, method)
        # Nada de ventas en cola: el siguiente frame es el ping del latido.
        assert ws.receive_json()["type"] == "ping"


# ---------------------------------------------------------------------------
# 3 · Autorización por tema: denegar no corta la conexión
# ---------------------------------------------------------------------------
def test_temas_sin_permiso_van_a_denied_y_el_resto_funciona(db, client):
    h = _headers(db, client, perms=SELLER_PERMS, username="camarero")
    token = _login(client, "camarero").json()["access_token"]

    with _connect(client, token) as ws:
        ws.send_json({"type": "subscribe", "topics": ["cash", "sales", "voladores"]})
        sub = ws.receive_json()
        assert sub["type"] == "subscribed"
        assert sub["topics"] == ["sales"]  # concedidos, en orden
        assert sub["denied"] == ["cash", "voladores"]


def test_system_esta_abierto_a_cualquier_autenticado(db, client):
    h = _headers(db, client, perms=("products.view",), username="mirador")
    token = _login(client, "mirador").json()["access_token"]

    with _connect(client, token) as ws:
        ws.send_json({"type": "subscribe", "topics": ["system"]})
        sub = ws.receive_json()
        assert sub["type"] == "subscribed" and sub["topics"] == ["system"]


# ---------------------------------------------------------------------------
# 4 · Replay: ``since_id`` repasa ``event_log`` y el cursor sigue al vivo
# ---------------------------------------------------------------------------
def test_replay_desde_since_id_y_continuidad_del_cursor(db, client):
    h = _headers(db, client)
    terminal = _mk_terminal(db)
    method = _mk_payment_method(db)
    token = _login(client).json()["access_token"]

    order, _, _ = _sale(client, h, terminal, method)  # created + updated + closed

    with _connect(client, token) as ws:
        ws.send_json({"type": "subscribe", "topics": ["sales"], "since_id": 0})
        # Llegan primero los eventos históricos y al final el ``subscribed``.
        replayed = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "subscribed":
                summary = frame
                break
            assert frame["type"] == "event"
            replayed.append(frame["event"])

        assert [ev["type"] for ev in replayed] == [
            "sales.created", "sales.updated", "sales.closed",
        ]
        assert all(ev["payload"]["order_id"] == order["id"] for ev in replayed)
        assert summary["topics"] == ["sales"] and summary["denied"] == []
        assert summary["replayed"] == 3
        assert summary["cursor"] == replayed[-1]["id"]

        # El cursor queda a la altura del log: lo nuevo llega en vivo, sin hueco.
        order2, _, _ = _sale(client, h, terminal, method)
        live = _recv(ws, "event")["event"]
        assert live["type"] == "sales.created"
        assert live["payload"]["order_id"] == order2["id"]
        assert int(live["id"]) > int(summary["cursor"])

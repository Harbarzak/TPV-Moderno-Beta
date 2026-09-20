"""Tests E2E del KDS (fase 32) contra PostgreSQL real.

Las comandas NACEN en ``/sales`` (añadir un producto ``kitchen=true`` a un
borrador crea la comanda en la MISMA transacción): aquí se prueban el tablero
(``GET /kds/board``), los toques de línea con cabecera derivada, el «todo
listo» masivo, la prioridad, la sincronización de rectificaciones, la
cancelación por retirada/anulación, las estaciones y la reimpresión KOT.
El permiso ``kds.operate`` manda en todo el módulo.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte
de tablas vacías. Las cantidades viajan como string «X.XXXX» (§3).
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
KDS = f"{V1}/kds"
PRINTERS = f"{V1}/admin/printers"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# Vender (para crear comandas) + operar el KDS. La reimpresión KOT añade
# ``admin.printers`` solo donde se crea la impresora.
KDS_PERMS = ("products.view", "products.edit", "sales.sell", "sales.void", "kds.operate")


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


def _headers(db, client, perms=KDS_PERMS, username="jefe") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _mk_terminal(db, code=None) -> str:
    # Código único: algún test crea varios terminales (uq_terminals_code).
    code = code or f"T{uuid4().hex[:6].upper()}"
    return str(db.execute(
        text("INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": f"Terminal {code}"},
    ).scalar_one())


def _mk_table(db, name="M1") -> str:
    zone_id = db.execute(
        text("INSERT INTO zones (name) VALUES ('Sala') RETURNING id")
    ).scalar_one()
    return str(db.execute(
        text("INSERT INTO dining_tables (zone_id, name, seats) VALUES (:z, :n, 4) RETURNING id"),
        {"z": zone_id, "n": name},
    ).scalar_one())


def _kitchen_product(client, h, station_id=None, *, kitchen=True,
                     name="Tortilla de patatas", price="6.00") -> dict:
    """Producto de catálogo (vía API): de cocina o no, con estación opcional."""
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": f"G{uuid4().hex[:8]}", "name": "IVA reducido",
        "rate": "10.00", "valid_from": "2026-01-01",
    }).json()
    resp = client.post(f"{CAT}/products", headers=h, json={
        "name": name, "tax_rate_id": tax["id"], "price": price,
        "kitchen": kitchen, "kitchen_station_id": station_id,
    })
    assert resp.status_code == 200, resp.text  # el catálogo responde 200
    return resp.json()


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


def _mk_kitchen_draft(db, client, h, *, station_id=None, kitchen=True, quantity="1",
                      lines=1, **order_extra) -> tuple[dict, dict, list[dict]]:
    """Atajo: terminal + producto (de cocina o no) + borrador con ``lines`` líneas."""
    terminal = _mk_terminal(db)
    product = _kitchen_product(client, h, station_id, kitchen=kitchen)
    order = _mk_order(client, h, terminal, **order_extra)
    sales_lines = [
        _add_line(client, h, order["id"],
                  {"product_id": product["id"], "quantity": quantity})
        for _ in range(lines)
    ]
    return order, product, sales_lines


def _board(client, h) -> list[dict]:
    resp = client.get(f"{KDS}/board", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _tap(client, h, line_id, status: str):
    return client.patch(f"{KDS}/lines/{line_id}/status", headers=h, json={"status": status})


def _kds_events(db) -> list[str]:
    return db.execute(
        text("SELECT type FROM event_log WHERE topic = 'kds' ORDER BY id")
    ).scalars().all()


# ---------------------------------------------------------------------------
# 1 · Nacimiento de comandas (productor en /sales)
# ---------------------------------------------------------------------------
def test_producto_de_cocina_crea_comanda_en_el_tablero(db, client):
    h = _headers(db, client)
    station = client.post(f"{KDS}/stations", headers=h, json={"name": "Plancha"})
    assert station.status_code == 201, station.text
    station = station.json()
    order, _, sales_line = _mk_kitchen_draft(db, client, h, station_id=station["id"],
                                             quantity="2")

    board = _board(client, h)
    assert len(board) == 1
    ticket = board[0]
    assert ticket["order_id"] == order["id"]
    assert ticket["status"] == "pending"
    assert ticket["priority"] == 0
    assert ticket["order_type"] == order["order_type"]
    assert ticket["table_name"] is None
    assert ticket["ready_at"] is None and ticket["served_at"] is None
    assert len(ticket["lines"]) == 1
    kline = ticket["lines"][0]
    assert kline["order_line_id"] == sales_line[0]["id"]
    assert kline["name"] == "Tortilla de patatas"
    assert kline["quantity"] == "2.000"
    assert kline["status"] == "pending"
    assert kline["station_id"] == station["id"]  # snapshot de la estación
    # El productor dejó rastro en event_log (mismo commit que la venta).
    events = _kds_events(db)
    assert "kds.ticket_created" in events
    assert "kds.line_added" in events


def test_producto_de_mesa_muestra_su_mesa_en_el_tablero(db, client):
    h = _headers(db, client)
    table = _mk_table(db)
    order, _, _ = _mk_kitchen_draft(
        db, client, h, order_type="restaurant", dining_table_id=table
    )
    ticket = _board(client, h)[0]
    assert ticket["table_name"] == "M1"


def test_producto_sin_cocina_no_genera_comanda(db, client):
    h = _headers(db, client)
    _mk_kitchen_draft(db, client, h, kitchen=False)
    assert _board(client, h) == []
    assert _kds_events(db) == []


# ---------------------------------------------------------------------------
# 2 · Toques de línea y cabecera derivada
# ---------------------------------------------------------------------------
def test_flujo_de_linea_con_cabecera_derivada_y_pasos_atras(db, client):
    h = _headers(db, client)
    _, _, sales_lines = _mk_kitchen_draft(db, client, h, quantity="1")
    ticket = _board(client, h)[0]
    line_id = ticket["lines"][0]["id"]

    # NUEVO → PREPARANDO.
    r = _tap(client, h, line_id, "preparing")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "preparing"
    # PREPARANDO → LISTO: la cabecera llega sola y estampa ready_at.
    body = _tap(client, h, line_id, "ready").json()
    assert body["status"] == "ready" and body["ready_at"] is not None
    # Paso atrás LISTO → PREPARANDO: ready_at se limpia.
    body = _tap(client, h, line_id, "preparing").json()
    assert body["status"] == "preparing" and body["ready_at"] is None
    # Salto directo NUEVO... bueno, PREPARANDO → LISTO otra vez, y servicio.
    _tap(client, h, line_id, "ready")
    body = _tap(client, h, line_id, "served").json()
    assert body["status"] == "served"
    assert body["served_at"] is not None and body["ready_at"] is not None
    # Ya no está entre las abiertas, pero sí en la ventana de servidas.
    board = _board(client, h)
    assert [t["id"] for t in board] == [ticket["id"]]
    assert board[0]["status"] == "served"
    # Comanda cerrada: ningún toque más (ni hacia atrás).
    r = _tap(client, h, line_id, "ready")
    assert r.status_code == 409, r.text


def test_doble_toque_al_mismo_estado_es_no_op_exitoso(db, client):
    h = _headers(db, client)
    _, _, sales_lines = _mk_kitchen_draft(db, client, h)
    line_id = _board(client, h)[0]["lines"][0]["id"]
    r = _tap(client, h, line_id, "pending")
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["status"] == "pending"


def test_salto_nuevo_a_servido_en_linea_devuelve_409(db, client):
    h = _headers(db, client)
    _, _, _ = _mk_kitchen_draft(db, client, h)
    line_id = _board(client, h)[0]["lines"][0]["id"]
    r = _tap(client, h, line_id, "served")
    assert r.status_code == 409, r.text


def test_todo_listo_masivo_y_luego_servido(db, client):
    h = _headers(db, client)
    _, _, _ = _mk_kitchen_draft(db, client, h, lines=2)
    ticket = _board(client, h)[0]

    r = client.patch(f"{KDS}/tickets/{ticket['id']}/status", headers=h,
                     json={"status": "ready"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready" and body["ready_at"] is not None
    assert all(line["status"] == "ready" for line in body["lines"])

    r = client.patch(f"{KDS}/tickets/{ticket['id']}/status", headers=h,
                     json={"status": "served"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "served" and body["served_at"] is not None
    assert all(line["status"] == "served" for line in body["lines"])


def test_servido_masivo_desde_nuevo_devuelve_409(db, client):
    h = _headers(db, client)
    _, _, _ = _mk_kitchen_draft(db, client, h)
    ticket = _board(client, h)[0]
    r = client.patch(f"{KDS}/tickets/{ticket['id']}/status", headers=h,
                     json={"status": "served"})
    assert r.status_code == 409, r.text


def test_prioridad_urgente_reordena_el_tablero(db, client):
    h = _headers(db, client)
    _, _, _ = _mk_kitchen_draft(db, client, h)  # primera comanda (más antigua)
    _, _, _ = _mk_kitchen_draft(db, client, h)
    board = _board(client, h)
    assert len(board) == 2
    segunda = board[1]["id"]

    r = client.patch(f"{KDS}/tickets/{segunda}/priority", headers=h,
                     json={"priority": 1})
    assert r.status_code == 204, r.text
    board = _board(client, h)
    assert board[0]["id"] == segunda  # urgente primero aunque sea más nueva
    assert board[0]["priority"] == 1

    # Fuera de rango: 422 de validación, no 500.
    r = client.patch(f"{KDS}/tickets/{segunda}/priority", headers=h,
                     json={"priority": 5})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 3 · Rectificaciones y retiradas desde /sales
# ---------------------------------------------------------------------------
def test_rectificar_cantidad_sincroniza_hasta_que_la_cocina_termina(db, client):
    h = _headers(db, client)
    order, _, sales_line = _mk_kitchen_draft(db, client, h, quantity="2")
    line_url = f"{SALES}/orders/{order['id']}/lines/{sales_line[0]['id']}"

    # En NUEVO: la rectificación llega al tablero.
    r = client.patch(line_url, headers=h, json={"quantity": "3"})
    assert r.status_code == 200, r.text
    assert _board(client, h)[0]["lines"][0]["quantity"] == "3.000"

    # En PREPARANDO también: aún se está cocinando, mejor saberlo en pantalla.
    line_id = _board(client, h)[0]["lines"][0]["id"]
    _tap(client, h, line_id, "preparing")
    r = client.patch(line_url, headers=h, json={"quantity": "5"})
    assert r.status_code == 200, r.text
    assert _board(client, h)[0]["lines"][0]["quantity"] == "5.000"

    # Ya LISTO: lo cocinado no se reescribe.
    _tap(client, h, line_id, "ready")
    r = client.patch(line_url, headers=h, json={"quantity": "7"})
    assert r.status_code == 200, r.text
    assert _board(client, h)[0]["lines"][0]["quantity"] == "5.000"


def test_quitar_la_unica_linea_pendiente_borra_la_comanda(db, client):
    h = _headers(db, client)
    order, _, sales_line = _mk_kitchen_draft(db, client, h)
    assert len(_board(client, h)) == 1

    r = client.delete(f"{SALES}/orders/{order['id']}/lines/{sales_line[0]['id']}",
                      headers=h)
    assert r.status_code == 204, r.text
    assert _board(client, h) == []
    assert "kds.line_cancelled" in _kds_events(db)


def test_quitar_una_de_dos_lineas_deja_la_comanda_viva(db, client):
    h = _headers(db, client)
    order, _, sales_lines = _mk_kitchen_draft(db, client, h, lines=2)
    r = client.delete(f"{SALES}/orders/{order['id']}/lines/{sales_lines[0]['id']}",
                      headers=h)
    assert r.status_code == 204, r.text
    board = _board(client, h)
    assert len(board) == 1
    assert len(board[0]["lines"]) == 1
    assert board[0]["lines"][0]["order_line_id"] == sales_lines[1]["id"]


def test_linea_en_marcha_retirada_desaparece_y_cierra_la_comanda(db, client):
    h = _headers(db, client)
    order, _, sales_line = _mk_kitchen_draft(db, client, h)
    line_id = _board(client, h)[0]["lines"][0]["id"]
    _tap(client, h, line_id, "preparing")

    r = client.delete(f"{SALES}/orders/{order['id']}/lines/{sales_line[0]['id']}",
                      headers=h)
    assert r.status_code == 204, r.text
    # Ya empezada: la línea de comanda desaparece con la de venta (la FK no es
    # cascade a propósito) y la comanda se borra; el rastro queda en event_log.
    assert _board(client, h) == []
    assert "kds.line_cancelled" in _kds_events(db)


def test_anular_la_venta_cancela_la_comanda(db, client):
    h = _headers(db, client)
    order, _, _ = _mk_kitchen_draft(db, client, h)
    line_id = _board(client, h)[0]["lines"][0]["id"]
    _tap(client, h, line_id, "preparing")

    r = client.post(f"{SALES}/orders/{order['id']}/void", headers=h,
                    json={"reason": "cobrado por error"})
    assert r.status_code == 200, r.text
    assert _board(client, h) == []
    assert "kds.ticket_cancelled" in _kds_events(db)


# ---------------------------------------------------------------------------
# 4 · Estaciones
# ---------------------------------------------------------------------------
def test_estaciones_crud_y_filtro_de_inactivas(db, client):
    h = _headers(db, client)
    frio = client.post(f"{KDS}/stations", headers=h, json={"name": "Frío", "sort_order": 1})
    plancha = client.post(f"{KDS}/stations", headers=h, json={"name": "Plancha", "sort_order": 2})
    assert frio.status_code == 201 and plancha.status_code == 201

    lista = client.get(f"{KDS}/stations", headers=h).json()["items"]
    assert {s["name"] for s in lista} == {"Frío", "Plancha"}

    # Renombrar y desactivar: fuera del listado por defecto.
    r = client.patch(f"{KDS}/stations/{frio.json()['id']}", headers=h,
                     json={"name": "Cámara", "active": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Cámara" and body["active"] is False

    activas = client.get(f"{KDS}/stations", headers=h).json()["items"]
    assert [s["name"] for s in activas] == ["Plancha"]
    todas = client.get(f"{KDS}/stations", headers=h,
                       params={"include_inactive": True}).json()["items"]
    assert {s["name"] for s in todas} == {"Cámara", "Plancha"}


# ---------------------------------------------------------------------------
# 5 · Impresión KOT
# ---------------------------------------------------------------------------
def test_reimpresion_kot_sin_impresora_de_cocina_devuelve_409(db, client):
    h = _headers(db, client)
    _, _, _ = _mk_kitchen_draft(db, client, h)
    ticket = _board(client, h)[0]
    r = client.post(f"{KDS}/tickets/{ticket['id']}/print", headers=h)
    assert r.status_code == 409, r.text


def test_reimpresion_kot_con_impresora_encola_el_ticket_completo(db, client):
    h = _headers(db, client, perms=KDS_PERMS + ("admin.printers",))
    printer = client.post(PRINTERS, headers=h, json={
        "name": "Cocina", "kind": "kitchen", "connection": "network",
        "address": "192.168.1.60:9100", "width_chars": 42, "is_default": True,
    })
    assert printer.status_code == 201, printer.text
    _, _, sales_lines = _mk_kitchen_draft(db, client, h, quantity="2")
    ticket = _board(client, h)[0]

    r = client.post(f"{KDS}/tickets/{ticket['id']}/print", headers=h)
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["kind"] == "kitchen"
    assert job["payload"]["kind"] == "kot"
    assert job["payload"]["kitchen_order_id"] == ticket["id"]
    assert [line["name"] for line in job["payload"]["lines"]] == ["Tortilla de patatas"]
    assert job["payload"]["lines"][0]["quantity"] == "2.000"
    assert "kds.ticket_printed" in _kds_events(db)


# ---------------------------------------------------------------------------
# 6 · Permisos: el módulo entero tras ``kds.operate``
# ---------------------------------------------------------------------------
def test_el_modulo_kds_exige_su_permiso_propio(db, client):
    h = _headers(db, client, perms=("products.view", "sales.sell"), username="sin_kds")
    assert client.get(f"{KDS}/board", headers=h).status_code == 403
    assert client.post(f"{KDS}/stations", headers=h, json={"name": "X"}).status_code == 403

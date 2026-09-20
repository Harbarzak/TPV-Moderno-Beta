"""Tests E2E del modo restaurante (fase 30) contra PostgreSQL real.

Cubren la configuración de sala (zonas y mesas con plano 2D), los estados
derivados del semáforo (libre / abierta / «cuenta pedida»), la sesión de mesa
(borrador tipo ``restaurant`` reutilizando ``/sales`` para las líneas), la
apertura idempotente, el traspaso, la juntada (líneas movidas + origen
anulado con motivo) y la división de cuenta (líneas enteras y parciales),
más permisos ``restaurant.operate`` y la forma del sondeo móvil.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte
de tablas vacías. El dinero y las posiciones viajan como string (§3).
"""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.security import hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
REST = f"{V1}/restaurant"
SALES = f"{V1}/sales"
AUTH = f"{V1}/auth"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# El mapa opera la sala (restaurant.operate) y vende en mesa (líneas /sales).
REST_PERMS = ("products.view", "sales.sell", "restaurant.operate")


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


def _headers(db, client, perms=REST_PERMS, username="jefe") -> dict:
    role_id = _mk_role(db, f"role-{username}", perms)
    _mk_user(db, role_id, username)
    return _bearer(_login(client, username).json()["access_token"])


def _mk_terminal(db, code="T1") -> str:
    return str(db.execute(
        text("INSERT INTO terminals (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": f"Terminal {code}"},
    ).scalar_one())


def _mk_zone(db, client, h, name="Sala", sort_order=0) -> dict:
    resp = client.post(f"{REST}/zones", headers=h,
                       json={"name": name, "sort_order": sort_order})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mk_table(db, client, h, zone_id, name="M1", seats=4, **extra) -> dict:
    payload = {"zone_id": zone_id, "name": name, "seats": seats, **extra}
    resp = client.post(f"{REST}/tables", headers=h, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _mk_product(db, name="Café", price="2.00", rate="21.00") -> str:
    tax_id = db.execute(
        text(
            "INSERT INTO tax_rates (code, name, rate, valid_from) "
            "VALUES ('general', 'IVA general', :r, DATE '2026-01-01') "
            "ON CONFLICT (code, valid_from) DO UPDATE SET code = EXCLUDED.code "
            "RETURNING id"
        ),
        {"r": rate},
    ).scalar_one()
    return str(db.execute(
        text(
            "INSERT INTO products (name, tax_rate_id, price, active) "
            "VALUES (:n, :t, :p, true) RETURNING id"
        ),
        {"n": name, "t": tax_id, "p": price},
    ).scalar_one())


def _open(client, h, table_id, terminal_id, **extra) -> dict:
    resp = client.post(
        f"{REST}/tables/{table_id}/open",
        headers={**h, "Idempotency-Key": str(uuid4())},
        json={"terminal_id": terminal_id, **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_line(client, h, order_id, product_id, quantity="1") -> dict:
    resp = client.post(
        f"{SALES}/orders/{order_id}/lines",
        headers=h,
        json={"product_id": product_id, "quantity": quantity},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _floor(client, h) -> dict:
    """El plano indexado por nombre de mesa (varias zonas pueden repetir)."""
    resp = client.get(f"{REST}/tables", headers=h)
    assert resp.status_code == 200, resp.text
    return {item["name"]: item for item in resp.json()["items"]}


def _zone_tables(items: list[dict], zone_name: str) -> dict:
    return {i["name"]: i for i in items if i["zone_name"] == zone_name}


def _restaurant_actions(db) -> list[str]:
    return db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'restaurant.%' ORDER BY id")
    ).scalars().all()


# ---------------------------------------------------------------------------
# Configuración de sala: zonas y mesas
# ---------------------------------------------------------------------------
def test_zonas_y_mesas_configuracion(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)

    terraza = _mk_zone(db, client, h, name="Terraza", sort_order=2)
    sala = _mk_zone(db, client, h, name="Sala", sort_order=1)
    assert terraza["active"] is True and sala["sort_order"] == 1

    # Mesa colocada en el plano: posiciones como string, estado derivado libre.
    m1 = _mk_table(db, client, h, sala["id"], name="M1", seats=4,
                   pos_x="25.50", pos_y="40")
    assert m1["pos_x"] == "25.50" and Decimal(m1["pos_y"]) == Decimal("40")
    assert m1["status"] == "free" and m1["order_id"] is None

    # Sin colocar (NULL): aparece en la zona pero el mapa no la dibuja.
    barra = _mk_table(db, client, h, terraza["id"], name="Barra")
    assert barra["pos_x"] is None and barra["pos_y"] is None

    # UNIQUE (zone_id, name): 409 propio, nunca 500 del IntegrityError.
    dup = client.post(f"{REST}/tables", headers=h,
                      json={"zone_id": sala["id"], "name": "M1", "seats": 2})
    assert dup.status_code == 409, dup.text

    # Zona inexistente o inactiva: 404 / 409 del contrato.
    ghost = client.post(f"{REST}/tables", headers=h,
                        json={"zone_id": str(uuid4()), "name": "X", "seats": 2})
    assert ghost.status_code == 404, ghost.text

    # PATCH: renombrar, mover en el plano, cambiar aforo y de zona.
    moved = client.patch(f"{REST}/tables/{m1['id']}", headers=h,
                         json={"name": "M1-bis", "seats": 6,
                               "pos_x": "10.00", "pos_y": "90.00"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["name"] == "M1-bis" and moved.json()["seats"] == 6
    to_terraza = client.patch(f"{REST}/tables/{m1['id']}", headers=h,
                              json={"zone_id": terraza["id"]})
    assert to_terraza.status_code == 200, to_terraza.text
    assert to_terraza.json()["zone_id"] == terraza["id"]

    # Desactivar zona con mesas activas: 409; sin mesas: 200.
    blocked = client.patch(f"{REST}/zones/{terraza['id']}", headers=h,
                           json={"active": False})
    assert blocked.status_code == 409, blocked.text
    ok = client.patch(f"{REST}/zones/{sala['id']}", headers=h,
                      json={"active": False})
    assert ok.status_code == 200, ok.text
    assert ok.json()["active"] is False

    # El listado oculta inactivas por defecto; con include_inactive vuelven.
    items = client.get(f"{REST}/tables", headers=h).json()["items"]
    assert _zone_tables(items, "Sala") == {}
    everything = client.get(f"{REST}/tables?include_inactive=true",
                            headers=h).json()["items"]
    # Sala quedó inactiva y vacía; M1-bis (movida) sigue en Terraza activa.
    assert set(_zone_tables(everything, "Terraza")) == {"M1-bis", "Barra"}

    zones = client.get(f"{REST}/zones?include_inactive=true", headers=h).json()["items"]
    assert [z["name"] for z in zones] == ["Sala", "Terraza"]  # sort_order

    # Auditoría de sala con acciones propias del módulo.
    actions = _restaurant_actions(db)
    assert "restaurant.zone_created" in actions
    assert "restaurant.table_created" in actions
    assert "restaurant.table_updated" in actions


def test_estado_del_plano_libre_abierta_y_cuenta_pedida(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    a = _mk_table(db, client, h, zone["id"], name="M1")
    b = _mk_table(db, client, h, zone["id"], name="M2")
    product_id = _mk_product(db)

    plano = _floor(client, h)
    assert plano["M1"]["status"] == "free" and plano["M2"]["status"] == "free"

    # Abrir sesión de mesa = borrador tipo restaurant (§4.2).
    order = _open(client, h, a["id"], terminal_id, guest_count=2, note="ventana")
    assert order["order_type"] == "restaurant"
    assert order["dining_table_id"] == a["id"]
    assert order["status"] == "draft" and order["total_amount"] is None

    # Línea por /sales (la sesión ES el borrador): precio CON IVA incluido.
    line = _add_line(client, h, order["id"], product_id, quantity="2")
    assert line["unit_price"] == "2.00" and line["tax_rate"] == "21.00"
    assert Decimal(line["total"]) == Decimal("4.00")  # 2 × 2.00 (IVA dentro)

    plano = _floor(client, h)
    open_m1 = plano["M1"]
    assert open_m1["status"] == "open"
    assert open_m1["order_id"] == order["id"]
    assert Decimal(open_m1["open_total"]) == Decimal("4.00")
    assert open_m1["waiter"] == "Usuario de Prueba"
    assert open_m1["guest_count"] == 2 and open_m1["note"] == "ventana"
    assert plano["M2"]["status"] == "free"

    # «Cuenta pedida»: el azul del semáforo, derivado de bill_requested_at.
    bill = client.post(f"{REST}/orders/{order['id']}/bill", headers=h)
    assert bill.status_code == 200, bill.text
    assert _floor(client, h)["M1"]["status"] == "bill"

    twice = client.post(f"{REST}/orders/{order['id']}/bill", headers=h)
    assert twice.status_code == 409, twice.text

    # Cancelar la petición vuelve al ámbar; sin comanda, 404.
    cancel = client.post(f"{REST}/orders/{order['id']}/bill/cancel", headers=h)
    assert cancel.status_code == 200, cancel.text
    assert _floor(client, h)["M1"]["status"] == "open"
    ghost = client.post(f"{REST}/orders/{uuid4()}/bill", headers=h)
    assert ghost.status_code == 404, ghost.text

    # M2 sigue libre durante todo el ciclo.
    assert _floor(client, h)["M2"]["status"] == "free"


def test_sesion_notas_comensales_y_apertura_idempotente(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    table = _mk_table(db, client, h, zone["id"], name="M1")

    # Reintento con la misma clave: Replay, un solo pedido.
    idem = {"Idempotency-Key": str(uuid4())}
    payload = {"terminal_id": terminal_id, "guest_count": 3, "note": "cumpleaños"}
    first = client.post(f"{REST}/tables/{table['id']}/open", headers={**h, **idem},
                        json=payload)
    assert first.status_code == 201, first.text
    again = client.post(f"{REST}/tables/{table['id']}/open", headers={**h, **idem},
                        json=payload)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]

    # Segunda apertura sin clave: la mesa ya está ocupada (uq_orders_open_per_table).
    double = client.post(f"{REST}/tables/{table['id']}/open", headers=h,
                         json={"terminal_id": terminal_id})
    assert double.status_code == 409, double.text

    # Mesa inexistente: 404 (también las inactivas no se pueden abrir).
    ghost = client.post(f"{REST}/tables/{uuid4()}/open", headers=h,
                        json={"terminal_id": terminal_id})
    assert ghost.status_code == 404, ghost.text

    # Notas y comensales de la sesión (PATCH /restaurant/orders/{id}).
    patch = client.patch(f"{REST}/orders/{first.json()['id']}", headers=h,
                         json={"note": "sin lactosa", "guest_count": 4})
    assert patch.status_code == 200, patch.text
    assert patch.json()["note"] == "sin lactosa"
    assert patch.json()["guest_count"] == 4
    plano = _floor(client, h)
    assert plano["M1"]["note"] == "sin lactosa"
    assert plano["M1"]["guest_count"] == 4

    # Sin campos no se toca nada (campos no enviados ≠ None).
    untouched = client.patch(f"{REST}/orders/{first.json()['id']}", headers=h,
                             json={})
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["note"] == "sin lactosa"


# ---------------------------------------------------------------------------
# Traspasar, juntar y dividir
# ---------------------------------------------------------------------------
def test_traspaso_de_mesa(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    a = _mk_table(db, client, h, zone["id"], name="M1")
    b = _mk_table(db, client, h, zone["id"], name="M2")
    c = _mk_table(db, client, h, zone["id"], name="M3")
    product_id = _mk_product(db)

    order = _open(client, h, a["id"], terminal_id)
    _add_line(client, h, order["id"], product_id, quantity="1")

    moved = client.post(f"{REST}/tables/{a['id']}/transfer", headers=h,
                        json={"target_table_id": b["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == order["id"]
    assert moved.json()["dining_table_id"] == b["id"]

    plano = _floor(client, h)
    assert plano["M1"]["status"] == "free"
    assert plano["M2"]["status"] == "open"
    assert plano["M2"]["order_id"] == order["id"]

    # Destino ocupada: 409; misma mesa: 422; mesa sin comanda: 404.
    _open(client, h, c["id"], terminal_id)  # M3 abierta para el siguiente caso
    occupied = client.post(f"{REST}/tables/{c['id']}/transfer", headers=h,
                           json={"target_table_id": b["id"]})
    assert occupied.status_code == 409, occupied.text
    same = client.post(f"{REST}/tables/{b['id']}/transfer", headers=h,
                       json={"target_table_id": b["id"]})
    assert same.status_code == 422, same.text
    free_source = client.post(f"{REST}/tables/{a['id']}/transfer", headers=h,
                              json={"target_table_id": c["id"]})
    assert free_source.status_code == 404, free_source.text


def test_juntar_mesas(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    a = _mk_table(db, client, h, zone["id"], name="M1")
    b = _mk_table(db, client, h, zone["id"], name="M2")
    c = _mk_table(db, client, h, zone["id"], name="M3")
    cafe = _mk_product(db, name="Café")
    tosta = _mk_product(db, name="Tostada")

    src = _open(client, h, a["id"], terminal_id, guest_count=3)
    _add_line(client, h, src["id"], cafe, quantity="1")           # 2.00
    dst = _open(client, h, b["id"], terminal_id)                  # sin comensales
    _add_line(client, h, dst["id"], tosta, quantity="2")          # 4.00

    merged = client.post(f"{REST}/tables/{a['id']}/merge", headers=h,
                         json={"target_table_id": b["id"]})
    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["id"] == dst["id"]
    assert body["guest_count"] == 3  # heredados del origen
    # Las líneas del destino conservan su orden; las movidas continúan detrás.
    assert [line["name"] for line in body["lines"]] == ["Tostada", "Café"]

    # La mesa origen queda libre y su borrador, anulado con motivo.
    plano = _floor(client, h)
    assert plano["M1"]["status"] == "free"
    assert plano["M2"]["status"] == "open"
    assert Decimal(plano["M2"]["open_total"]) == Decimal("6.00")

    events = db.execute(
        text("SELECT event_type, payload FROM sale_events WHERE order_id = :o"),
        {"o": src["id"]},
    ).all()
    assert any(row[0] == "sale_voided" and "Junta de mesas" in row[1]["reason"]
               for row in events)

    # Origen sin comanda: 404; destino libre: 404.
    no_source = client.post(f"{REST}/tables/{a['id']}/merge", headers=h,
                            json={"target_table_id": b["id"]})
    assert no_source.status_code == 404, no_source.text
    free_target = client.post(f"{REST}/tables/{b['id']}/merge", headers=h,
                              json={"target_table_id": c["id"]})
    assert free_target.status_code == 404, free_target.text


def test_division_de_cuenta(db, client):
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    a = _mk_table(db, client, h, zone["id"], name="M1")
    b = _mk_table(db, client, h, zone["id"], name="M2")
    c = _mk_table(db, client, h, zone["id"], name="M3")
    product_id = _mk_product(db)  # precio 2.00 con IVA incluido

    src = _open(client, h, a["id"], terminal_id)
    line_a = _add_line(client, h, src["id"], product_id, quantity="2")   # 4.00
    line_b = _add_line(client, h, src["id"], product_id, quantity="1")   # 2.00

    # Línea entera a M2: mismo snapshot, nueva comanda en la mesa destino.
    whole = client.post(f"{REST}/tables/{a['id']}/split", headers=h, json={
        "target_table_id": b["id"],
        "lines": [{"line_id": line_a["id"], "quantity": "2"}],
    })
    assert whole.status_code == 200, whole.text
    new_order = whole.json()
    assert new_order["id"] != src["id"]
    assert new_order["order_type"] == "restaurant"
    assert len(new_order["lines"]) == 1
    assert new_order["lines"][0]["id"] == line_a["id"]  # reasignada, no copiada
    assert Decimal(new_order["lines"][0]["total"]) == Decimal("4.00")

    plano = _floor(client, h)
    assert plano["M1"]["status"] == "open"
    assert Decimal(plano["M1"]["open_total"]) == Decimal("2.00")
    assert Decimal(plano["M2"]["open_total"]) == Decimal("4.00")

    # Parcial a M3: la mitad de la línea restante; recálculo con el motor puro.
    partial = client.post(f"{REST}/tables/{a['id']}/split", headers=h, json={
        "target_table_id": c["id"],
        "lines": [{"line_id": line_b["id"], "quantity": "0.5"}],
    })
    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert Decimal(body["lines"][0]["quantity"]) == Decimal("0.5")
    assert Decimal(body["lines"][0]["total"]) == Decimal("1.00")

    source_lines = client.get(f"{SALES}/orders/{src['id']}", headers=h).json()
    assert Decimal(source_lines["lines"][0]["quantity"]) == Decimal("0.5")
    assert Decimal(source_lines["lines"][0]["total"]) == Decimal("1.00")

    # Separar la comanda completa: 409 con salida por traspaso/anulación
    # (hacia una mesa LIBRE: un destino ocupada daría otro 409 antes).
    d = _mk_table(db, client, h, zone["id"], name="M4")
    all_lines = client.get(f"{SALES}/orders/{src['id']}", headers=h).json()["lines"]
    todo = client.post(f"{REST}/tables/{a['id']}/split", headers=h, json={
        "target_table_id": d["id"],
        "lines": [{"line_id": all_lines[0]["id"],
                   "quantity": all_lines[0]["quantity"]}],
    })
    assert todo.status_code == 409, todo.text
    assert "traspasa" in todo.json()["detail"]

    # Cada línea solo una vez: 422; destino ocupada: 409.
    dup = client.post(f"{REST}/tables/{a['id']}/split", headers=h, json={
        "target_table_id": b["id"],
        "lines": [{"line_id": line_b["id"], "quantity": "0.1"},
                  {"line_id": line_b["id"], "quantity": "0.2"}],
    })
    assert dup.status_code == 422, dup.text
    occupied = client.post(f"{REST}/tables/{c['id']}/split", headers=h, json={
        "target_table_id": b["id"],
        "lines": [{"line_id": line_b["id"], "quantity": "0.1"}],
    })
    assert occupied.status_code == 409, occupied.text


# ---------------------------------------------------------------------------
# Permisos y contratos públicos
# ---------------------------------------------------------------------------
def test_permisos_restaurant_operate(db, client):
    # Sin restaurant.operate: el módulo entero queda fuera (403).
    h_sin = _headers(db, client, perms=("products.view", "sales.sell"),
                     username="cocina")
    denied = client.get(f"{REST}/tables", headers=h_sin)
    assert denied.status_code == 403, denied.text
    denied_zone = client.post(f"{REST}/zones", headers=h_sin, json={"name": "X"})
    assert denied_zone.status_code == 403, denied_zone.text

    # Sin token: 401; con permiso: el mapa responde.
    anon = client.get(f"{REST}/tables")
    assert anon.status_code == 401, anon.text
    h = _headers(db, client, username="jefe")
    zone = _mk_zone(db, client, h)
    assert zone["id"]

    # Ventas sin sales.sell ni con restaurant.operate: las líneas piden vender.
    h_solo_sala = _headers(db, client, perms=("restaurant.operate",),
                           username="sala")
    table = _mk_table(db, client, h, zone["id"], name="M1")
    order = _open(client, h_solo_sala, table["id"], _mk_terminal(db))
    product_id = _mk_product(db)
    no_sell = client.post(f"{SALES}/orders/{order['id']}/lines", headers=h_solo_sala,
                          json={"product_id": product_id, "quantity": "1"})
    assert no_sell.status_code == 403, no_sell.text


def test_formato_del_sondeo_movil(db, client):
    """La PWA sondea ``GET /restaurant/tables`` esperando
    ``{items: [{id, name, status?}]}`` (lib/capabilities → TablesScreen)."""
    h = _headers(db, client)
    terminal_id = _mk_terminal(db)
    zone = _mk_zone(db, client, h)
    table = _mk_table(db, client, h, zone["id"], name="M1")

    resp = client.get(f"{REST}/tables", headers=h)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == table["id"]
    assert item["name"] == "M1"
    assert item["status"] == "free"

    order = _open(client, h, table["id"], terminal_id)
    items = client.get(f"{REST}/tables", headers=h).json()["items"]
    assert items[0]["status"] == "open"
    assert items[0]["order_id"] == order["id"]

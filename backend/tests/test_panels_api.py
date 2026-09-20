"""Tests E2E de los paneles del TPV visual (fase 05) contra PostgreSQL real.

Cubren el árbol ``GET /catalog/panels`` (panel→subpanel→producto con snapshot
de venta embebido), el CRUD de paneles/subpaneles, el rediseño de rejilla por
PUT completo (con sus validaciones) y los permisos del módulo.

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
AUTH = f"{V1}/auth"
# Secreto de prueba nuevo y rotado (≥32 caracteres); nunca vive en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"


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
# Fábricas mínimas (idéntico patrón que test_catalog_api.py)
# ---------------------------------------------------------------------------
def _mk_role(db, code: str, permissions: tuple[str, ...] = ()) -> int:
    role_id = db.execute(
        text("INSERT INTO roles (code, name) VALUES (:c, :n) RETURNING id"),
        {"c": code, "n": code},
    ).scalar_one()
    for perm in permissions:
        perm_id = db.execute(
            text(
                "INSERT INTO permissions (code, description) "
                "VALUES (:c, 'permiso de prueba') RETURNING id"
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


def _admin_headers(db, client) -> dict:
    role_id = _mk_role(db, "admin", ("products.view", "products.edit"))
    _mk_user(db, role_id, "jefe")
    return _bearer(_login(client, "jefe").json()["access_token"])


def _catalog_actions(db) -> list[str]:
    return db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'catalog.%' ORDER BY id")
    ).scalars().all()


def _mk_panel(client, h, name="Cafetería", sort_order=0) -> dict:
    resp = client.post(f"{CAT}/panels", headers=h, json={"name": name, "sort_order": sort_order})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_subpanel(client, h, panel_id, name="Cafés", sort_order=0) -> dict:
    resp = client.post(
        f"{CAT}/panels/{panel_id}/subpanels",
        headers=h, json={"name": name, "sort_order": sort_order},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_product(client, h, tax_rate_id, name="Café solo", price="1.50", **extra) -> dict:
    payload = {"name": name, "tax_rate_id": str(tax_rate_id), "price": price}
    payload.update(extra)
    resp = client.post(f"{CAT}/products", headers=h, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _put_grid(client, h, panel_id, items: list[dict]):
    return client.put(f"{CAT}/panels/{panel_id}/items", headers=h, json={"items": items})


def _tree(client, h) -> dict:
    resp = client.get(f"{CAT}/panels", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1 · CRUD de paneles + baja lógica + orden + auditoría
# ---------------------------------------------------------------------------
def test_crud_panel_orden_y_auditoria(db, client):
    h = _admin_headers(db, client)
    assert _tree(client, h)["panels"] == []

    panel = _mk_panel(client, h, name="Cafetería", sort_order=1)
    assert panel["name"] == "Cafetería" and panel["active"] is True

    patched = client.patch(f"{CAT}/panels/{panel['id']}", headers=h,
                           json={"name": "Barras", "sort_order": 2})
    assert patched.status_code == 200
    assert patched.json() == {**panel, "name": "Barras", "sort_order": 2}

    other = _mk_panel(client, h, name="Vinos", sort_order=1)
    reordered = client.post(f"{CAT}/panels/reorder", headers=h, json={"items": [
        {"id": other["id"], "sort_order": 0}, {"id": panel["id"], "sort_order": 1},
    ]})
    assert reordered.status_code == 200
    assert [p["name"] for p in reordered.json()["panels"]] == ["Vinos", "Barras"]

    missing = client.post(f"{CAT}/panels/reorder", headers=h, json={"items": [
        {"id": str(uuid4()), "sort_order": 0},
    ]})
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"

    deleted = client.delete(f"{CAT}/panels/{panel['id']}", headers=h)
    assert deleted.status_code == 200 and deleted.json()["active"] is False
    assert [p["name"] for p in _tree(client, h)["panels"]] == ["Vinos"]  # baja = fuera del árbol

    assert _catalog_actions(db) == [
        "catalog.panel_created", "catalog.panel_updated",
        "catalog.panel_created",  # la creación de «Vinos» también se audita
        "catalog.panels_reordered", "catalog.panel_deleted",
    ]


# ---------------------------------------------------------------------------
# 2 · Subpaneles: pertenencia al panel, edición y orden
# ---------------------------------------------------------------------------
def test_subpaneles(db, client):
    h = _admin_headers(db, client)

    orphan = client.post(f"{CAT}/panels/{uuid4()}/subpanels", headers=h,
                         json={"name": "Huérfano"})
    assert orphan.status_code == 404

    panel = _mk_panel(client, h)
    sub_b = _mk_subpanel(client, h, panel["id"], name="Tés", sort_order=2)
    sub_a = _mk_subpanel(client, h, panel["id"], name="Cafés", sort_order=1)
    assert sub_a["panel_id"] == panel["id"]

    tree = _tree(client, h)["panels"][0]
    assert [s["name"] for s in tree["subpanels"]] == ["Cafés", "Tés"]

    reordered = client.post(f"{CAT}/subpanels/reorder", headers=h, json={"items": [
        {"id": sub_b["id"], "sort_order": 0}, {"id": sub_a["id"], "sort_order": 1},
    ]})
    assert reordered.status_code == 200
    assert [s["name"] for s in reordered.json()["panels"][0]["subpanels"]] == ["Tés", "Cafés"]

    renamed = client.patch(f"{CAT}/subpanels/{sub_a['id']}", headers=h, json={"name": "Lácteos"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Lácteos"

    deleted = client.delete(f"{CAT}/subpanels/{sub_b['id']}", headers=h)
    assert deleted.status_code == 200 and deleted.json()["active"] is False
    assert [s["name"] for s in _tree(client, h)["panels"][0]["subpanels"]] == ["Lácteos"]

    actions = _catalog_actions(db)
    assert actions[:4] == [
        "catalog.panel_created",
        "catalog.subpanel_created", "catalog.subpanel_created",
        "catalog.subpanels_reordered",
    ]
    assert actions[-1] == "catalog.subpanel_deleted"


# ---------------------------------------------------------------------------
# 3 · Rejilla: PUT completo, snapshot embebido y reemplazo
# ---------------------------------------------------------------------------
def test_rejilla_put_snapshot_y_reemplazo(db, client):
    h = _admin_headers(db, client)
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    cafe = _mk_product(client, h, tax["id"], name="Café solo", price="1.50")
    te = _mk_product(client, h, tax["id"], name="Té verde", price="2.10", short_name="Té")

    panel = _mk_panel(client, h)
    sub = _mk_subpanel(client, h, panel["id"], name="Calientes")

    resp = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 0, "grid_col": 1,
         "label": "Solo", "color": "#8b4513"},
        {"product_id": te["id"], "subpanel_id": sub["id"], "grid_row": 1, "grid_col": 0},
    ])
    assert resp.status_code == 200, resp.text
    tree_panel = resp.json()["panels"][0]

    # Botón directo del panel: posición, alternativa y producto embebido.
    assert len(tree_panel["items"]) == 1
    direct = tree_panel["items"][0]
    assert (direct["grid_row"], direct["grid_col"]) == (0, 1)
    assert direct["label"] == "Solo" and direct["color"] == "#8b4513"
    assert direct["product"]["id"] == cafe["id"]
    assert direct["product"]["price"] == "1.50"  # dinero string en el snapshot (§3)
    assert direct["product"]["tax_code"] == "general"
    assert direct["product"]["tax_rate"] == "21.00"
    assert direct["product"]["weighable"] is False

    # Botón dentro del subpanel.
    assert len(tree_panel["subpanels"][0]["items"]) == 1
    assert tree_panel["subpanels"][0]["items"][0]["product"]["name"] == "Té verde"

    # Un segundo PUT reemplaza TODO el diseño: quita el del subpanel y mueve el otro.
    resp2 = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 2, "grid_col": 2},
        {"product_id": te["id"], "grid_row": 0, "grid_col": 0},
    ])
    assert resp2.status_code == 200
    tree2 = resp2.json()["panels"][0]
    assert len(tree2["items"]) == 2
    assert tree2["subpanels"][0]["items"] == []
    assert {(i["grid_row"], i["grid_col"]) for i in tree2["items"]} == {(0, 0), (2, 2)}
    assert db.execute(
        text("SELECT count(*) FROM panel_items WHERE subpanel_id = :s"), {"s": sub["id"]}
    ).scalar_one() == 0

    assert "catalog.panel_items_replaced" in _catalog_actions(db)


# ---------------------------------------------------------------------------
# 4 · Validaciones del rediseño: todo-o-nada
# ---------------------------------------------------------------------------
def test_rejilla_validaciones_no_dejan_estado_a_medias(db, client):
    h = _admin_headers(db, client)
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    cafe = _mk_product(client, h, tax["id"])

    panel = _mk_panel(client, h)
    sub = _mk_subpanel(client, h, panel["id"], name="Calientes")
    other_panel = _mk_panel(client, h, name="Otro")
    other_sub = _mk_subpanel(client, h, other_panel["id"], name="Ajeno")

    assert _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 0, "grid_col": 0},
    ]).status_code == 200

    # Producto inexistente → 404 y el diseño previo sigue intacto.
    bad = _put_grid(client, h, panel["id"], [
        {"product_id": str(uuid4()), "grid_row": 0, "grid_col": 0},
    ])
    assert bad.status_code == 404 and bad.json()["code"] == "NOT_FOUND"
    assert len(_tree(client, h)["panels"][0]["items"]) == 1

    # Subpanel de OTRO panel → 422 (existe, pero no pertenece aquí).
    alien = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "subpanel_id": other_sub["id"], "grid_row": 0, "grid_col": 0},
    ])
    assert alien.status_code == 422 and alien.json()["code"] == "VALIDATION_ERROR"

    # Dos botones en la misma posición del mismo contenedor → 409.
    clash = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 1, "grid_col": 1},
        {"product_id": cafe["id"], "grid_row": 1, "grid_col": 1},
    ])
    assert clash.status_code == 409 and clash.json()["code"] == "CONFLICT"
    # La misma posición en contenedores distintos es válida (no es 409).
    ok = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 1, "grid_col": 1},
        {"product_id": cafe["id"], "subpanel_id": sub["id"], "grid_row": 1, "grid_col": 1},
    ])
    assert ok.status_code == 200

    # Fuera de la rejilla 20×20 y panel inexistente.
    assert client.put(f"{CAT}/panels/{panel['id']}/items", headers=h, json={"items": [
        {"product_id": cafe["id"], "grid_row": 20, "grid_col": 0},
    ]}).status_code == 422
    assert _put_grid(client, h, str(uuid4()), [
        {"product_id": cafe["id"], "grid_row": 0, "grid_col": 0},
    ]).status_code == 404


# ---------------------------------------------------------------------------
# 5 · Quitar un botón suelto
# ---------------------------------------------------------------------------
def test_borrar_item_de_rejilla(db, client):
    h = _admin_headers(db, client)
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    cafe = _mk_product(client, h, tax["id"])
    te = _mk_product(client, h, tax["id"], name="Té")
    panel = _mk_panel(client, h)

    tree = _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 0, "grid_col": 0},
        {"product_id": te["id"], "grid_row": 0, "grid_col": 1},
    ]).json()
    item_ids = [i["id"] for i in tree["panels"][0]["items"]]

    removed = client.delete(f"{CAT}/panel-items/{item_ids[0]}", headers=h)
    assert removed.status_code == 204
    remaining = _tree(client, h)["panels"][0]["items"]
    assert [i["id"] for i in remaining] == [item_ids[1]]

    again = client.delete(f"{CAT}/panel-items/{item_ids[0]}", headers=h)
    assert again.status_code == 404

    # El producto no se toca: solo desapareció el botón.
    assert client.get(f"{CAT}/products/{cafe['id']}", headers=h).status_code == 200


# ---------------------------------------------------------------------------
# 6 · Productos dados de baja desaparecen de la rejilla al leer
# ---------------------------------------------------------------------------
def test_producto_inactivo_desaparece_de_la_rejilla(db, client):
    h = _admin_headers(db, client)
    tax = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "21.00", "valid_from": "2026-01-01",
    }).json()
    cafe = _mk_product(client, h, tax["id"])
    panel = _mk_panel(client, h)
    assert _put_grid(client, h, panel["id"], [
        {"product_id": cafe["id"], "grid_row": 0, "grid_col": 0},
    ]).status_code == 200
    assert len(_tree(client, h)["panels"][0]["items"]) == 1

    assert client.delete(f"{CAT}/products/{cafe['id']}", headers=h).status_code == 200
    assert _tree(client, h)["panels"][0]["items"] == []  # filtrado al leer


# ---------------------------------------------------------------------------
# 7 · Permisos: leer es venta, rediseñar es administración
# ---------------------------------------------------------------------------
def test_permisos_paneles(db, client):
    viewer_role = _mk_role(db, "consulta", ("products.view",))
    _mk_user(db, viewer_role, "consulta")
    viewer = _bearer(_login(client, "consulta").json()["access_token"])

    assert client.get(f"{CAT}/panels", headers=viewer).status_code == 200
    denied = client.post(f"{CAT}/panels", headers=viewer, json={"name": "X"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"

    anonymous = client.get(f"{CAT}/panels")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "AUTH_REQUIRED"

"""Tests E2E del módulo de productos (fase 04) contra PostgreSQL real.

Cubren CRUD de departamentos/categorías/productos, tarifas, IVA con vigencia,
códigos de barras, imágenes, activación, orden, histórico inmutable de precios,
el snapshot optimizado ``/catalog/pos`` y los permisos ``products.view``/``products.edit``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías. El dinero viaja SIEMPRE como string en el JSON (§3).
"""

import os
from datetime import date
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
# Fábricas mínimas (idéntico patrón que test_auth_api.py)
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


def _mk_user(
    db,
    role_id,
    username: str = "jefe",
    password: str = CLAVE,
    pin: str | None = None,
) -> int:
    return db.execute(
        text(
            "INSERT INTO users (username, password_hash, pin_hash, full_name, role_id) "
            "VALUES (:u, :ph, :pih, :fn, :r) RETURNING id"
        ),
        {
            "u": username,
            "ph": hash_password(password),
            "pih": hash_password(pin) if pin else None,
            "fn": "Usuario de Prueba",
            "r": role_id,
        },
    ).scalar_one()


def _login(client, username: str = "jefe", password: str = CLAVE):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(db, client) -> dict:
    """Rol con los dos permisos del módulo + login; devuelve los headers."""
    role_id = _mk_role(db, "admin", ("products.view", "products.edit"))
    _mk_user(db, role_id, "jefe")
    return _bearer(_login(client, "jefe").json()["access_token"])


def _mk_department(client, h, code="BEB", name="Bebidas", sort_order=0) -> dict:
    resp = client.post(
        f"{CAT}/departments", headers=h,
        json={"code": code, "name": name, "sort_order": sort_order},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_category(client, h, name="Cafés", department_id=None) -> dict:
    resp = client.post(
        f"{CAT}/categories", headers=h,
        json={"name": name, "department_id": str(department_id) if department_id else None},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_tax_rate(client, h, code="general", rate="21.00", valid_from="2026-01-01") -> dict:
    resp = client.post(
        f"{CAT}/tax-rates", headers=h,
        json={"code": code, "name": f"IVA {rate}", "rate": rate, "valid_from": valid_from},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_tier(client, h, code="empleado", name="Precio empleado") -> dict:
    resp = client.post(f"{CAT}/tiers", headers=h, json={"code": code, "name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mk_product(client, h, tax_rate_id, name="Café solo", price="1.50", **extra) -> dict:
    payload = {"name": name, "tax_rate_id": str(tax_rate_id), "price": price}
    payload.update(extra)
    resp = client.post(f"{CAT}/products", headers=h, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _catalog_actions(db) -> list[str]:
    return db.execute(
        text("SELECT action FROM audit_log WHERE action LIKE 'catalog.%' ORDER BY id")
    ).scalars().all()


# ---------------------------------------------------------------------------
# 1 · CRUD de departamentos (nivel 1) + unicidad de código + baja lógica
# ---------------------------------------------------------------------------
def test_crud_departamento(db, client):
    h = _admin_headers(db, client)

    row = _mk_department(client, h, code="BEB", name="Bebidas")
    assert row["code"] == "BEB" and row["active"] is True and row["sort_order"] == 0

    dup = client.post(f"{CAT}/departments", headers=h,
                      json={"code": "BEB", "name": "Otro"})
    assert dup.status_code == 409
    assert dup.json()["code"] == "CONFLICT"

    dept_id = row["id"]
    patched = client.patch(f"{CAT}/departments/{dept_id}", headers=h,
                           json={"name": "Bebidas frías", "sort_order": 5})
    assert patched.status_code == 200
    assert patched.json()["name"] == "Bebidas frías"
    assert patched.json()["sort_order"] == 5

    # Baja lógica: la fila persiste con active=false.
    deleted = client.delete(f"{CAT}/departments/{dept_id}", headers=h)
    assert deleted.status_code == 200
    assert deleted.json()["active"] is False
    assert db.execute(
        text("SELECT active FROM departments WHERE id = :i"), {"i": dept_id}
    ).scalar_one() is False

    assert client.get(f"{CAT}/departments", headers=h).json() == []
    inactive = client.get(f"{CAT}/departments?include_inactive=true", headers=h).json()
    assert len(inactive) == 1 and inactive[0]["active"] is False

    assert _catalog_actions(db) == [
        "catalog.department_created", "catalog.department_updated", "catalog.department_deleted",
    ]


# ---------------------------------------------------------------------------
# 2 · Categorías (subcategorías): departamento inexistente → 404
# ---------------------------------------------------------------------------
def test_categoria_exige_departamento_existente(db, client):
    h = _admin_headers(db, client)

    suelta = _mk_category(client, h, name="Varios")
    assert suelta["department_id"] is None

    bad = client.post(f"{CAT}/categories", headers=h,
                      json={"name": "Huérfana", "department_id": str(uuid4())})
    assert bad.status_code == 404
    assert bad.json()["code"] == "NOT_FOUND"

    dept = _mk_department(client, h)
    colgada = _mk_category(client, h, name="Refrescos", department_id=dept["id"])
    assert colgada["department_id"] == dept["id"]

    listing = client.get(f"{CAT}/categories?department_id={dept['id']}", headers=h).json()
    assert [c["name"] for c in listing] == ["Refrescos"]


# ---------------------------------------------------------------------------
# 3 · Producto completo: hijos, dinero string, histórico y auditoría
# ---------------------------------------------------------------------------
def test_producto_completo(db, client):
    h = _admin_headers(db, client)
    dept = _mk_department(client, h)
    cat = _mk_category(client, h, department_id=dept["id"])
    tax = _mk_tax_rate(client, h)
    tier = _mk_tier(client, h)

    row = _mk_product(
        client, h, tax["id"], name="Café solo", price="12.50", sku="CAFE-1",
        category_id=cat["id"], short_name="Café", kitchen=True,
        barcodes=["8400000000017", " 8400000000024 "],
        tier_prices=[{"tier_id": tier["id"], "price": "10.00"}],
        images=[{"path": "img/cafe.png", "sort_order": 1}],
    )
    # Dinero SIEMPRE string en JSON (§3), incluso en respuestas.
    assert row["price"] == "12.50" and isinstance(row["price"], str)
    assert row["barcodes"] == ["8400000000017", "8400000000024"]  # strip aplicado
    assert row["tier_prices"] == [{"tier_code": "empleado", "price": "10.00"}]
    assert row["images"] == [{"path": "img/cafe.png", "sort_order": 1}]
    assert row["tax_rate_id"] == tax["id"]

    product_id = row["id"]
    # Primera fila del histórico inmutable, abierta.
    prices = db.execute(
        text("SELECT price, valid_to FROM product_prices WHERE product_id = :p"),
        {"p": product_id},
    ).all()
    assert len(prices) == 1 and prices[0][0] == Decimal("12.50")
    assert prices[0][1] is None

    assert db.execute(
        text("SELECT count(*) FROM product_barcodes WHERE product_id = :p"),
        {"p": product_id},
    ).scalar_one() == 2

    created = client.get(f"{CAT}/products/{product_id}", headers=h)
    assert created.status_code == 200
    assert created.json()["price"] == "12.50"

    # Listado paginado: total coherente.
    listing = client.get(f"{CAT}/products", headers=h).json()
    assert listing["total"] == 1 and listing["limit"] == 100 and listing["offset"] == 0

    # 404 en detalle inexistente.
    missing = client.get(f"{CAT}/products/{uuid4()}", headers=h)
    assert missing.status_code == 404 and missing.json()["code"] == "NOT_FOUND"

    assert "catalog.product_created" in _catalog_actions(db)


# ---------------------------------------------------------------------------
# 4 · Cambio de precio: cierra la fila vigente y abre otra (histórico inmutable)
# ---------------------------------------------------------------------------
def test_cambio_de_precio_abre_fila_nueva(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    product = _mk_product(client, h, tax["id"], price="5.00")

    patched = client.patch(f"{CAT}/products/{product['id']}", headers=h,
                           json={"price": "6.00"})
    assert patched.status_code == 200
    assert patched.json()["price"] == "6.00"

    rows = db.execute(
        text("SELECT price, valid_to IS NOT NULL FROM product_prices "
             "WHERE product_id = :p ORDER BY valid_from"),
        {"p": product["id"]},
    ).all()
    assert rows == [
        (Decimal("5.00"), True),   # cerrada, inmutable
        (Decimal("6.00"), False),  # vigente
    ]

    history = client.get(f"{CAT}/products/{product['id']}/prices", headers=h)
    assert history.status_code == 200
    items = history.json()
    assert [i["price"] for i in items] == ["6.00", "5.00"]  # más reciente primero
    assert items[0]["valid_to"] is None and items[1]["valid_to"] is not None


def test_mismo_precio_no_abre_fila(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    product = _mk_product(client, h, tax["id"], price="5.00")

    # Cambio de nombre sin tocar el precio: ninguna fila nueva.
    assert client.patch(f"{CAT}/products/{product['id']}", headers=h,
                        json={"name": "Café cortado"}).status_code == 200
    # Enviar el mismo precio tampoco.
    assert client.patch(f"{CAT}/products/{product['id']}", headers=h,
                        json={"price": "5.00"}).status_code == 200
    assert db.execute(
        text("SELECT count(*) FROM product_prices WHERE product_id = :p"),
        {"p": product["id"]},
    ).scalar_one() == 1


# ---------------------------------------------------------------------------
# 5 · Unicidad de SKU y códigos de barras (global)
# ---------------------------------------------------------------------------
def test_sku_y_barcode_duplicados_conflicto(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    _mk_product(client, h, tax["id"], sku="SKU-1", barcodes=["8400000000017"])

    dup_sku = client.post(f"{CAT}/products", headers=h, json={
        "name": "Otro", "tax_rate_id": tax["id"], "price": "2.00", "sku": "SKU-1",
    })
    assert dup_sku.status_code == 409 and dup_sku.json()["code"] == "CONFLICT"

    dup_barcode = client.post(f"{CAT}/products", headers=h, json={
        "name": "Otro", "tax_rate_id": tax["id"], "price": "2.00",
        "barcodes": ["8400000000017"],
    })
    assert dup_barcode.status_code == 409

    # El strip del validador no burla la unicidad.
    dup_espacios = client.post(f"{CAT}/products", headers=h, json={
        "name": "Otro", "tax_rate_id": tax["id"], "price": "2.00",
        "barcodes": ["  8400000000017  "],
    })
    assert dup_espacios.status_code == 409

    # Reasignar el mismo código a su dueño (PATCH) sí está permitido.
    owner = client.get(f"{CAT}/products", headers=h).json()["items"][0]
    ok = client.patch(f"{CAT}/products/{owner['id']}", headers=h,
                      json={"barcodes": ["8400000000017"]})
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# 6 · Reordenación bulk (orden de la rejilla)
# ---------------------------------------------------------------------------
def test_reorder_departamentos(db, client):
    h = _admin_headers(db, client)
    d1 = _mk_department(client, h, code="D1", name="Uno")
    d2 = _mk_department(client, h, code="D2", name="Dos")
    d3 = _mk_department(client, h, code="D3", name="Tres")

    resp = client.post(f"{CAT}/departments/reorder", headers=h, json={"items": [
        {"id": d1["id"], "sort_order": 30},
        {"id": d2["id"], "sort_order": 10},
        {"id": d3["id"], "sort_order": 20},
    ]})
    assert resp.status_code == 200
    assert [d["id"] for d in resp.json()] == [d2["id"], d3["id"], d1["id"]]

    # Un id desconocido aborta todo (no se reordena a medias) y no muta nada.
    bad = client.post(f"{CAT}/departments/reorder", headers=h, json={"items": [
        {"id": str(uuid4()), "sort_order": 99},
        {"id": d2["id"], "sort_order": 1},
    ]})
    assert bad.status_code == 404
    orders = db.execute(
        text("SELECT sort_order FROM departments WHERE id = :i"), {"i": d2["id"]}
    ).scalar_one()
    assert orders == 10


# ---------------------------------------------------------------------------
# 7 · Baja lógica del producto y efecto en /catalog/pos
# ---------------------------------------------------------------------------
def test_baja_logica_excluye_del_pos(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    product = _mk_product(client, h, tax["id"], price="5.00", barcodes=["123"])

    pos = client.get(f"{CAT}/pos", headers=h).json()["products"]
    assert len(pos) == 1
    assert pos[0]["price"] == "5.00" and isinstance(pos[0]["price"], str)
    assert pos[0]["tax_code"] == "general" and pos[0]["tax_rate"] == "21.00"
    assert pos[0]["barcodes"] == ["123"]

    assert client.delete(f"{CAT}/products/{product['id']}", headers=h).status_code == 200
    assert client.get(f"{CAT}/pos", headers=h).json()["products"] == []

    # Sigue existiendo (soft delete), y se puede listar con active=false.
    assert db.execute(
        text("SELECT active FROM products WHERE id = :p"), {"p": product["id"]}
    ).scalar_one() is False
    listing = client.get(f"{CAT}/products?active=false", headers=h).json()
    assert listing["total"] == 1


def test_pos_filtra_cadena_inactiva(db, client):
    h = _admin_headers(db, client)
    dept = _mk_department(client, h)
    cat = _mk_category(client, h, department_id=dept["id"])
    tax = _mk_tax_rate(client, h)
    tier = _mk_tier(client, h)
    product = _mk_product(
        client, h, tax["id"], category_id=cat["id"], price="2.00",
        barcodes=["999"], tier_prices=[{"tier_id": tier["id"], "price": "1.50"}],
    )
    suelto = _mk_product(client, h, tax["id"], name="Suelto", price="1.00")  # sin categoría

    def pos_ids():
        return [p["id"] for p in client.get(f"{CAT}/pos", headers=h).json()["products"]]

    # Categoría inactiva: su producto sale; el que no tiene categoría sigue.
    client.patch(f"{CAT}/categories/{cat['id']}", headers=h, json={"active": False})
    assert pos_ids() == [suelto["id"]]

    client.patch(f"{CAT}/categories/{cat['id']}", headers=h, json={"active": True})
    # Departamento inactivo: la cadena completa debe estar activa para vender.
    client.patch(f"{CAT}/departments/{dept['id']}", headers=h, json={"active": False})
    assert pos_ids() == [suelto["id"]]

    client.patch(f"{CAT}/departments/{dept['id']}", headers=h, json={"active": True})
    assert set(pos_ids()) == {product["id"], suelto["id"]}

    pos = client.get(f"{CAT}/pos", headers=h).json()["products"]
    con_todo = next(p for p in pos if p["id"] == product["id"])
    assert con_todo["department_id"] == dept["id"]
    assert con_todo["barcodes"] == ["999"]
    assert con_todo["tier_prices"] == [{"tier_code": "empleado", "price": "1.50"}]


# ---------------------------------------------------------------------------
# 8 · Permisos: products.view lee, products.edit escribe, PIN de venta puede leer
# ---------------------------------------------------------------------------
def test_permisos_catalogo(db, client):
    role_id = _mk_role(db, "waiter", ("products.view",))
    _mk_user(db, role_id, "camarero")
    h = _bearer(_login(client, "camarero").json()["access_token"])

    # Lectura permitida con solo products.view.
    assert client.get(f"{CAT}/pos", headers=h).status_code == 200
    assert client.get(f"{CAT}/products", headers=h).status_code == 200

    denied = client.post(f"{CAT}/departments", headers=h,
                         json={"code": "X", "name": "X"})
    assert denied.status_code == 403
    assert denied.json()["code"] == "PERMISSION_DENIED"

    # Sin token: 401 antes que cualquier otra cosa.
    no_token = client.get(f"{CAT}/products")
    assert no_token.status_code == 401
    assert no_token.json()["code"] == "AUTH_REQUIRED"

    # Token de PIN (alcance pos) con products.view: leer el catálogo es vender.
    _mk_user(db, role_id, "camarero-pin", pin="4729")
    pin_login = client.post(f"{AUTH}/pin", json={"username": "camarero-pin", "pin": "4729"})
    assert pin_login.status_code == 200
    hp = _bearer(pin_login.json()["access_token"])
    assert client.get(f"{CAT}/pos", headers=hp).status_code == 200
    write = client.post(f"{CAT}/products", headers=hp, json={
        "name": "X", "tax_rate_id": str(uuid4()), "price": "1.00",
    })
    assert write.status_code == 403  # le falta products.edit


# ---------------------------------------------------------------------------
# 9 · IVA con vigencia: nueva versión cierra la anterior
# ---------------------------------------------------------------------------
def test_tax_rate_versionado(db, client):
    h = _admin_headers(db, client)
    v1 = _mk_tax_rate(client, h, code="general", rate="21.00", valid_from="2026-01-01")

    v2 = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "IVA general", "rate": "10.00",
        "valid_from": "2026-09-01",
    })
    assert v2.status_code == 200

    vigencias = db.execute(
        text("SELECT rate, valid_to FROM tax_rates WHERE code = 'general' ORDER BY valid_from")
    ).all()
    assert vigencias == [
        (21.00, date(2026, 9, 1)),  # cerrada por la nueva
        (10.00, None),
    ]

    current = client.get(f"{CAT}/tax-rates?only_current=true", headers=h).json()
    assert len(current) == 1
    assert current[0]["rate"] == "10.00" and current[0]["valid_to"] is None

    dup = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "duplicada", "rate": "8.00",
        "valid_from": "2026-09-01",
    })
    assert dup.status_code == 409

    # Retroactiva respecto de la vigente: 422, no 409.
    past = client.post(f"{CAT}/tax-rates", headers=h, json={
        "code": "general", "name": "retroactiva", "rate": "8.00",
        "valid_from": "2020-01-01",
    })
    assert past.status_code == 422
    assert past.json()["code"] == "VALIDATION_ERROR"
    assert v1["valid_to"] is None  # v1 devuelta antes del cierre: coherente en respuesta


# ---------------------------------------------------------------------------
# 10 · Listado: paginación, búsqueda y filtros
# ---------------------------------------------------------------------------
def test_listado_paginado_y_busqueda(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    dept = _mk_department(client, h)
    cat = _mk_category(client, h, department_id=dept["id"])
    _mk_product(client, h, tax["id"], name="Café", category_id=cat["id"])
    _mk_product(client, h, tax["id"], name="Té")
    _mk_product(client, h, tax["id"], name="Croissant", active=False)

    assert client.get(f"{CAT}/products", headers=h).json()["total"] == 3

    page = client.get(f"{CAT}/products?limit=2&offset=1", headers=h).json()
    assert page["total"] == 3 and len(page["items"]) == 2

    found = client.get(f"{CAT}/products?search=af", headers=h).json()
    assert found["total"] == 1 and found["items"][0]["name"] == "Café"

    assert client.get(f"{CAT}/products?search=zzz", headers=h).json()["total"] == 0

    by_dept = client.get(f"{CAT}/products?department_id={dept['id']}", headers=h).json()
    assert by_dept["total"] == 1

    active_only = client.get(f"{CAT}/products?active=true", headers=h).json()
    assert active_only["total"] == 2  # Croissant está de baja


# ---------------------------------------------------------------------------
# 11 · Tarifa inexistente en precios por tarifa → 404
# ---------------------------------------------------------------------------
def test_tier_inexistente_404(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)

    bad = client.post(f"{CAT}/products", headers=h, json={
        "name": "X", "tax_rate_id": tax["id"], "price": "1.00",
        "tier_prices": [{"tier_id": str(uuid4()), "price": "0.90"}],
    })
    assert bad.status_code == 404

    product = _mk_product(client, h, tax["id"], price="1.00")
    bad_patch = client.patch(f"{CAT}/products/{product['id']}", headers=h, json={
        "tier_prices": [{"tier_id": str(uuid4()), "price": "0.90"}],
    })
    assert bad_patch.status_code == 404


# ---------------------------------------------------------------------------
# 12 · Reemplazo completo de colecciones (barcodes/imágenes) en PATCH
# ---------------------------------------------------------------------------
def test_reemplazo_de_colecciones(db, client):
    h = _admin_headers(db, client)
    tax = _mk_tax_rate(client, h)
    product = _mk_product(
        client, h, tax["id"], price="1.00",
        barcodes=["1", "2"],
        images=[{"path": "a.png", "sort_order": 0}, {"path": "b.png", "sort_order": 1}],
    )

    # Solo imágenes: queda exactamente la colección enviada.
    patched = client.patch(f"{CAT}/products/{product['id']}", headers=h, json={
        "images": [{"path": "c.png", "sort_order": 0}],
    })
    assert patched.status_code == 200
    assert patched.json()["images"] == [{"path": "c.png", "sort_order": 0}]
    assert patched.json()["barcodes"] == ["1", "2"]  # intactos: no venía el campo

    # Vaciar barcodes explícitamente (lista vacía ≠ campo ausente).
    emptied = client.patch(f"{CAT}/products/{product['id']}", headers=h,
                           json={"barcodes": []})
    assert emptied.json()["barcodes"] == []
    assert db.execute(
        text("SELECT count(*) FROM product_barcodes WHERE product_id = :p"),
        {"p": product["id"]},
    ).scalar_one() == 0
    assert db.execute(
        text("SELECT count(*) FROM product_images WHERE product_id = :p"),
        {"p": product["id"]},
    ).scalar_one() == 1

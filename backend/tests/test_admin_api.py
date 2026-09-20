"""Tests E2E de administración (fase 22) contra PostgreSQL real.

Cubren los módulos nuevos del panel: usuarios/camareros (alta, PATCH, reset de
contraseña con revocación de sesiones, PIN alta/retirada, no auto-desactivarse),
roles y matriz de permisos (``admin`` intocable), terminales y dispositivos
(token solo en alta/rotación, baja lógica), auditoría (filtros por prefijo) y
backups (listado + 503 sin pg_dump + ejecución real si hay pg_dump). También la
pared de permisos: el camarero no administra.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella). Cada test parte de
tablas vacías; el fixture ``app`` es function-scoped para aislar ``backup_dir``
en un directorio temporal.
"""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.security import hash_password
from app.main import create_app
from app.services import backups as backups_service
from tests.conftest import apply_seed

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
A = f"{V1}/admin"
AUTH = f"{V1}/auth"
# Secretos de prueba nuevos y rotados; nunca viven en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

# El encargado administra todo el panel (y vende). El camarero solo opera.
BOSS_PERMS = ("products.view", "sales.sell", "orders.discount",
              "admin.users", "admin.roles", "admin.terminals",
              "admin.audit", "admin.backups")
WAITER_PERMS = ("products.view", "sales.sell", "orders.discount")


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


@pytest.fixture()
def app(tmp_path):
    if not TEST_DB_URL:
        pytest.skip("TPV_TEST_DATABASE_URL no definida: se omiten los tests de integración")
    return create_app(_settings(backup_dir=str(tmp_path / "backups")))


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
    return {"Authorization": f"Bearer {_login(client, username).json()['access_token']}"}


def _audit_actions(db, like="admin.%") -> set[str]:
    return {
        row[0] for row in db.execute(
            text("SELECT DISTINCT action FROM audit_log WHERE action LIKE :p"), {"p": like}
        )
    }


# ---------------------------------------------------------------------------
# Pared de permisos: el camarero no administra
# ---------------------------------------------------------------------------
def test_camarero_sin_acceso_al_panel(db, client):
    h = _headers(db, client, WAITER_PERMS, "camarero")
    for path in ("/users", "/roles", "/terminals", "/devices", "/audit", "/backups"):
        resp = client.get(f"{A}{path}", headers=h)
        assert resp.status_code == 403, (path, resp.status_code)
        # El código de error estable (el «title» es el texto humano, p. ej. Forbidden).
        assert resp.json()["code"] == "PERMISSION_DENIED"
    assert client.post(f"{A}/terminals", headers=h, json={"code": "T1", "name": "x"}).status_code == 403
    assert client.post(f"{A}/backups/run", headers=h).status_code == 403


# ---------------------------------------------------------------------------
# Usuarios y camareros
# ---------------------------------------------------------------------------
def test_crear_y_listar_usuarios(db, client):
    h = _headers(db, client)
    resp = client.post(f"{A}/users", headers=h, json={
        "username": "camarero1", "password": "Clave-Segura-2026",
        "full_name": "Ana García", "role_code": "role-cajero", "pin": "1234",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["username"] == "camarero1"
    assert body["role_code"] == "role-cajero"
    assert body["has_pin"] is True
    assert body["active"] is True
    # NINGÚN hash sale por la API (ADR-008)
    assert "password_hash" not in body and "pin_hash" not in body

    listing = client.get(f"{A}/users", headers=h).json()
    assert listing["total"] == 2  # cajero + camarero1
    assert listing["limit"] == 50 and listing["offset"] == 0
    assert any(u["username"] == "camarero1" for u in listing["items"])


def test_username_duplicado_y_rol_desconocido(db, client):
    h = _headers(db, client)
    base = {"password": "Clave-Segura-2026", "full_name": "Dup", "role_code": "role-cajero"}
    assert client.post(f"{A}/users", headers=h,
                       json={"username": "cajero", **base}).status_code == 409
    resp = client.post(f"{A}/users", headers=h,
                       json={"username": "nuevo", **base, "role_code": "no-existe"})
    assert resp.status_code == 422


def test_password_corta_rechazada(db, client):
    h = _headers(db, client)
    resp = client.post(f"{A}/users", headers=h, json={
        "username": "corta", "password": "7chars!", "full_name": "x", "role_code": "role-cajero",
    })
    assert resp.status_code == 422


def test_patch_usuario_cambia_nombre_y_rol(db, client):
    h = _headers(db, client)
    user_id = client.post(f"{A}/users", headers=h, json={
        "username": "temporal", "password": "Clave-Segura-2026",
        "full_name": "Nombre viejo", "role_code": "role-cajero",
    }).json()["id"]

    role_id = _mk_role(db, "supervisor", ("sales.sell",))
    resp = client.patch(f"{A}/users/{user_id}", headers=h,
                        json={"full_name": "Nombre nuevo", "role_code": "supervisor"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_name"] == "Nombre nuevo"
    assert body["role_code"] == "supervisor"

    actions = _audit_actions(db)
    assert "admin.user_created" in actions and "admin.user_updated" in actions
    update = db.execute(
        text("SELECT before_data, after_data FROM audit_log WHERE action = 'admin.user_updated'")
    ).one()
    assert update[0]["full_name"] == "Nombre viejo"
    assert update[1]["role"] == "supervisor"


def test_nadie_se_desactiva_a_si_mismo(db, client):
    h = _headers(db, client)
    me = client.get(f"{AUTH}/me", headers=h).json()
    resp = client.patch(f"{A}/users/{me['id']}", headers=h, json={"active": False})
    assert resp.status_code == 422


def test_reset_de_password_revoca_las_sesiones(db, client):
    boss = _headers(db, client)
    role_id = _mk_role(db, "role-camarero", WAITER_PERMS)
    _mk_user(db, role_id, "camarero")
    old_token = _login(client, "camarero").json()["access_token"]
    # la sesión vieja funciona
    assert client.get(f"{AUTH}/me",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 200

    users = client.get(f"{A}/users", headers=boss).json()["items"]
    user_id = next(u["id"] for u in users if u["username"] == "camarero")
    resp = client.put(f"{A}/users/{user_id}/password", headers=boss,
                      json={"password": "Otra-Clave-2026"})
    assert resp.status_code == 204

    # la sesión vieja muere; la nueva contraseña entra
    assert client.get(f"{AUTH}/me",
                      headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert _login(client, "camarero", "Otra-Clave-2026").status_code == 200
    assert "admin.password_reset" in _audit_actions(db)


def test_pin_alta_retirada_y_validacion(db, client):
    h = _headers(db, client)
    user_id = client.post(f"{A}/users", headers=h, json={
        "username": "pinuser", "password": "Clave-Segura-2026",
        "full_name": "Pin", "role_code": "role-cajero",
    }).json()["id"]

    assert client.put(f"{A}/users/{user_id}/pin", headers=h, json={"pin": "4321"}).status_code == 204
    users = client.get(f"{A}/users", headers=h).json()["items"]
    assert next(u for u in users if u["id"] == user_id)["has_pin"] is True

    assert client.put(f"{A}/users/{user_id}/pin", headers=h, json={"pin": None}).status_code == 204
    users = client.get(f"{A}/users", headers=h).json()["items"]
    assert next(u for u in users if u["id"] == user_id)["has_pin"] is False

    assert client.put(f"{A}/users/{user_id}/pin", headers=h, json={}).status_code == 422
    assert client.put(f"{A}/users/{user_id}/pin", headers=h, json={"pin": "12a4"}).status_code == 422
    assert "admin.pin_updated" in _audit_actions(db)
    assert "admin.pin_cleared" in _audit_actions(db)


def test_listado_excluye_inactivos_salvo_include(db, client):
    h = _headers(db, client)
    user_id = client.post(f"{A}/users", headers=h, json={
        "username": "baja", "password": "Clave-Segura-2026",
        "full_name": "Baja", "role_code": "role-cajero",
    }).json()["id"]
    client.patch(f"{A}/users/{user_id}", headers=h, json={"active": False})

    assert client.get(f"{A}/users", headers=h).json()["total"] == 1  # solo cajero
    both = client.get(f"{A}/users", headers=h, params={"include_inactive": True}).json()
    assert both["total"] == 2


# ---------------------------------------------------------------------------
# Roles y matriz de permisos
# ---------------------------------------------------------------------------
def test_roles_crear_duplicado_y_codigo_invalido(db, client):
    h = _headers(db, client)
    assert client.post(f"{A}/roles", headers=h,
                       json={"code": "supervisor", "name": "Supervisor"}).status_code == 201
    assert client.post(f"{A}/roles", headers=h,
                       json={"code": "supervisor", "name": "Otro"}).status_code == 409
    assert client.post(f"{A}/roles", headers=h,
                       json={"code": "Supervisor", "name": "x"}).status_code == 422


def test_matriz_reemplazo_y_permisos_desconocidos(db, client):
    h = _headers(db, client)
    role_id = client.post(f"{A}/roles", headers=h,
                          json={"code": "supervisor", "name": "Supervisor"}).json()["id"]

    resp = client.put(f"{A}/roles/{role_id}/permissions", headers=h,
                      json={"permissions": ["sales.sell", "products.view"]})
    assert resp.status_code == 204
    role = next(r for r in client.get(f"{A}/roles", headers=h).json()["items"]
                if r["id"] == role_id)
    assert role["permissions"] == ["products.view", "sales.sell"]  # ordenados, reemplazo total

    resp = client.put(f"{A}/roles/{role_id}/permissions", headers=h,
                      json={"permissions": ["products.view", "no.existe"]})
    assert resp.status_code == 422
    assert "no.existe" in resp.json()["detail"]
    # el reemplazo fallido no dejó la matriz a medias
    role = next(r for r in client.get(f"{A}/roles", headers=h).json()["items"]
                if r["id"] == role_id)
    assert role["permissions"] == ["products.view", "sales.sell"]


def test_la_matriz_de_admin_es_intocable(db, client):
    h = _headers(db, client)
    # El rol base «admin» (is_system) y su matriz vienen del seed: el fixture
    # «db» vacía también el catálogo que crean las migraciones de datos.
    apply_seed(db)
    roles = client.get(f"{A}/roles", headers=h).json()["items"]
    admin_role = next(r for r in roles if r["code"] == "admin")
    assert admin_role["is_system"] is True
    # la migración 0004 sincronizó el CROSS JOIN: admin lo tiene TODO
    perms = client.get(f"{A}/permissions", headers=h).json()["items"]
    assert {p["code"] for p in perms} <= set(admin_role["permissions"])
    assert "sales.sell" in admin_role["permissions"]
    assert "admin.audit" in admin_role["permissions"]

    resp = client.put(f"{A}/roles/{admin_role['id']}/permissions", headers=h,
                      json={"permissions": ["products.view"]})
    assert resp.status_code == 422  # nadie puede dejar sin permisos al admin


def test_catalogo_de_permisos_incluye_los_nuevos(db, client):
    h = _headers(db, client)
    # El catálogo base (con sales.void, admin.*...) lo siembra seed.sql: el
    # TRUNCATE del fixture «db» también vacía lo insertado por las migraciones.
    apply_seed(db)
    codes = {p["code"] for p in client.get(f"{A}/permissions", headers=h).json()["items"]}
    assert {"sales.sell", "sales.void", "admin.roles", "admin.audit",
            "admin.backups"} <= codes


# ---------------------------------------------------------------------------
# Terminales
# ---------------------------------------------------------------------------
def test_terminales_crud_y_baja_logica(db, client):
    h = _headers(db, client)
    terminal_id = client.post(f"{A}/terminals", headers=h,
                              json={"code": "T-1", "name": "Barra"}).json()["id"]
    assert client.post(f"{A}/terminals", headers=h,
                       json={"code": "T-1", "name": "Duplicada"}).status_code == 409

    resp = client.patch(f"{A}/terminals/{terminal_id}", headers=h, json={"name": "Barra alta"})
    assert resp.status_code == 200 and resp.json()["name"] == "Barra alta"

    assert client.delete(f"{A}/terminals/{terminal_id}", headers=h).status_code == 204
    activas = client.get(f"{A}/terminals", headers=h).json()["items"]
    assert all(t["id"] != terminal_id for t in activas)
    todas = client.get(f"{A}/terminals", headers=h,
                       params={"include_inactive": True}).json()["items"]
    baja = next(t for t in todas if t["id"] == terminal_id)
    assert baja["active"] is False
    # el código sigue ocupado: no se recicla
    assert client.post(f"{A}/terminals", headers=h,
                       json={"code": "T-1", "name": "Reciclada"}).status_code == 409
    assert "admin.terminal_deactivated" in _audit_actions(db)


# ---------------------------------------------------------------------------
# Dispositivos: token solo en alta/rotación
# ---------------------------------------------------------------------------
def test_alta_y_rotacion_entregan_token_una_vez(db, client):
    h = _headers(db, client)
    resp = client.post(f"{A}/devices", headers=h, json={"name": "Agente barra", "kind": "agent"})
    assert resp.status_code == 201, resp.text
    device, token = resp.json()["device"], resp.json()["token"]
    assert token and device["active"] is True

    items = client.get(f"{A}/devices", headers=h).json()["items"]
    assert len(items) == 1
    assert "token" not in items[0] and "token_hash" not in items[0]  # NUNCA en listados

    resp = client.post(f"{A}/devices/{device['id']}/rotate-token", headers=h)
    assert resp.status_code == 201
    assert resp.json()["token"] != token

    actions = _audit_actions(db)
    assert "admin.device_enrolled" in actions
    assert "admin.device_token_rotated" in actions


def test_dispositivo_con_terminal_valida_estado(db, client):
    h = _headers(db, client)
    terminal_id = client.post(f"{A}/terminals", headers=h,
                              json={"code": "T-1", "name": "Barra"}).json()["id"]

    resp = client.post(f"{A}/devices", headers=h,
                       json={"name": "Pinpad", "kind": "pinpad", "terminal_id": terminal_id})
    assert resp.status_code == 201
    assert resp.json()["device"]["terminal_id"] == terminal_id

    inactive = client.post(f"{A}/terminals", headers=h,
                           json={"code": "T-2", "name": "Baja"}).json()["id"]
    client.delete(f"{A}/terminals/{inactive}", headers=h)
    assert client.post(f"{A}/devices", headers=h, json={
        "name": "Huerfano", "kind": "printer", "terminal_id": inactive,
    }).status_code == 422
    assert client.post(f"{A}/devices", headers=h, json={
        "name": "Fantasma", "kind": "printer", "terminal_id": str(uuid4()),
    }).status_code == 404

    device_id = resp.json()["device"]["id"]
    assert client.delete(f"{A}/devices/{device_id}", headers=h).status_code == 204
    items = client.get(f"{A}/devices", headers=h).json()["items"]
    assert all(d["id"] != device_id for d in items)


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------
def test_auditoria_filtro_por_prefijo_y_pagina(db, client):
    h = _headers(db, client)
    client.post(f"{A}/terminals", headers=h, json={"code": "T-1", "name": "Barra"})
    client.post(f"{A}/users", headers=h, json={
        "username": "alguien", "password": "Clave-Segura-2026",
        "full_name": "Alguien", "role_code": "role-cajero",
    })

    resp = client.get(f"{A}/audit", headers=h, params={"action": "admin."})
    assert resp.status_code == 200
    page = resp.json()
    assert page["total"] >= 2 and page["limit"] == 50
    assert all(e["action"].startswith("admin.") for e in page["items"])
    # el actor se resuelve con el nombre de usuario
    assert all(e["username"] == "cajero" for e in page["items"])

    subset = client.get(f"{A}/audit", headers=h,
                        params={"action": "admin.user_created"}).json()
    assert subset["total"] == 1
    assert subset["items"][0]["after_data"]["username"] == "alguien"

    vacio = client.get(f"{A}/audit", headers=h, params={"action": "zzz."}).json()
    assert vacio["total"] == 0 and vacio["items"] == []


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
def test_listado_vacio_y_503_sin_pg_dump(db, client, monkeypatch):
    h = _headers(db, client)
    assert client.get(f"{A}/backups", headers=h).json() == {"items": []}

    monkeypatch.setattr(backups_service, "find_pg_dump", lambda: None)
    resp = client.post(f"{A}/backups/run", headers=h)
    assert resp.status_code == 503
    assert resp.json()["code"] == "BACKUP_UNAVAILABLE"
    # y el listado sigue vacío: no hay fichero fantasma
    assert client.get(f"{A}/backups", headers=h).json() == {"items": []}


def test_backup_completo_cuando_hay_pg_dump(db, client, app):
    if backups_service.find_pg_dump() is None:
        pytest.skip("pg_dump no disponible en esta máquina")
    h = _headers(db, client)
    resp = client.post(f"{A}/backups/run", headers=h)
    assert resp.status_code == 201, resp.text
    info = resp.json()
    assert info["name"].startswith("tpv_") and info["name"].endswith(".dump")
    assert info["size_bytes"] > 0

    listing = client.get(f"{A}/backups", headers=h).json()["items"]
    assert [b["name"] for b in listing] == [info["name"]]
    assert "admin.backup_created" in _audit_actions(db)

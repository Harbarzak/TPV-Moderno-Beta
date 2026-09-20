"""Tests E2E de autenticación y permisos (fase 03) contra PostgreSQL real.

Cubren la lista exigida: login correcto, login incorrecto, token expirado, refresh
(rotación + reuso falla), revocación, permisos insuficientes y acceso autorizado; más
extras de la fase: usuario inactivo, PIN con alcance ``pos``, rate limiting, cookie
HttpOnly y auditoría en ``audit_log``.

Requieren ``TPV_TEST_DATABASE_URL`` (skip limpio sin ella, misma convención que el
resto de tests de integración). Cada test parte de tablas vacías (fixture ``db``).
El access token viaja en la cabecera ``Authorization``; el refresh, en la cookie.
"""

import os
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies import require_permission
from app.core.config import Settings
from app.core.security import create_access_token, hash_password
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
V1 = "/api/v1"
AUTH = f"{V1}/auth"
REFRESH_COOKIE = "tpv_refresh"
# Secreto de prueba nuevo y rotado (≥32 caracteres); nunca vive en el código de la app.
SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"
CLAVE = "Clave-Segura-2026"

RUTA_ADMIN = f"{V1}/_test/admin"  # ruta de prueba que exige admin.users


def _admin_de_prueba() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# Fixtures y fábricas mínimas (roles/permisos/usuarios por test)
# ---------------------------------------------------------------------------
def _settings(**overrides) -> Settings:
    base = dict(
        env="test",
        log_level="WARNING",
        database_url=TEST_DB_URL,
        # TestClient usa un event loop por petición: sin pool no hay conexiones
        # colgando de un loop anterior.
        db_null_pool=True,
        jwt_secret=SECRET,
        auth_rate_limit_attempts=50,  # holgado: el límite se prueba aparte
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
        test_client.app.add_api_route(
            RUTA_ADMIN,
            _admin_de_prueba,
            methods=["GET"],
            name="admin_de_prueba",
            dependencies=[Depends(require_permission("admin.users"))],
        )
        yield test_client


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
    active: bool = True,
) -> int:
    return db.execute(
        text(
            "INSERT INTO users (username, password_hash, pin_hash, full_name, role_id, active) "
            "VALUES (:u, :ph, :pih, :fn, :r, :a) RETURNING id"
        ),
        {
            "u": username,
            "ph": hash_password(password),
            "pih": hash_password(pin) if pin else None,
            "fn": "Usuario de Prueba",
            "r": role_id,
            "a": active,
        },
    ).scalar_one()


def _login(client, username: str = "jefe", password: str = CLAVE):
    return client.post(f"{AUTH}/login", json={"username": username, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1 · Login correcto (+ sesión, cookie HttpOnly y auditoría)
# ---------------------------------------------------------------------------
def test_login_correcto(db, client):
    role_id = _mk_role(db, "admin", ("admin.users", "sales.charge"))
    _mk_user(db, role_id, "jefe")

    resp = _login(client, "jefe")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900  # access token de 15 minutos (§6)
    assert body["access_token"]
    assert body["user"]["username"] == "jefe"
    assert body["user"]["role"] == "admin"
    assert set(body["user"]["permissions"]) == {"admin.users", "sales.charge"}

    set_cookie = resp.headers["set-cookie"].lower()
    assert f"{REFRESH_COOKIE}=" in set_cookie
    assert "httponly" in set_cookie
    assert "path=/api/v1/auth" in set_cookie
    assert "secure" not in set_cookie  # solo fuera de dev/test

    # Sesión creada, último acceso anotado y auditoría del acceso.
    assert db.execute(text("SELECT count(*) FROM user_sessions")).scalar_one() == 1
    assert db.execute(
        text("SELECT last_login_at IS NOT NULL FROM users WHERE username = 'jefe'")
    ).scalar_one()
    actions = db.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
    assert actions == ["auth.login"]

    # El access token recién emitido da acceso.
    assert client.get(f"{AUTH}/me", headers=_bearer(body["access_token"])).status_code == 200


# ---------------------------------------------------------------------------
# 2 · Login incorrecto (contraseña errónea, usuario inexistente, inactivo)
# ---------------------------------------------------------------------------
def test_login_incorrecto(db, client):
    role_id = _mk_role(db, "waiter", ("sales.charge",))
    _mk_user(db, role_id, "camarero")
    _mk_user(db, role_id, "borrado", active=False)

    resp = _login(client, "camarero", "contraseña-errónea")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "INVALID_CREDENTIALS"
    assert body["type"] == "urn:tpv:error:INVALID_CREDENTIALS"
    assert resp.headers["content-type"].startswith("application/problem+json")

    # Usuario inexistente o desactivado: mismo código y mismo mensaje
    # (no delatar cuentas válidas) y nunca cookie de sesión.
    missing = _login(client, "no-existe", "lo-que-sea")
    inactive = _login(client, "borrado", CLAVE)
    assert missing.status_code == inactive.status_code == 401
    assert missing.json()["detail"] == body["detail"]
    assert inactive.json()["detail"] == body["detail"]
    assert "set-cookie" not in missing.headers
    assert "set-cookie" not in inactive.headers

    reasons = db.execute(
        text(
            "SELECT after_data ->> 'reason' FROM audit_log "
            "WHERE action = 'auth.login_failed' ORDER BY id"
        )
    ).scalars().all()
    assert reasons == ["bad_password", "unknown_user", "inactive"]


# ---------------------------------------------------------------------------
# 3 · Token expirado
# ---------------------------------------------------------------------------
def test_token_expirado(db, client):
    role_id = _mk_role(db, "waiter")
    _mk_user(db, role_id, "camarero")
    _login(client, "camarero")

    expired = create_access_token(
        client.app.state.settings,
        user_id=uuid4(),
        session_id=uuid4(),
        role_code="waiter",
        ttl_minutes=-1,
    )
    resp = client.get(f"{AUTH}/me", headers=_bearer(expired))
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_EXPIRED"


# ---------------------------------------------------------------------------
# 4 · Refresh con rotación (el token viejo muere; reusarlo falla)
# ---------------------------------------------------------------------------
def test_refresh_rota_y_el_reuso_falla(db, client):
    role_id = _mk_role(db, "manager", ("admin.users",))
    _mk_user(db, role_id, "encargado")

    login = _login(client, "encargado")
    old_access = login.json()["access_token"]
    old_refresh = client.cookies[REFRESH_COOKIE]

    refreshed = client.post(f"{AUTH}/refresh")  # envía la cookie automáticamente
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]
    new_refresh = client.cookies[REFRESH_COOKIE]
    assert new_access != old_access
    assert new_refresh != old_refresh  # rotación: valor nuevo

    # El access nuevo funciona; el viejo quedó revocado por la rotación.
    assert client.get(f"{AUTH}/me", headers=_bearer(new_access)).status_code == 200
    old_me = client.get(f"{AUTH}/me", headers=_bearer(old_access))
    assert old_me.status_code == 401
    assert old_me.json()["code"] == "SESSION_REVOKED"

    # Reusar el refresh viejo (sin cookie nueva en la jarra) también falla.
    client.cookies.delete(REFRESH_COOKIE)
    reuse = client.post(f"{AUTH}/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "SESSION_REVOKED"

    # El refresh nuevo sigue siendo el único válido.
    assert client.post(f"{AUTH}/refresh", json={"refresh_token": new_refresh}).status_code == 200

    revoked = db.execute(
        text("SELECT revoked_at IS NOT NULL FROM user_sessions")
    ).scalars().all()
    assert revoked == [True, True, False]  # sesión 1 y 2 rotadas; la 3 viva


# ---------------------------------------------------------------------------
# 5 · Revocación (logout cierra la sesión: access y refresh dejan de valer)
# ---------------------------------------------------------------------------
def test_revocacion_logout(db, client):
    role_id = _mk_role(db, "waiter")
    _mk_user(db, role_id, "camarero")

    login = _login(client, "camarero")
    access = login.json()["access_token"]
    refresh_value = client.cookies[REFRESH_COOKIE]

    out = client.post(f"{AUTH}/logout")
    assert out.status_code == 200
    assert out.json() == {"status": "ok"}

    me = client.get(f"{AUTH}/me", headers=_bearer(access))
    assert me.status_code == 401
    assert me.json()["code"] == "SESSION_REVOKED"

    client.cookies.delete(REFRESH_COOKIE)
    reuse = client.post(f"{AUTH}/refresh", json={"refresh_token": refresh_value})
    assert reuse.status_code == 401
    assert reuse.json()["code"] == "SESSION_REVOKED"

    assert db.execute(
        text("SELECT count(*) FROM audit_log WHERE action = 'auth.logout'")
    ).scalar_one() == 1


# ---------------------------------------------------------------------------
# 6 · Permisos insuficientes (RBAC granular)
# ---------------------------------------------------------------------------
def test_permisos_insuficientes(db, client):
    role_id = _mk_role(db, "waiter", ("sales.charge",))  # sin admin.*
    _mk_user(db, role_id, "camarero")
    access = _login(client, "camarero").json()["access_token"]

    resp = client.get(RUTA_ADMIN, headers=_bearer(access))
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "PERMISSION_DENIED"
    assert body["type"] == "urn:tpv:error:PERMISSION_DENIED"

    # Su token sigue siendo válido para lo que no exige permiso.
    assert client.get(f"{AUTH}/me", headers=_bearer(access)).status_code == 200


# ---------------------------------------------------------------------------
# 7 · Acceso autorizado
# ---------------------------------------------------------------------------
def test_acceso_autorizado(db, client):
    role_id = _mk_role(db, "manager", ("admin.users",))
    _mk_user(db, role_id, "encargado")
    access = _login(client, "encargado").json()["access_token"]

    resp = client.get(RUTA_ADMIN, headers=_bearer(access))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    me = client.get(f"{AUTH}/me", headers=_bearer(access)).json()
    assert me["username"] == "encargado"
    assert me["role"] == "manager"
    assert "admin.users" in me["permissions"]


# ---------------------------------------------------------------------------
# Extras de la fase
# ---------------------------------------------------------------------------
def test_acceso_sin_token_o_token_basura(db, client):
    role_id = _mk_role(db, "waiter")
    _mk_user(db, role_id, "camarero")

    missing = client.get(f"{AUTH}/me")
    assert missing.status_code == 401
    assert missing.json()["code"] == "AUTH_REQUIRED"
    assert missing.headers["www-authenticate"] == "Bearer"

    bad = client.get(f"{AUTH}/me", headers=_bearer("esto-no-es-un-jwt"))
    assert bad.status_code == 401
    assert bad.json()["code"] == "TOKEN_INVALID"

    # Firmado con otro secreto: formato válido, firma no.
    otras = create_access_token(
        Settings(env="test", jwt_secret="otro-secreto-nuevo-y-rotado-largo-1111"),
        user_id=uuid4(), session_id=uuid4(), role_code="waiter",
    )
    forged = client.get(f"{AUTH}/me", headers=_bearer(otras))
    assert forged.status_code == 401
    assert forged.json()["code"] == "TOKEN_INVALID"


def test_pin_alcance_pos_nunca_administra(db, client):
    role_id = _mk_role(db, "manager", ("admin.users",))
    _mk_user(db, role_id, "encargado", pin="4729")
    _mk_user(db, role_id, "sin-pin", pin=None)

    resp = client.post(f"{AUTH}/pin", json={"username": "encargado", "pin": "4729"})
    assert resp.status_code == 200
    access = resp.json()["access_token"]

    me = client.get(f"{AUTH}/me", headers=_bearer(access)).json()
    assert me["scope"] == "pos"

    # Rol con admin.users, pero token de PIN: la administración queda bloqueada (§6).
    admin_route = client.get(RUTA_ADMIN, headers=_bearer(access))
    assert admin_route.status_code == 403
    assert admin_route.json()["code"] == "PERMISSION_DENIED"

    # PIN erróneo y usuario sin PIN: credenciales inválidas.
    assert client.post(f"{AUTH}/pin", json={"username": "encargado", "pin": "0000"}).status_code == 401
    assert client.post(f"{AUTH}/pin", json={"username": "sin-pin", "pin": "4729"}).status_code == 401


def test_refresh_sin_token(db, client):
    resp = client.post(f"{AUTH}/refresh")
    assert resp.status_code == 401
    assert resp.json()["code"] == "TOKEN_INVALID"


def test_rate_limit_login(engine):
    app = create_app(
        _settings(auth_rate_limit_attempts=2, auth_rate_limit_window_seconds=60)
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        # Los dos primeros intentos pasan (fallan por credenciales); el tercero no.
        assert client.post(f"{AUTH}/login", json={"username": "nadie", "password": "x"}).status_code == 401
        assert client.post(f"{AUTH}/login", json={"username": "nadie", "password": "x"}).status_code == 401
        third = client.post(f"{AUTH}/login", json={"username": "nadie", "password": "x"})
        assert third.status_code == 429
        assert third.json()["code"] == "RATE_LIMITED"
        assert third.headers["retry-after"] == "60"

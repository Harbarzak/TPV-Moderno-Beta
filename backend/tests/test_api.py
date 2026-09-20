"""Tests de la API base (fase Backend base).

Cubren: healthchecks, errores RFC 9457 con ``code`` estable, CORS, request_id y las
rutas versionadas. No requieren PostgreSQL: el único test con BD real se salta si
``TPV_TEST_DATABASE_URL`` no está definida (misma convención que test_integrity.py).
"""

import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import AppError
from app.main import create_app

TEST_DB_URL = os.environ.get("TPV_TEST_DATABASE_URL", "")
CORS_ORIGIN = "http://localhost:5173"

V1 = "/api/v1"


def _make_app(database_url: str = ""):
    return create_app(
        Settings(
            env="test",
            log_level="WARNING",
            database_url=database_url,
            # TestClient usa un event loop por petición: sin pool no hay conexiones
            # colgando de un loop anterior.
            db_null_pool=True,
            cors_origins=[CORS_ORIGIN],
        )
    )


@pytest.fixture()
def cliente():
    app = _make_app()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_healthz_ok(cliente):
    resp = cliente.get(f"{V1}/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["env"] == "test"
    assert body["version"]


def test_readyz_503_sin_bd(cliente):
    resp = cliente.get(f"{V1}/readyz")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "DATABASE_UNAVAILABLE"
    assert body["status"] == 503


@pytest.mark.skipif(not TEST_DB_URL, reason="requiere TPV_TEST_DATABASE_URL")
def test_readyz_ok_con_bd():
    app = _make_app(TEST_DB_URL)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"{V1}/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_404_es_problem_json(cliente):
    resp = cliente.get(f"{V1}/no-existe")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "NOT_FOUND"
    assert body["type"] == "urn:tpv:error:NOT_FOUND"


def test_error_validacion_422(cliente):
    @cliente.app.get(f"{cliente.app.state.settings.api_prefix}/_test/eco")
    def _eco(n: int) -> dict:
        return {"n": n}

    resp = cliente.get(f"{V1}/_test/eco")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "n" in body["detail"]


def test_app_error_code_estable(cliente):
    @cliente.app.get(f"{cliente.app.state.settings.api_prefix}/_test/conflicto")
    def _conflicto() -> None:
        raise AppError(409, "SALE_ALREADY_PAID", "El ticket ya está cobrado")

    resp = cliente.get(f"{V1}/_test/conflicto")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "SALE_ALREADY_PAID"
    assert body["detail"] == "El ticket ya está cobrado"
    assert body["title"] == "Conflict"


def test_error_no_controlado_500(cliente):
    @cliente.app.get(f"{cliente.app.state.settings.api_prefix}/_test/fallo")
    def _fallo() -> None:
        raise RuntimeError("fallo interno de prueba")

    resp = cliente.get(f"{V1}/_test/fallo")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
    # Nunca filtrar detalles internos al cliente.
    assert "fallo interno" not in resp.text


def test_cors_origen_permitido(cliente):
    resp = cliente.get(f"{V1}/healthz", headers={"Origin": CORS_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == CORS_ORIGIN
    exposed = resp.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed


def test_cors_origen_rechazado(cliente):
    resp = cliente.get(f"{V1}/healthz", headers={"Origin": "http://ajeno.example"})
    assert "access-control-allow-origin" not in resp.headers


def test_request_id_eco_y_generacion(cliente):
    resp = cliente.get(f"{V1}/healthz", headers={"X-Request-ID": "peticion-prueba"})
    assert resp.headers["x-request-id"] == "peticion-prueba"

    resp = cliente.get(f"{V1}/healthz")
    UUID(resp.headers["x-request-id"])  # uuid4 válido


def test_openapi_expone_rutas_v1(cliente):
    paths = cliente.get("/openapi.json").json()["paths"]
    assert f"{V1}/healthz" in paths
    assert f"{V1}/readyz" in paths

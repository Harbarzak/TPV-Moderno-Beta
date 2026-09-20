"""Muro de autenticación: toda operación de negocio exige credenciales (fase Testing).

Recorre el contrato OpenAPI real (``app.openapi()``) y llama cada operación HTTP
sin cabecera ``Authorization`` ni cookie: la respuesta debe ser **401
AUTH_REQUIRED** y llegar antes de tocar PostgreSQL. Automatiza el inventario que
la fase Seguridad comprobó a mano (toda ruta de negocio con su permiso declarado)
y lo convierte en regresión: cualquier ruta que se publique sin dependencia de
auth falla aquí, sin mantenimiento manual del listado.

Exclusiones justificadas (públicas por diseño):
- ``GET /healthz`` y ``GET /readyz``: sondas de vida/preparación, sin secretos.
- ``POST /auth/login`` y ``POST /auth/pin``: cómo se OBTIENEN las credenciales.
- ``POST /auth/logout``: idempotente por diseño (fase 03) — sin sesión, 200.

No requiere BD: la dependencia de auth se resuelve antes que la sesión de datos.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.main import create_app

V1 = "/api/v1"
# Secreto de test desechable (ADR-008: jamás un valor real del proyecto).
TEST_SECRET = "secreto-de-tests-nuevo-y-rotado-64-chars-000000"

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
PUBLIC_OPERATIONS = {
    ("GET", f"{V1}/healthz"),
    ("GET", f"{V1}/readyz"),
    ("POST", f"{V1}/auth/login"),
    ("POST", f"{V1}/auth/pin"),
    ("POST", f"{V1}/auth/logout"),
}
PATH_PARAM = re.compile(r"\{[^}]+\}")
# Los parámetros de path se sustituyen por un UUID sintáctico válido: la ruta
# casa igual y el 401 del muro salta antes de validar el parámetro.
DUMMY_PARAM = "00000000-0000-0000-0000-000000000000"
# Código estable esperado en el 401. Por defecto AUTH_REQUIRED (sin Bearer);
# refresh autentica por cookie de refresco y, al no haberla, el propio
# endpoint responde 401 TOKEN_INVALID (fase 03) — muro cubierto igualmente.
EXPECTED_CODE_BY_OPERATION = {
    ("POST", f"{V1}/auth/refresh"): ErrorCode.TOKEN_INVALID,
}
DEFAULT_EXPECTED_CODE = ErrorCode.AUTH_REQUIRED


def _make_app():
    return create_app(
        Settings(
            env="test",
            log_level="WARNING",
            db_null_pool=True,
            jwt_secret=TEST_SECRET,
        )
    )


def _business_operations() -> list[tuple[str, str, str]]:
    """(método, URL con parámetros sustituidos, plantilla OpenAPI) de negocio."""
    paths = _make_app().openapi()["paths"]
    operations = []
    for template, methods in paths.items():
        for method in methods:
            if method not in HTTP_METHODS:
                continue
            if (method.upper(), template) in PUBLIC_OPERATIONS:
                continue
            url = PATH_PARAM.sub(DUMMY_PARAM, template)
            operations.append((method.upper(), url, template))
    return operations


_OPERATIONS = _business_operations()
_OPERATION_IDS = [f"{method} {template}" for method, _, template in _OPERATIONS]


@pytest.fixture(scope="module")
def client():
    """Una sola app/cliente para todo el muro (~90 peticiones sin BD)."""
    with TestClient(_make_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(("method", "url", "template"), _OPERATIONS, ids=_OPERATION_IDS)
def test_operacion_de_negocio_exige_credenciales(client, method: str, url: str, template: str):
    """Sin credenciales, cada operación de negocio responde 401 AUTH_REQUIRED."""
    response = client.request(method, url)
    assert response.status_code == 401, (
        f"{method} {template} respondió {response.status_code} sin credenciales: "
        "el muro de autenticación no cubre esta ruta"
    )
    expected = EXPECTED_CODE_BY_OPERATION.get((method, template), DEFAULT_EXPECTED_CODE)
    assert response.json()["code"] == expected

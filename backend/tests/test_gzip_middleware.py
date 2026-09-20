"""Tests de compresión HTTP (fase Rendimiento).

Verifican el cableado de ``GZipMiddleware`` decidido por medición
(docs/performance/): los payloads grandes viajan comprimidos (catálogo POS,
informes) y las respuestas diminutas (healthz, problem+json) siguen sin
comprimir, porque gzip las engordaría. No requieren PostgreSQL.
"""

import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from app.core.config import Settings
from app.main import GZIP_COMPRESS_LEVEL, GZIP_MINIMUM_SIZE, create_app

V1 = "/api/v1"


def _make_app():
    return create_app(Settings(env="test", log_level="WARNING", db_null_pool=True))


def test_gzip_cableado_en_el_stack():
    """El middleware va montado en create_app con los parámetros medidos."""
    app = _make_app()
    gzs = [m for m in app.user_middleware if m.cls is GZipMiddleware]
    assert len(gzs) == 1
    assert gzs[0].kwargs == {
        "minimum_size": GZIP_MINIMUM_SIZE,
        "compresslevel": GZIP_COMPRESS_LEVEL,
    }


def test_respuesta_pequena_sin_gzip():
    """Por debajo del umbral no hay content-encoding (healthz ~45 B)."""
    with TestClient(_make_app()) as client:
        resp = client.get(f"{V1}/healthz", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


def test_respuesta_grande_se_comprime():
    """Con el mismo par de parámetros que create_app, un cuerpo ≥ 1 KiB viaja
    gzip y un cuerpo menor pasa en claro (comportamiento del umbral elegido)."""

    def build() -> FastAPI:
        app = FastAPI()
        app.add_middleware(
            GZipMiddleware,
            minimum_size=GZIP_MINIMUM_SIZE,
            compresslevel=GZIP_COMPRESS_LEVEL,
        )

        @app.get("/grande")
        def grande() -> JSONResponse:
            # Ítems del catálogo POS: JSON repetitivo, el caso medido (7,2x).
            items = [
                {"id": f"p{i:04d}", "name": f"Producto de barra {i:04d}", "price": f"{i % 9 + 1}.50"}
                for i in range(120)
            ]
            return JSONResponse({"items": items})

        @app.get("/pequena")
        def pequena() -> JSONResponse:
            return JSONResponse({"status": "ok"})

        return app

    with TestClient(build()) as client:
        headers = {"Accept-Encoding": "gzip"}
        grande_resp = client.get("/grande", headers=headers)
        pequena_resp = client.get("/pequena", headers=headers)

    assert len(json.dumps({"items": [
        {"id": f"p{i:04d}", "name": f"Producto de barra {i:04d}", "price": f"{i % 9 + 1}.50"}
        for i in range(120)
    ]})) > GZIP_MINIMUM_SIZE
    assert grande_resp.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in pequena_resp.headers

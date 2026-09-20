"""Frontends servidos por el propio servidor (fase Instalador, §1.4).

El instalador apunta ``TPV_SERVE_FRONTEND_DIR`` / ``TPV_SERVE_MOBILE_DIR`` /
``TPV_SERVE_ADMIN_DIR`` a los builds estáticos y la API los publica en
``/app/tpv``, ``/app/movil`` y ``/app/admin``: los clientes llaman por rutas
relativas, así que «apuntar el navegador a la IP del servidor» es toda la
configuración del terminal. Sin carpetas configuradas la app es solo API: el
comportamiento de desarrollo y tests no cambia.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

V1 = "/api/v1"


def _settings(**overrides) -> Settings:
    return Settings(env="test", log_level="WARNING", db_null_pool=True, **overrides)


def test_sin_carpetas_configuradas_solo_api():
    """Por defecto no hay monturas estáticas (compatibilidad total con lo previo)."""
    app = create_app(_settings())
    assert not any(getattr(r, "path", "").startswith("/app/") for r in app.routes)


def test_mostrador_movil_y_admin_sirven_index_y_assets(tmp_path):
    """Con carpetas configuradas, cada montaje sirve index.html y sus assets."""
    tpv = tmp_path / "tpv"
    movil = tmp_path / "movil"
    admin = tmp_path / "admin"
    for root in (tpv, movil, admin):
        (root / "assets").mkdir(parents=True)
        (root / "index.html").write_text(
            "<!doctype html><html><body>front</body></html>", encoding="utf-8"
        )
        (root / "assets" / "app.js").write_text("export {};", encoding="utf-8")

    app = create_app(
        _settings(
            serve_frontend_dir=str(tpv),
            serve_mobile_dir=str(movil),
            serve_admin_dir=str(admin),
        )
    )
    with TestClient(app) as client:
        # La API sigue viva codo con codo con los frontends.
        assert client.get(f"{V1}/healthz").status_code == 200
        for mount in ("/app/tpv", "/app/movil", "/app/admin"):
            index = client.get(f"{mount}/")
            assert index.status_code == 200
            assert "front" in index.text
            asset = client.get(f"{mount}/assets/app.js")
            assert asset.status_code == 200
            assert "export" in asset.text
        # index.html implícito en la raíz del montaje (html=True).
        sin_barra = client.get("/app/tpv")
        assert sin_barra.status_code == 200


def test_carpeta_inexistente_falla_al_arrancar(tmp_path):
    """Ruta mal escrita en el .env → error en el arranque (check_dir del
    montaje), no en la primera petición de un camarero."""
    with pytest.raises(RuntimeError):
        create_app(_settings(serve_frontend_dir=str(tmp_path / "no-existe")))

"""Lanzador del servidor API (el punto de entrada de producción en Windows).

psycopg en modo async solo funciona sobre un ``SelectorEventLoop``: el
``ProactorEventLoop`` que Python y uvicorn eligen por defecto en win32 no
tiene ``add_reader`` y toda consulta a la BD falla (hallazgo del QA de la
fase 24). En Linux da igual, pero el instalador Windows (deploy\install.ps1)
arranca este módulo, así que aquí se fija el loop ANTES de crear el servidor.

Se usa la API pública de uvicorn (Config + Server) y el ``loop_factory`` de
``asyncio.run``: sin parchear internos de uvicorn ni tocar la política global.
El host y el puerto llegan por entorno (TPV_BIND / TPV_PORT), que es como
deploy\start-server.ps1 exporta la configuración de conf\\.env.

Arranque: ``python -m app.serve`` con el directorio de trabajo en ``backend``.
"""

import asyncio
import os

import uvicorn


def _loop_factory() -> asyncio.AbstractEventLoop:
    """SelectorEventLoop siempre: es el único que psycopg async soporta."""
    return asyncio.SelectorEventLoop()


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host=os.environ.get("TPV_BIND", "0.0.0.0"),
        port=int(os.environ.get("TPV_PORT", "8000")),
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve(), loop_factory=_loop_factory)


if __name__ == "__main__":
    main()

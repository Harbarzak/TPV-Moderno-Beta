"""Punto de entrada FastAPI: fábrica ``create_app()`` + instancia ``app`` para uvicorn.

Fase Backend base: configuración por entorno, logging JSON con request_id, errores
RFC 9457, engine async perezoso hacia PostgreSQL, healthchecks y CORS. Fase 03 añade
autenticación (``/api/v1/auth``) con limitador de intentos por instancia de app.
La lógica TPV (ventas, caja, impresión…) llega en fases posteriores como servicios.

Capas (ARCHITECTURE.md §2): ``api → services → domain``; ``repos`` y ``adapters`` son
detalles inyectados. Los servicios nunca importan de ``api``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from app import __version__
from app.adapters.fiscal import build_fiscal_adapter
from app.adapters.hardware import HardwareAdapters
from app.adapters.printing import NullPrinterAdapter
from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.events import EventBus
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.ratelimit import RateLimiter
from app.db.session import dispose_engine

logger = get_logger("tpv.app")

# Compresión HTTP (fase Rendimiento, docs/performance/): umbrales medidos, no
# intuición. 1 KiB deja fuera las respuestas diminutas (healthz, problem+json);
# level 6 iguala el ratio del 9 con ~20% menos CPU en los payloads medidos.
GZIP_MINIMUM_SIZE = 1024
GZIP_COMPRESS_LEVEL = 6


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("startup", env=settings.env, version=__version__)
    yield
    await dispose_engine()
    logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Fábrica de la aplicación (inyectable en tests y workers)."""
    cfg = settings or get_settings()
    configure_logging(cfg.log_level)

    app = FastAPI(
        title="TPV API",
        version=__version__,
        description=(
            "Backend LAN-first del TPV moderno. Los clientes nunca acceden a la base de "
            "datos: solo este servidor habla con PostgreSQL (ADR-002)."
        ),
        lifespan=lifespan,
    )
    app.state.settings = cfg
    # Anti-abuso de login/pin: una instancia por app (aislada entre tests).
    app.state.rate_limiter = RateLimiter(
        cfg.auth_rate_limit_attempts, cfg.auth_rate_limit_window_seconds
    )
    # DeviceAdapter de impresión (fase 10): los drivers reales —térmica ESC/POS,
    # Windows, tpv-agent— implementarán PrinterAdapter en la fase 13.
    app.state.print_adapter = NullPrinterAdapter()
    # Periféricos (fase 11): cajón, escáner, pinpad, CashDro y display de
    # cliente como Protocolos sustituibles; sin drivers reales aún (fases 13-14).
    app.state.hardware = HardwareAdapters.defaults()
    # Hub WebSocket (fase 12): pub/sub en memoria (§8.3); los servicios solo
    # registran eventos en su transacción y core.events difunde tras el commit.
    app.state.event_bus = EventBus()
    # Adaptador fiscal (fase 34, ADR-010): plugin desacoplado del motor de
    # ventas. Un proveedor mal escrito (TPV_FISCAL_PROVIDER) falla AQUÍ, al
    # desplegar — no en la primera descarga fiscal.
    app.state.fiscal_adapter = build_fiscal_adapter(cfg.fiscal_provider)

    # Orden: RequestContext (interno) → GZip (medio) → CORS (externo, preflights).
    # Compresión medida en la fase Rendimiento (docs/performance/): el snapshot de
    # catálogo (~94 kB por 250 productos) baja a ~13 kB con gzip (7,2x) por ~2 ms
    # de CPU; level 6 iguala al 9 con menos coste. minimum_size=1024 deja sin
    # comprimir las respuestas diminutas (healthz, problem+json), donde gzip
    # engordaría el cuerpo. WS no pasa por este middleware (solo HTTP).
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        GZipMiddleware,
        minimum_size=GZIP_MINIMUM_SIZE,
        compresslevel=GZIP_COMPRESS_LEVEL,
    )
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[REQUEST_ID_HEADER],
        )

    register_error_handlers(app)
    app.include_router(api_v1_router, prefix=cfg.api_prefix)

    # Frontends servidos por el propio servidor (ARCHITECTURE.md §1.4): el
    # instalador apunta estas rutas a los builds estáticos y «actualizar» pasa
    # a ser «actualizar el servidor». Montaje condicional: sin carpeta
    # configurada la app es solo API (tests, desarrollo). html=True sirve
    # index.html en la raíz de cada montaje; los frontends usan rutas
    # relativas (API incluida), así que no necesitan más configuración.
    static_mounts = (
        ("/app/tpv", cfg.serve_frontend_dir, "tpv-frontend"),
        ("/app/movil", cfg.serve_mobile_dir, "tpv-mobile"),
        ("/app/admin", cfg.serve_admin_dir, "tpv-admin"),
    )
    for mount_path, directory, name in static_mounts:
        if directory:
            # check_dir por defecto: una ruta mal escrita en el .env falla
            # en el arranque, no en la primera petición de un camarero.
            app.mount(mount_path, StaticFiles(directory=directory, html=True), name=name)
    return app


app = create_app()

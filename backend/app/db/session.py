"""Acceso a datos: engine async perezoso + fábrica de sesiones (ADR-001: SQLAlchemy 2.x async).

El engine se crea la primera vez que se necesita, nunca en import: la aplicación arranca
aunque no haya ``TPV_DATABASE_URL`` y ``/readyz`` lo reporta (arranque tolerante, §12.2).
URL asíncrona esperada: ``postgresql+psycopg://usuario:clave@host:5432/tpv``.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.events import attach_bus

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Engine único de la aplicación; se crea al primer uso."""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        if not cfg.database_url:
            raise RuntimeError(
                "TPV_DATABASE_URL no está configurada: no hay conexión a PostgreSQL"
            )
        engine_kwargs: dict = {"echo": cfg.db_echo}
        if cfg.db_null_pool:
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs.update(
                pool_pre_ping=True,
                pool_size=cfg.db_pool_size,
                max_overflow=cfg.db_max_overflow,
            )
        _engine = create_async_engine(cfg.database_url, **engine_kwargs)
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Fábrica de sesiones por petición."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    return _session_factory


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Dependencia FastAPI: una sesión por petición (rollback explícito en error).

    Usa los settings inyectados en la app (``app.state.settings``): la misma fábrica
    sirve para la app por defecto y para instancias de prueba con overrides.
    """
    factory = get_session_factory(request.app.state.settings)
    session = factory()
    # Fase 12: el bus de eventos viaja con la sesión (session.info) — los
    # servicios solo llaman record() y core.events difunde tras el commit.
    attach_bus(session, getattr(request.app.state, "event_bus", None))
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database(settings: Settings | None = None) -> bool:
    """True si hay BD configurada y responde a ``SELECT 1`` (readiness)."""
    try:
        factory = get_session_factory(settings)
    except RuntimeError:
        return False
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engine() -> None:
    """Cierra el pool y olvida el estado (apagado y tests)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None

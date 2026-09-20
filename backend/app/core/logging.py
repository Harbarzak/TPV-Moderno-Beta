"""Logging estructurado JSON con ``request_id`` propagado (ARCHITECTURE.md §11).

Toda la app (y los logs stdlib de uvicorn/sqlalchemy) sale por stdout en JSON, una línea
por evento, con el ``request_id`` de la petición actual cuando existe (ver middleware).
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configura structlog + logging stdlib para emitir JSON por stdout."""
    level_value = getattr(logging, level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level_value)

    # uvicorn instala sus propios handlers con formato texto: los retiramos para que
    # todo pase por el raíz (mismo formato JSON, mismo request_id).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str = "tpv") -> structlog.stdlib.BoundLogger:
    """Logger estructurado de la aplicación."""
    return structlog.get_logger(name)

"""Healthchecks del sistema (ARCHITECTURE.md §7.2 y §11).

- ``GET /healthz``: liveness — el proceso responde; no toca la BD.
- ``GET /readyz``: readiness — la BD responde; 503 problem+json si no (monitor/instalador).
"""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app import __version__
from app.core.config import Settings
from app.core.errors import ErrorCode, problem_response
from app.db.session import check_database

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    env: str
    version: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz(request: Request) -> HealthResponse:
    """Liveness: proceso vivo. No depende de la base de datos."""
    settings: Settings = request.app.state.settings
    return HealthResponse(status="ok", env=settings.env, version=__version__)


@router.get("/readyz", responses={503: {"description": "Base de datos no accesible"}})
async def readyz(request: Request) -> Response:
    """Readiness: BD accesible. Sin BD configurada o sin respuesta → 503 problem+json."""
    settings: Settings = request.app.state.settings
    if await check_database(settings):
        return Response(
            content='{"status":"ready"}',
            media_type="application/json",
        )
    return problem_response(
        503,
        ErrorCode.DATABASE_UNAVAILABLE,
        "La base de datos no está configurada o no responde",
    )

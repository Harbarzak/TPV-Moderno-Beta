"""Autenticación y permisos (fase 03): ``/api/v1/auth``.

- ``POST /auth/login``   usuario+contraseña → JWT de acceso + refresh en cookie HttpOnly
- ``POST /auth/pin``     PIN de camarero → JWT con alcance ``pos`` (venta, no administración)
- ``POST /auth/refresh`` rotación: consume el refresh y emite uno nuevo (el viejo muere)
- ``POST /auth/logout``  revoca la sesión y borra la cookie
- ``GET  /auth/me``      identidad y permisos del token actual

El refresh viaja en cookie HttpOnly (``tpv_refresh``, limitada al propio router); los
clientes sin navegador (tpv-agent) pueden enviarlo también en el cuerpo. Login y PIN
llevan limitador de intentos por IP y ruta (anti-abuso, §6). Todos los errores salen
como problem+json con ``code`` estable.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.ratelimit import RateLimiter
from app.db.session import get_session_factory  # logout: sesión solo si hay algo que revocar
from app.services import auth as auth_service
from app.services.auth import REFRESH_COOKIE, LoginResult

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Peticiones y respuestas
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PinRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    pin: str = Field(pattern=r"^\d{4,8}$")  # PIN corto de terminal (§6)


class RefreshRequest(BaseModel):
    """Alternativa a la cookie para clientes sin navegador (tpv-agent)."""

    refresh_token: str | None = None


class UserResponse(BaseModel):
    id: UUID
    username: str
    full_name: str
    role: str
    permissions: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos de vida del access token
    user: UserResponse


class MeResponse(BaseModel):
    id: UUID
    username: str
    full_name: str
    role: str
    permissions: list[str]
    scope: str  # "full" (contraseña) | "pos" (PIN)


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------
def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _token_response(result: LoginResult) -> TokenResponse:
    p = result.principal
    return TokenResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        user=UserResponse(
            id=p.user_id,
            username=p.username,
            full_name=p.full_name,
            role=p.role_code,
            permissions=p.permission_list,
        ),
    )


def _set_refresh_cookie(request: Request, response: Response, result: LoginResult) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=result.refresh_token,
        max_age=result.refresh_max_age,
        httponly=True,  # inaccesible a JavaScript (§6)
        secure=_settings(request).env not in ("dev", "test"),
        samesite="strict",
        path=f"{_settings(request).api_prefix}/auth",
    )


def _client_ip(request: Request) -> str | None:
    host = request.client.host if request.client else None
    return auth_service.safe_ip(host)  # la columna es INET


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


async def _rate_limit(request: Request) -> None:
    """Anti-abuso por IP y ruta (§6). El estado vive en ``app.state``: aislado por tests."""
    limiter: RateLimiter = request.app.state.rate_limiter
    host = request.client.host if request.client else "desconocida"
    if not limiter.check(f"{request.url.path}:{host}"):
        window = _settings(request).auth_rate_limit_window_seconds
        raise AppError(
            429,
            ErrorCode.RATE_LIMITED,
            "Demasiados intentos: espere antes de reintentar",
            headers={"Retry-After": str(window)},
        )


def _refresh_from_request(request: Request, body: RefreshRequest | None = None) -> str:
    """Extrae el refresh (cookie o cuerpo) SIN tocar BD: si falta, 401 inmediato.

    El cuerpo es opcional: FastAPI resuelve las dependencias con ``yield`` (``get_db``)
    antes que las demás si el body es obligatorio, y entonces una petición sin token
    daría 500 en lugar de 401 cuando la BD no está disponible. Opcional = validación
    primero, BD después (mismo criterio que el JWT antes de la sesión).
    """
    token = request.cookies.get(REFRESH_COOKIE) or (body.refresh_token if body else None)
    if not token:
        raise AppError(401, ErrorCode.TOKEN_INVALID, "Falta el token de refresco")
    return token


def _optional_refresh(request: Request, body: RefreshRequest | None = None) -> str | None:
    """Igual que ``_refresh_from_request`` pero sin exigirlo (logout idempotente)."""
    return request.cookies.get(REFRESH_COOKIE) or (body.refresh_token if body else None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse, dependencies=[Depends(_rate_limit)])
async def login(
    body: LoginRequest, request: Request, response: Response, session: DbSession
) -> TokenResponse:
    result = await auth_service.login_password(
        session,
        _settings(request),
        username=body.username,
        password=body.password,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
    _set_refresh_cookie(request, response, result)
    return _token_response(result)


@router.post("/pin", response_model=TokenResponse, dependencies=[Depends(_rate_limit)])
async def pin(
    body: PinRequest, request: Request, response: Response, session: DbSession
) -> TokenResponse:
    result = await auth_service.login_pin(
        session,
        _settings(request),
        username=body.username,
        pin=body.pin,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
    _set_refresh_cookie(request, response, result)
    return _token_response(result)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    token: Annotated[str, Depends(_refresh_from_request)],
    request: Request,
    response: Response,
    session: DbSession,
) -> TokenResponse:
    result = await auth_service.refresh(
        session,
        _settings(request),
        refresh_token=token,
        ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
    _set_refresh_cookie(request, response, result)
    return _token_response(result)


@router.post("/logout")
async def logout(
    token: Annotated[str | None, Depends(_optional_refresh)],
    request: Request,
    response: Response,
) -> dict[str, str]:
    response.delete_cookie(REFRESH_COOKIE, path=f"{_settings(request).api_prefix}/auth")
    if not token:
        # Idempotente: sin token que revocar no hay nada que hacer (ni BD que tocar).
        return {"status": "ok"}
    # Con token sí hay que revocar: se abre sesión solo entonces; sin BD falla
    # honestamente (igual que login) en vez de fingir un cierre que no ocurrió.
    factory = get_session_factory(_settings(request))
    async with factory() as session:
        await auth_service.logout(session, refresh_token=token, ip=_client_ip(request))
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentUser) -> MeResponse:
    return MeResponse(
        id=principal.user_id,
        username=principal.username,
        full_name=principal.full_name,
        role=principal.role_code,
        permissions=principal.permission_list,
        scope=principal.scope,
    )

"""Dependencias FastAPI compartidas: usuario autenticado y permisos (fase 03).

Capa ``api``: traduce entre HTTP y ``services.auth``. El orden importa: primero se
verifican credenciales y firma del JWT (sin tocar la BD, 401 inmediato) y solo
después se abre sesión de BD para comprobar sesión viva, usuario activo y permisos.
``require_permission`` aplica el RBAC granular; con alcance ``pos`` (PIN) se bloquea
``admin.*`` (§6: el PIN nunca administra).
"""

from typing import Annotated, Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import SCOPE_FULL, decode_access_token
from app.db.session import get_db
from app.services.auth import Principal, load_principal

# auto_error=False: la ausencia de cabecera se traduce a problem+json, no al 403 por
# defecto de HTTPBearer (mantener el contrato RFC 9457 en TODOS los errores).
_bearer = HTTPBearer(auto_error=False, scheme_name="Access token")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _access_claims(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> dict[str, Any]:
    """Verifica cabecera Bearer, firma y expiración del JWT. Sin acceso a BD."""
    settings = request.app.state.settings
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Falta el token de acceso (cabecera Authorization: Bearer)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return decode_access_token(settings, credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise AppError(
            401,
            ErrorCode.TOKEN_EXPIRED,
            "El token de acceso ha caducado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.PyJWTError:
        raise AppError(
            401,
            ErrorCode.TOKEN_INVALID,
            "Token de acceso no válido",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


# Orden de parámetros = orden de resolución en FastAPI: los claims (JWT) se validan
# ANTES de abrir sesión de BD, así una petición sin credenciales responde 401 aunque
# la BD no esté configurada (nunca 500).
async def get_current_user(
    claims: Annotated[dict[str, Any], Depends(_access_claims)],
    session: DbSession,
) -> Principal:
    try:
        return await load_principal(session, claims=claims)
    except ValueError:  # UUID malformado en los claims: token no válido
        raise AppError(401, ErrorCode.TOKEN_INVALID, "Token de acceso no válido") from None


CurrentUser = Annotated[Principal, Depends(get_current_user)]


def require_permission(permission: str):
    """Fábrica de dependencia: exige un permiso del rol (RBAC granular)."""

    async def _check(principal: CurrentUser) -> Principal:
        if principal.scope != SCOPE_FULL and permission.startswith("admin."):
            raise AppError(
                403,
                ErrorCode.PERMISSION_DENIED,
                "El acceso con PIN no permite operaciones de administración",
            )
        if permission not in principal.permissions:
            raise AppError(
                403,
                ErrorCode.PERMISSION_DENIED,
                f"Falta el permiso requerido: {permission}",
            )
        return principal

    return _check

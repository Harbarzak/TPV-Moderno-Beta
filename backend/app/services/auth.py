"""Servicio de autenticación (fase 03): login, PIN, refresh con rotación, logout.

Política (ARCHITECTURE.md §6): Argon2id; JWT de acceso corto ligado a una sesión
revocable; refresh opaco rotativo en cookie HttpOnly; PIN de terminal con alcance
``pos`` (operación de venta, nunca administración); auditoría de accesos en
``audit_log``; mensajes genéricos para no delatar cuentas.

Nunca importa de ``api``: los controladores llaman a estas funciones.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.security import (
    SCOPE_FULL,
    SCOPE_POS,
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
    verify_dummy,
    verify_password,
)
from app.db.models.security import User
from app.repos import auth as auth_repo

logger = get_logger("tpv.auth")

REFRESH_COOKIE = "tpv_refresh"

# Errores como constantes de módulo: mismo cuerpo para todos los fallos de credencial.
_INVALID_CREDENTIALS = AppError(
    401, ErrorCode.INVALID_CREDENTIALS, "Credenciales inválidas"
)


@dataclass(frozen=True)
class Principal:
    """Quien actúa: identidad + permisos del rol + alcance del token."""

    user_id: UUID
    username: str
    full_name: str
    role_code: str
    permissions: frozenset[str]
    scope: str  # SCOPE_FULL | SCOPE_POS

    @property
    def permission_list(self) -> list[str]:
        return sorted(self.permissions)


@dataclass(frozen=True)
class LoginResult:
    """Resultado de login/pin/refresh: access en el cuerpo, refresh para la cookie."""

    access_token: str
    expires_in: int
    principal: Principal
    refresh_token: str
    refresh_max_age: int


def safe_ip(host: str | None) -> str | None:
    """La columna es INET: si lo que llega no es una IP válida, se guarda NULL."""
    if not host:
        return None
    try:
        return str(ip_address(host))
    except ValueError:
        return None


def _principal_from(user: User, permissions: list[str], scope: str) -> Principal:
    return Principal(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        role_code=user.role.code,
        permissions=frozenset(permissions),
        scope=scope,
    )


async def _issue_session(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    scope: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    """Camino común tras validar credenciales: sesión nueva + access + refresh."""
    refresh = new_refresh_token()
    now = datetime.now(UTC)
    row = await auth_repo.create_session(
        session,
        user_id=user.id,
        token_hash=hash_refresh_token(refresh),
        expires_at=now + timedelta(hours=settings.refresh_token_hours),
        ip=ip,
        user_agent=user_agent,
    )
    await auth_repo.touch_last_login(session, user.id)
    principal = _principal_from(
        user, await auth_repo.get_permission_codes(session, user.role_id), scope
    )
    await auth_repo.record_audit(
        session,
        action="auth.login",
        entity="user",
        user_id=user.id,
        entity_id=user.id,
        ip=ip,
        after_data={"username": user.username, "role": principal.role_code, "scope": scope},
    )
    await session.commit()
    return LoginResult(
        access_token=create_access_token(
            settings,
            user_id=user.id,
            session_id=row.id,
            role_code=principal.role_code,
            scope=scope,
        ),
        expires_in=settings.access_token_minutes * 60,
        principal=principal,
        refresh_token=refresh,
        refresh_max_age=settings.refresh_token_hours * 3600,
    )


async def login_password(
    session: AsyncSession,
    settings: Settings,
    *,
    username: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    user = await auth_repo.get_user_by_username(session, username)
    if user is None:
        # Sin usuario: verificar contra un hash señuelo (mismo tiempo de respuesta).
        verify_dummy(password)
        await auth_repo.record_audit(
            session,
            action="auth.login_failed",
            entity="user",
            ip=ip,
            after_data={"username": username, "reason": "unknown_user"},
        )
        await session.commit()
        raise _INVALID_CREDENTIALS

    audit_user = user.id
    if not user.active:
        verify_dummy(password)  # tampoco delatar cuentas desactivadas
        reason = "inactive"
    elif not verify_password(password, user.password_hash):
        reason = "bad_password"
    else:
        return await _issue_session(
            session, settings, user, scope=SCOPE_FULL, ip=ip, user_agent=user_agent
        )

    await auth_repo.record_audit(
        session,
        action="auth.login_failed",
        entity="user",
        user_id=audit_user,
        entity_id=audit_user,
        ip=ip,
        after_data={"username": username, "reason": reason},
    )
    await session.commit()
    raise _INVALID_CREDENTIALS


async def login_pin(
    session: AsyncSession,
    settings: Settings,
    *,
    username: str,
    pin: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    """PIN de camarero para terminales: acceso rápido con alcance ``pos`` (§6)."""
    user = await auth_repo.get_user_by_username(session, username)
    if user is None:
        verify_dummy(pin)
        await auth_repo.record_audit(
            session,
            action="auth.pin_failed",
            entity="user",
            ip=ip,
            after_data={"username": username, "reason": "unknown_user"},
        )
        await session.commit()
        raise _INVALID_CREDENTIALS

    audit_user = user.id
    if not user.active or user.pin_hash is None:
        verify_dummy(pin)
        reason = "inactive" if not user.active else "no_pin"
    elif not verify_password(pin, user.pin_hash):
        reason = "bad_pin"
    else:
        return await _issue_session(
            session, settings, user, scope=SCOPE_POS, ip=ip, user_agent=user_agent
        )

    await auth_repo.record_audit(
        session,
        action="auth.pin_failed",
        entity="user",
        user_id=audit_user,
        entity_id=audit_user,
        ip=ip,
        after_data={"username": username, "reason": reason},
    )
    await session.commit()
    raise _INVALID_CREDENTIALS


def _session_error(row) -> AppError:
    """Mapea el estado de una sesión a su error 401 (ya revocada o caducada)."""
    if row.revoked_at is not None:
        return AppError(
            401, ErrorCode.SESSION_REVOKED, "La sesión está cerrada o fue rotada"
        )
    if row.expires_at <= datetime.now(UTC):
        return AppError(401, ErrorCode.TOKEN_EXPIRED, "La sesión ha caducado")
    return AppError(401, ErrorCode.TOKEN_INVALID, "Sesión no válida")


async def refresh(
    session: AsyncSession,
    settings: Settings,
    *,
    refresh_token: str,
    ip: str | None,
    user_agent: str | None,
) -> LoginResult:
    """Rotación: el refresh usado queda revocado y se emite uno nuevo.

    Reutilizar un refresh ya rotado revoca el acceso (la sesión vieja está cerrada)
    y el nuevo token sigue siendo el único válido.
    """
    row = await auth_repo.get_session_by_token_hash(
        session, hash_refresh_token(refresh_token)
    )
    if row is None:
        raise AppError(401, ErrorCode.TOKEN_INVALID, "Token de refresco no reconocido")
    if row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise _session_error(row)

    user = await auth_repo.get_user(session, row.user_id)
    if user is None or not user.active:
        await auth_repo.revoke_session(session, row)
        await session.commit()
        raise _INVALID_CREDENTIALS

    await auth_repo.revoke_session(session, row)  # rotación: el viejo no vale ya
    result = await _issue_session(
        session, settings, user, scope=SCOPE_FULL, ip=ip, user_agent=user_agent
    )
    logger.info("auth_refresh", user=str(row.user_id))
    return result


async def logout(
    session: AsyncSession,
    *,
    refresh_token: str | None,
    ip: str | None,
) -> None:
    """Revoca la sesión del refresh entregado. Idempotente: 200 aunque ya esté."""
    if refresh_token:
        row = await auth_repo.get_session_by_token_hash(
            session, hash_refresh_token(refresh_token)
        )
        if row is not None:
            await auth_repo.revoke_session(session, row)
            await auth_repo.record_audit(
                session,
                action="auth.logout",
                entity="user",
                user_id=row.user_id,
                entity_id=row.user_id,
                ip=ip,
            )
            await session.commit()


async def load_principal(
    session: AsyncSession,
    *,
    claims: dict,
) -> Principal:
    """Reconstruye el Principal desde los claims del JWT ya verificado y la BD.

    La firma y la expiración las comprueba la capa ``api`` (mapeo de errores PyJWT);
    aquí se verifica que la sesión exista y siga viva (revocación inmediata aunque
    el JWT no haya caducado) y que el usuario esté activo. Los permisos se leen
    siempre de BD: revocar un permiso surte efecto sin esperar a que expire.
    """
    row = await auth_repo.get_session(session, UUID(claims["sid"]))
    if row is None:
        raise AppError(401, ErrorCode.TOKEN_INVALID, "Sesión no encontrada")
    if row.revoked_at is not None or row.expires_at <= datetime.now(UTC):
        raise _session_error(row)

    user = await auth_repo.get_user(session, UUID(claims["sub"]))
    if user is None or not user.active:
        raise _INVALID_CREDENTIALS

    permissions = await auth_repo.get_permission_codes(session, user.role_id)
    return _principal_from(user, permissions, claims.get("scope", SCOPE_FULL))

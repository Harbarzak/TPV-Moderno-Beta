"""Acceso a datos de autenticación (fase 03): usuarios, permisos, sesiones, auditoría.

Solo consultas y escrituras: sin política de negocio (eso vive en ``services``).
Toda función recibe la ``AsyncSession`` de la petición.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.security import (
    Permission,
    RolePermission,
    User,
    UserSession,
)
from app.db.models.system import AuditLog


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    stmt = select(User).options(joinedload(User.role)).where(User.username == username)
    return await session.scalar(stmt)


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    stmt = select(User).options(joinedload(User.role)).where(User.id == user_id)
    return await session.scalar(stmt)


async def get_permission_codes(session: AsyncSession, role_id: UUID) -> list[str]:
    """Códigos de permiso del rol, ordenados (RBAC granular, seed de fase 01)."""
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list((await session.scalars(stmt)).all())


async def create_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    token_hash: str,
    expires_at: datetime,
    ip: str | None,
    user_agent: str | None,
) -> UserSession:
    row = UserSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        ip=ip,
        user_agent=user_agent,
    )
    session.add(row)
    await session.flush()
    return row


async def get_session_by_token_hash(
    session: AsyncSession, token_hash: str
) -> UserSession | None:
    return await session.scalar(select(UserSession).where(UserSession.token_hash == token_hash))


async def get_session(session: AsyncSession, session_id: UUID) -> UserSession | None:
    return await session.scalar(select(UserSession).where(UserSession.id == session_id))


async def revoke_session(session: AsyncSession, row: UserSession) -> None:
    """Marca la sesión como cerrada (logout o rotación); idempotente."""
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)


async def touch_last_login(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(last_login_at=func.now())
    )


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    entity: str,
    user_id: UUID | None = None,
    entity_id: UUID | None = None,
    ip: str | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
) -> None:
    """Append-only (audit_log, fase 01): login, fallos de login y logout."""
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            ip=ip,
            before_data=before_data,
            after_data=after_data,
        )
    )

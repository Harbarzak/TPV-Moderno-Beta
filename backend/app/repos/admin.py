"""Acceso a datos del panel de administración (fase Administración).

Consultas y escrituras puras sobre usuarios, roles, permisos, terminales,
dispositivos y ``audit_log``: sin política de negocio (eso vive en
``services``) ni HTTP (``api``). Lo que ya existía de la fase 03 (sesiones,
códigos de permiso, ``record_audit``) sigue en ``repos.auth`` y se reutiliza.
Ninguna función comitea: el commit lo hace el servicio.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.enums import DeviceKind
from app.db.models.security import (
    Device,
    Permission,
    RolePermission,
    SystemRole,
    Terminal,
    User,
)
from app.db.models.system import AuditLog


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
async def list_users(
    session: AsyncSession, *, include_inactive: bool, limit: int, offset: int
) -> list[User]:
    stmt = (
        select(User)
        .options(joinedload(User.role))
        .order_by(User.username)
        .limit(limit)
        .offset(offset)
    )
    if not include_inactive:
        stmt = stmt.where(User.active)
    return list((await session.scalars(stmt)).all())


async def count_users(session: AsyncSession, *, include_inactive: bool) -> int:
    stmt = select(func.count()).select_from(User)
    if not include_inactive:
        stmt = stmt.where(User.active)
    return (await session.scalar(stmt)) or 0


# ---------------------------------------------------------------------------
# Roles y permisos
# ---------------------------------------------------------------------------
async def list_roles(session: AsyncSession) -> list[SystemRole]:
    stmt = select(SystemRole).order_by(SystemRole.code)
    return list((await session.scalars(stmt)).all())


async def get_role(session: AsyncSession, role_id: UUID) -> SystemRole | None:
    return await session.get(SystemRole, role_id)


async def get_role_by_code(session: AsyncSession, code: str) -> SystemRole | None:
    return await session.scalar(select(SystemRole).where(SystemRole.code == code))


async def list_permissions(session: AsyncSession) -> list[Permission]:
    stmt = select(Permission).order_by(Permission.code)
    return list((await session.scalars(stmt)).all())


async def get_permissions_by_codes(
    session: AsyncSession, codes: list[str]
) -> list[Permission]:
    if not codes:
        return []
    stmt = select(Permission).where(Permission.code.in_(codes))
    return list((await session.scalars(stmt)).all())


async def replace_role_permissions(
    session: AsyncSession, role_id: UUID, permission_ids: list[UUID]
) -> None:
    """Reemplazo completo de la matriz del rol (una transacción, un flush)."""
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    session.add_all(
        RolePermission(role_id=role_id, permission_id=pid) for pid in permission_ids
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Terminales
# ---------------------------------------------------------------------------
async def list_terminals(session: AsyncSession, *, include_inactive: bool) -> list[Terminal]:
    stmt = select(Terminal).order_by(Terminal.code)
    if not include_inactive:
        stmt = stmt.where(Terminal.active)
    return list((await session.scalars(stmt)).all())


async def get_terminal(session: AsyncSession, terminal_id: UUID) -> Terminal | None:
    return await session.get(Terminal, terminal_id)


async def get_terminal_by_code(session: AsyncSession, code: str) -> Terminal | None:
    return await session.scalar(select(Terminal).where(Terminal.code == code))


def add_terminal(session: AsyncSession, *, code: str, name: str) -> Terminal:
    row = Terminal(code=code, name=name, active=True)
    session.add(row)
    return row


# ---------------------------------------------------------------------------
# Dispositivos (tpv-agent y periféricos)
# ---------------------------------------------------------------------------
async def list_devices(
    session: AsyncSession, *, include_inactive: bool, kind: DeviceKind | None = None
) -> list[Device]:
    stmt = select(Device).order_by(Device.name)
    if not include_inactive:
        stmt = stmt.where(Device.active)
    if kind is not None:
        stmt = stmt.where(Device.kind == kind)
    return list((await session.scalars(stmt)).all())


async def get_device(session: AsyncSession, device_id: UUID) -> Device | None:
    return await session.get(Device, device_id)


def add_device(
    session: AsyncSession,
    *,
    kind: DeviceKind,
    name: str,
    token_hash: str,
    terminal_id: UUID | None = None,
) -> Device:
    row = Device(kind=kind, name=name, token_hash=token_hash, terminal_id=terminal_id, active=True)
    session.add(row)
    return row


# ---------------------------------------------------------------------------
# Auditoría (append-only: aquí solo SELECT)
# ---------------------------------------------------------------------------
def _audit_filters(
    stmt,
    *,
    action: str | None,
    user_id: UUID | None,
    entity: str | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
):
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))  # prefijo: «admin.», «sales.»…
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if occurred_from is not None:
        stmt = stmt.where(AuditLog.occurred_at >= occurred_from)
    if occurred_to is not None:
        stmt = stmt.where(AuditLog.occurred_at <= occurred_to)
    return stmt


async def search_audit(
    session: AsyncSession,
    *,
    action: str | None = None,
    user_id: UUID | None = None,
    entity: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int,
    offset: int,
) -> list[tuple[AuditLog, str | None]]:
    """Página de auditoría con el nombre del usuario resuelto (outer join)."""
    stmt = select(AuditLog, User.username).outerjoin(User, User.id == AuditLog.user_id)
    stmt = _audit_filters(
        stmt,
        action=action,
        user_id=user_id,
        entity=entity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    stmt = stmt.order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list((await session.execute(stmt)).all())


async def count_audit(
    session: AsyncSession,
    *,
    action: str | None = None,
    user_id: UUID | None = None,
    entity: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
) -> int:
    stmt = select(func.count()).select_from(AuditLog)
    stmt = _audit_filters(
        stmt,
        action=action,
        user_id=user_id,
        entity=entity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return (await session.scalar(stmt)) or 0

"""Usuarios y roles del panel de administración (fase Administración).

Política (ARCHITECTURE.md §6, separación administración/operación):
- Las credenciales (contraseña/PIN) solo se crean o restablecen aquí y NUNCA
  se devuelven; restablecer una credencial revoca las sesiones vivas del
  usuario (que deberá volver a autenticarse).
- El rol ``admin`` tiene siempre todos los permisos (seed CROSS JOIN): su
  matriz NO es editable para que nadie pueda quedarse sin administradores.
- Nadie puede desactivarse a sí mismo desde el panel.
- El nombre de usuario es identidad y no se renombra; el PIN es credencial de
  operación (ámbito ``pos``) y aquí solo se gestiona, nunca se usa.

Cada mutación escribe en ``audit_log`` (``admin.*``) y publica un evento en el
tema ``system`` dentro de la misma transacción. Nunca importa de ``api``.
"""

import re
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_password
from app.db.models.security import SystemRole, User, UserSession
from app.repos import admin as repo
from app.repos import auth as auth_repo
from app.services import events as events_service
from app.services.auth import Principal

MIN_PASSWORD_LEN = 8
PIN_PATTERN = re.compile(r"^\d{4,6}$")
ROLE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def _utcnow():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


async def _get_user_or_404(session: AsyncSession, user_id: UUID) -> User:
    user = await auth_repo.get_user(session, user_id)
    if user is None:
        raise _not_found("Usuario no encontrado")
    return user


async def _revoke_sessions(session: AsyncSession, user_id: UUID) -> None:
    """Una credencial restablecida invalida las sesiones vivas de su dueño."""
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_utcnow())
    )


async def _role_by_code(session: AsyncSession, code: str) -> SystemRole:
    role = await repo.get_role_by_code(session, code)
    if role is None:
        raise _validation("El rol indicado no existe")
    return role


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
async def list_users(
    session: AsyncSession, *, include_inactive: bool, limit: int, offset: int
) -> tuple[list[User], int]:
    items = await repo.list_users(
        session, include_inactive=include_inactive, limit=limit, offset=offset
    )
    total = await repo.count_users(session, include_inactive=include_inactive)
    return items, total


async def create_user(
    session: AsyncSession,
    principal: Principal,
    *,
    username: str,
    password: str,
    full_name: str,
    role_code: str,
    pin: str | None = None,
) -> User:
    role = await _role_by_code(session, role_code)
    if await auth_repo.get_user_by_username(session, username) is not None:
        raise _conflict("Ya existe un usuario con ese nombre de usuario")

    user = User(
        username=username,
        password_hash=hash_password(password),
        pin_hash=hash_password(pin) if pin else None,
        full_name=full_name,
        role_id=role.id,
        active=True,
    )
    user.role = role  # snapshot en memoria: la respuesta la lee sin lazy load
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:  # backstop de la UNIQUE (carrera)
        await session.rollback()
        raise _conflict("Ya existe un usuario con ese nombre de usuario") from exc

    await auth_repo.record_audit(
        session,
        action="admin.user_created",
        entity="user",
        user_id=principal.user_id,
        entity_id=user.id,
        after_data={"username": username, "full_name": full_name, "role": role.code},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.user_created",
        payload={"user_id": str(user.id), "username": username, "role": role.code},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return user


async def update_user(
    session: AsyncSession, principal: Principal, user_id: UUID, *, fields: dict
) -> User:
    """Mutables: ``full_name``, ``role_code`` y ``active``. Ni el nombre de
    usuario ni las credenciales se tocan aquí (tienen endpoints propios)."""
    user = await _get_user_or_404(session, user_id)
    if fields.get("active") is False and user.id == principal.user_id:
        raise _validation("No puedes desactivar tu propio usuario")

    before = {"full_name": user.full_name, "role": user.role.code, "active": user.active}
    if "full_name" in fields:
        user.full_name = fields["full_name"]
    if "active" in fields:
        user.active = fields["active"]
    role_change: str | None = None
    if "role_code" in fields and fields["role_code"] != user.role.code:
        role = await _role_by_code(session, fields["role_code"])
        user.role_id = role.id
        role_change = role.code
        user.role = role  # el snapshot de respuesta usa la relationship

    await auth_repo.record_audit(
        session,
        action="admin.user_updated",
        entity="user",
        user_id=principal.user_id,
        entity_id=user.id,
        before_data=before,
        after_data={
            "full_name": user.full_name,
            "role": role_change or user.role.code,
            "active": user.active,
        },
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.user_updated",
        payload={"user_id": str(user.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return user


async def set_password(
    session: AsyncSession, principal: Principal, user_id: UUID, *, password: str
) -> None:
    user = await _get_user_or_404(session, user_id)
    user.password_hash = hash_password(password)
    await _revoke_sessions(session, user.id)
    await auth_repo.record_audit(
        session,
        action="admin.password_reset",
        entity="user",
        user_id=principal.user_id,
        entity_id=user.id,
        after_data={"username": user.username},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.password_reset",
        payload={"user_id": str(user.id), "username": user.username},
        actor_user_id=principal.user_id,
    )
    await session.commit()


async def set_pin(
    session: AsyncSession, principal: Principal, user_id: UUID, *, pin: str | None
) -> None:
    """Alta/cambio de PIN de operación (``pos``) o retirada con ``None``."""
    user = await _get_user_or_404(session, user_id)
    action = "admin.pin_cleared" if pin is None else "admin.pin_updated"
    user.pin_hash = None if pin is None else hash_password(pin)
    await _revoke_sessions(session, user.id)
    await auth_repo.record_audit(
        session,
        action=action,
        entity="user",
        user_id=principal.user_id,
        entity_id=user.id,
        after_data={"username": user.username},
    )
    await events_service.record(
        session,
        topic="system",
        type=action,
        payload={"user_id": str(user.id), "username": user.username},
        actor_user_id=principal.user_id,
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Roles y matriz de permisos
# ---------------------------------------------------------------------------
async def list_roles(session: AsyncSession) -> list[tuple[SystemRole, list[str]]]:
    roles = await repo.list_roles(session)
    return [(role, await auth_repo.get_permission_codes(session, role.id)) for role in roles]


async def list_permissions(session: AsyncSession) -> list:
    return await repo.list_permissions(session)


async def create_role(
    session: AsyncSession, principal: Principal, *, code: str, name: str
) -> SystemRole:
    if await repo.get_role_by_code(session, code) is not None:
        raise _conflict("Ya existe un rol con ese código")

    role = SystemRole(code=code, name=name, is_system=False)
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:  # backstop de la UNIQUE (carrera)
        await session.rollback()
        raise _conflict("Ya existe un rol con ese código") from exc

    await auth_repo.record_audit(
        session,
        action="admin.role_created",
        entity="role",
        user_id=principal.user_id,
        entity_id=role.id,
        after_data={"code": code, "name": name},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.role_created",
        payload={"role_id": str(role.id), "code": code},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return role


async def update_role(
    session: AsyncSession, principal: Principal, role_id: UUID, *, fields: dict
) -> SystemRole:
    """Solo el nombre visible: el código es identidad de los tokens y seeds."""
    role = await repo.get_role(session, role_id)
    if role is None:
        raise _not_found("Rol no encontrado")
    before = {"name": role.name}
    if "name" in fields:
        role.name = fields["name"]
    await auth_repo.record_audit(
        session,
        action="admin.role_updated",
        entity="role",
        user_id=principal.user_id,
        entity_id=role.id,
        before_data=before,
        after_data={"name": role.name},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.role_updated",
        payload={"role_id": str(role.id), "code": role.code},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return role


async def set_role_permissions(
    session: AsyncSession, principal: Principal, role_id: UUID, *, codes: list[str]
) -> SystemRole:
    """Reemplazo completo de la matriz. ``admin`` es intocable: siempre lo tiene todo."""
    role = await repo.get_role(session, role_id)
    if role is None:
        raise _not_found("Rol no encontrado")
    if role.code == "admin":
        raise _validation("El rol admin tiene siempre todos los permisos")

    wanted = sorted(set(codes))
    found = await repo.get_permissions_by_codes(session, wanted)
    known = {p.code for p in found}
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise _validation(f"Permisos desconocidos: {', '.join(unknown)}")

    await repo.replace_role_permissions(session, role.id, [p.id for p in found])
    await auth_repo.record_audit(
        session,
        action="admin.role_permissions_replaced",
        entity="role",
        user_id=principal.user_id,
        entity_id=role.id,
        before_data={"permissions": await auth_repo.get_permission_codes(session, role.id)},
        after_data={"permissions": wanted},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.role_permissions_replaced",
        payload={"role_id": str(role.id), "code": role.code},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return role

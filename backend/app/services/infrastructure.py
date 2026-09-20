"""Terminales y dispositivos del panel de administración (fase Administración).

- Terminales: registro lógico de puestos TPV (code/name/active); el histórico
  de ventas y cajas las referencia, así que la baja es SIEMPRE lógica.
- Dispositivos: tpv-agent y periféricos (printer/pinpad/cajón/display). El
  alta y la rotación generan un token que se entrega UNA vez en la respuesta
  (en BD solo queda su SHA-256, misma política que el refresh de sesión).

Cada mutación escribe en ``audit_log`` (``admin.*``) y publica un evento en el
tema ``system``. Nunca importa de ``api``.
"""

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.security import hash_device_token, new_device_token
from app.db.enums import DeviceKind
from app.db.models.security import Device, Terminal
from app.repos import admin as repo
from app.repos import auth as auth_repo
from app.services import events as events_service
from app.services.auth import Principal


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


# ---------------------------------------------------------------------------
# Terminales
# ---------------------------------------------------------------------------
async def list_terminals(
    session: AsyncSession, *, include_inactive: bool
) -> list[Terminal]:
    return await repo.list_terminals(session, include_inactive=include_inactive)


async def create_terminal(
    session: AsyncSession, principal: Principal, *, code: str, name: str
) -> Terminal:
    if await repo.get_terminal_by_code(session, code) is not None:
        raise _conflict("Ya existe una terminal con ese código")

    terminal = repo.add_terminal(session, code=code, name=name)
    try:
        await session.flush()
    except IntegrityError as exc:  # backstop de la UNIQUE (carrera)
        await session.rollback()
        raise _conflict("Ya existe una terminal con ese código") from exc

    await auth_repo.record_audit(
        session,
        action="admin.terminal_created",
        entity="terminal",
        user_id=principal.user_id,
        entity_id=terminal.id,
        after_data={"code": code, "name": name},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.terminal_created",
        payload={"terminal_id": str(terminal.id), "code": code},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return terminal


async def update_terminal(
    session: AsyncSession, principal: Principal, terminal_id: UUID, *, fields: dict
) -> Terminal:
    """Mutables: ``name`` y ``active``; el código es identidad de ventas/cajas."""
    terminal = await repo.get_terminal(session, terminal_id)
    if terminal is None:
        raise _not_found("Terminal no encontrada")
    before = {"name": terminal.name, "active": terminal.active}
    if "name" in fields:
        terminal.name = fields["name"]
    if "active" in fields:
        terminal.active = fields["active"]
    await auth_repo.record_audit(
        session,
        action="admin.terminal_updated",
        entity="terminal",
        user_id=principal.user_id,
        entity_id=terminal.id,
        before_data=before,
        after_data={"name": terminal.name, "active": terminal.active},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.terminal_updated",
        payload={"terminal_id": str(terminal.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return terminal


async def deactivate_terminal(
    session: AsyncSession, principal: Principal, terminal_id: UUID
) -> Terminal:
    """Baja lógica: la terminal deja de elegirse, su histórico queda intacto."""
    terminal = await repo.get_terminal(session, terminal_id)
    if terminal is None:
        raise _not_found("Terminal no encontrada")
    terminal.active = False
    await auth_repo.record_audit(
        session,
        action="admin.terminal_deactivated",
        entity="terminal",
        user_id=principal.user_id,
        entity_id=terminal.id,
        after_data={"code": terminal.code},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.terminal_deactivated",
        payload={"terminal_id": str(terminal.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return terminal


# ---------------------------------------------------------------------------
# Dispositivos
# ---------------------------------------------------------------------------
async def _check_terminal(session: AsyncSession, terminal_id: UUID | None) -> UUID | None:
    if terminal_id is None:
        return None
    terminal = await repo.get_terminal(session, terminal_id)
    if terminal is None:
        raise _not_found("Terminal no encontrada")
    if not terminal.active:
        raise _validation("La terminal no está activa")
    return terminal.id


async def list_devices(
    session: AsyncSession, *, include_inactive: bool, kind: DeviceKind | None = None
) -> list[Device]:
    return await repo.list_devices(session, include_inactive=include_inactive, kind=kind)


async def enroll_device(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    kind: DeviceKind,
    terminal_id: UUID | None = None,
) -> tuple[Device, str]:
    """Alta de dispositivo: devuelve el token EN CLARO una única vez."""
    checked_terminal = await _check_terminal(session, terminal_id)
    token = new_device_token()
    device = repo.add_device(
        session, kind=kind, name=name, token_hash=hash_device_token(token),
        terminal_id=checked_terminal,
    )
    try:
        await session.flush()
    except IntegrityError as exc:  # colisión astronómica del token (backstop)
        await session.rollback()
        raise _conflict("No se pudo emitir el token; reintenta el alta") from exc

    await auth_repo.record_audit(
        session,
        action="admin.device_enrolled",
        entity="device",
        user_id=principal.user_id,
        entity_id=device.id,
        after_data={"name": name, "kind": kind.value, "terminal_id": str(checked_terminal)},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.device_enrolled",
        payload={"device_id": str(device.id), "kind": kind.value},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return device, token


async def rotate_device_token(
    session: AsyncSession, principal: Principal, device_id: UUID
) -> tuple[Device, str]:
    """El token anterior deja de valer al instante (solo vive su hash)."""
    device = await repo.get_device(session, device_id)
    if device is None:
        raise _not_found("Dispositivo no encontrado")
    token = new_device_token()
    device.token_hash = hash_device_token(token)
    await auth_repo.record_audit(
        session,
        action="admin.device_token_rotated",
        entity="device",
        user_id=principal.user_id,
        entity_id=device.id,
        after_data={"name": device.name},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.device_token_rotated",
        payload={"device_id": str(device.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return device, token


async def update_device(
    session: AsyncSession, principal: Principal, device_id: UUID, *, fields: dict
) -> Device:
    """Mutables: ``name``, ``terminal_id`` y ``active`` (kind inmutable)."""
    device = await repo.get_device(session, device_id)
    if device is None:
        raise _not_found("Dispositivo no encontrado")
    before = {
        "name": device.name,
        "terminal_id": str(device.terminal_id) if device.terminal_id else None,
        "active": device.active,
    }
    if "name" in fields:
        device.name = fields["name"]
    if "terminal_id" in fields:
        device.terminal_id = await _check_terminal(session, fields["terminal_id"])
    if "active" in fields:
        device.active = fields["active"]
    await auth_repo.record_audit(
        session,
        action="admin.device_updated",
        entity="device",
        user_id=principal.user_id,
        entity_id=device.id,
        before_data=before,
        after_data={
            "name": device.name,
            "terminal_id": str(device.terminal_id) if device.terminal_id else None,
            "active": device.active,
        },
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.device_updated",
        payload={"device_id": str(device.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return device


async def deactivate_device(
    session: AsyncSession, principal: Principal, device_id: UUID
) -> Device:
    device = await repo.get_device(session, device_id)
    if device is None:
        raise _not_found("Dispositivo no encontrado")
    device.active = False
    await auth_repo.record_audit(
        session,
        action="admin.device_deactivated",
        entity="device",
        user_id=principal.user_id,
        entity_id=device.id,
        after_data={"name": device.name},
    )
    await events_service.record(
        session,
        topic="system",
        type="admin.device_deactivated",
        payload={"device_id": str(device.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return device

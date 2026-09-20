"""Administración (fase 22): usuarios, roles, permisos, terminales, dispositivos,
auditoría y backups en ``/api/v1/admin``.

Los módulos que ya tenían endpoint propio siguen donde estaban (separación de
dominios, no de prefijo): catálogo en ``/catalog``, formas de pago en
``/admin/payment-methods``, impresoras en ``/admin/printers``, ajustes de
negocio en ``/admin/business-settings``. Este router añade el resto y cada
grupo exige SU permiso granular:

- usuarios/camareros → ``admin.users`` · roles/permisos → ``admin.roles``
- terminales/dispositivos → ``admin.terminals`` · auditoría → ``admin.audit``
- backups → ``admin.backups``

Reglas transversales: ningún hash (contraseña/PIN/token) sale por la API; el
token de dispositivo se muestra UNA vez (alta y rotación); las terminales y
dispositivos no se borran, se dan de baja (``active=false``); cada mutación
queda en ``audit_log`` con su evento ``system`` asociado.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.repos import auth as auth_repo
from app.db.enums import DeviceKind
from app.db.models.security import Device, SystemRole, Terminal, User
from app.db.models.system import AuditLog
from app.services import accounts, audit as audit_service, backups as backups_service
from app.services import infrastructure as infra_service

router = APIRouter(prefix="/admin", tags=["admin"])

_users = [Depends(require_permission("admin.users"))]
_roles = [Depends(require_permission("admin.roles"))]
_infra = [Depends(require_permission("admin.terminals"))]
_audit = [Depends(require_permission("admin.audit"))]
_backups = [Depends(require_permission("admin.backups"))]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    role_code: str = Field(min_length=1, max_length=64)
    pin: str | None = Field(None, pattern=r"^\d{4,6}$")


class UserUpdate(BaseModel):
    """Solo los campos enviados se actualizan (username/credenciales inmutables)."""

    full_name: str | None = Field(None, min_length=1, max_length=120)
    role_code: str | None = Field(None, min_length=1, max_length=64)
    active: bool | None = None


class PasswordSet(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class PinSet(BaseModel):
    pin: str | None = Field(None, pattern=r"^\d{4,6}$")  # null retira el PIN


class RoleCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    name: str = Field(min_length=1, max_length=80)


class RoleUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RolePermissionsSet(BaseModel):
    permissions: list[str] = Field(max_length=500)


class TerminalCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=80)


class TerminalUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    active: bool | None = None


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: DeviceKind
    terminal_id: UUID | None = None


class DeviceUpdate(BaseModel):
    """Solo los campos enviados se actualizan (kind inmutable)."""

    name: str | None = Field(None, min_length=1, max_length=80)
    terminal_id: UUID | None = None
    active: bool | None = None


# ---------------------------------------------------------------------------
# Respuestas (NUNCA hashes: password_hash/pin_hash/token_hash)
# ---------------------------------------------------------------------------
class UserResponse(BaseModel):
    id: UUID
    username: str
    full_name: str
    role_code: str
    active: bool
    has_pin: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class RoleResponse(BaseModel):
    id: UUID
    code: str
    name: str
    is_system: bool
    permissions: list[str]


class RoleListResponse(BaseModel):
    items: list[RoleResponse]


class PermissionResponse(BaseModel):
    code: str
    description: str


class PermissionListResponse(BaseModel):
    items: list[PermissionResponse]


class TerminalResponse(BaseModel):
    id: UUID
    code: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


class TerminalListResponse(BaseModel):
    items: list[TerminalResponse]


class DeviceResponse(BaseModel):
    id: UUID
    kind: str
    name: str
    terminal_id: UUID | None
    active: bool
    last_seen_at: datetime | None
    created_at: datetime


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]


class DeviceEnrolledResponse(BaseModel):
    """El token viaja en claro SOLO aquí (alta/rotación); en BD queda su SHA-256."""

    device: DeviceResponse
    token: str


class AuditEntryResponse(BaseModel):
    id: int
    occurred_at: datetime
    user_id: UUID | None
    username: str | None
    terminal_id: UUID | None
    action: str
    entity: str
    entity_id: UUID | None
    ip: str | None
    before_data: dict | None
    after_data: dict | None


class AuditPageResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int
    limit: int
    offset: int


class BackupFileInfo(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


class BackupListResponse(BaseModel):
    items: list[BackupFileInfo]


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role_code=user.role.code,
        active=user.active,
        has_pin=user.pin_hash is not None,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _role_response(role: SystemRole, permissions: list[str]) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        code=role.code,
        name=role.name,
        is_system=role.is_system,
        permissions=permissions,
    )


def _terminal_response(terminal: Terminal) -> TerminalResponse:
    return TerminalResponse(
        id=terminal.id,
        code=terminal.code,
        name=terminal.name,
        active=terminal.active,
        created_at=terminal.created_at,
        updated_at=terminal.updated_at,
    )


def _device_response(device: Device) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        kind=device.kind.value,
        name=device.name,
        terminal_id=device.terminal_id,
        active=device.active,
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
    )


def _audit_entry(entry: AuditLog, username: str | None) -> AuditEntryResponse:
    return AuditEntryResponse(
        id=entry.id,
        occurred_at=entry.occurred_at,
        user_id=entry.user_id,
        username=username,
        terminal_id=entry.terminal_id,
        action=entry.action,
        entity=entry.entity,
        entity_id=entry.entity_id,
        ip=entry.ip,
        before_data=entry.before_data,
        after_data=entry.after_data,
    )


# ---------------------------------------------------------------------------
# Usuarios y camareros (admin.users)
# ---------------------------------------------------------------------------
@router.get("/users", dependencies=_users)
async def list_users(
    session: DbSession,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    users, total = await accounts.list_users(
        session, include_inactive=include_inactive, limit=limit, offset=offset
    )
    return UserListResponse(
        items=[_user_response(user) for user in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/users", dependencies=_users, status_code=status.HTTP_201_CREATED)
async def create_user(session: DbSession, principal: CurrentUser, payload: UserCreate) -> UserResponse:
    user = await accounts.create_user(
        session,
        principal,
        username=payload.username,
        password=payload.password,
        full_name=payload.full_name,
        role_code=payload.role_code,
        pin=payload.pin,
    )
    return _user_response(user)


@router.patch("/users/{user_id}", dependencies=_users)
async def update_user(
    session: DbSession, principal: CurrentUser, user_id: UUID, payload: UserUpdate
) -> UserResponse:
    user = await accounts.update_user(
        session, principal, user_id, fields=payload.model_dump(exclude_unset=True)
    )
    return _user_response(user)


@router.put(
    "/users/{user_id}/password",
    dependencies=_users,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_password(
    session: DbSession, principal: CurrentUser, user_id: UUID, payload: PasswordSet
) -> None:
    """Restablece la contraseña y revoca las sesiones vivas del usuario."""
    await accounts.set_password(session, principal, user_id, password=payload.password)


@router.put(
    "/users/{user_id}/pin",
    dependencies=_users,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_pin(
    session: DbSession, principal: CurrentUser, user_id: UUID, payload: PinSet
) -> None:
    """Alta/cambio del PIN de operación (``pos``); ``{"pin": null}`` lo retira."""
    data = payload.model_dump(exclude_unset=True)
    if "pin" not in data:
        from app.core.errors import AppError, ErrorCode

        raise AppError(
            422, ErrorCode.VALIDATION_ERROR, 'Indica "pin" (o null para retirarlo)'
        )
    await accounts.set_pin(session, principal, user_id, pin=data["pin"])


# ---------------------------------------------------------------------------
# Roles y permisos (admin.roles)
# ---------------------------------------------------------------------------
@router.get("/roles", dependencies=_roles)
async def list_roles(session: DbSession) -> RoleListResponse:
    roles = await accounts.list_roles(session)
    return RoleListResponse(
        items=[_role_response(role, permissions) for role, permissions in roles]
    )


@router.post("/roles", dependencies=_roles, status_code=status.HTTP_201_CREATED)
async def create_role(
    session: DbSession, principal: CurrentUser, payload: RoleCreate
) -> RoleResponse:
    role = await accounts.create_role(session, principal, code=payload.code, name=payload.name)
    return _role_response(role, [])


@router.patch("/roles/{role_id}", dependencies=_roles)
async def update_role(
    session: DbSession, principal: CurrentUser, role_id: UUID, payload: RoleUpdate
) -> RoleResponse:
    role = await accounts.update_role(
        session, principal, role_id, fields=payload.model_dump(exclude_unset=True)
    )
    return _role_response(role, await auth_repo.get_permission_codes(session, role.id))


@router.put(
    "/roles/{role_id}/permissions",
    dependencies=_roles,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_role_permissions(
    session: DbSession, principal: CurrentUser, role_id: UUID, payload: RolePermissionsSet
) -> None:
    """Reemplazo completo de la matriz del rol (``admin`` es inmutable)."""
    await accounts.set_role_permissions(session, principal, role_id, codes=payload.permissions)


@router.get("/permissions", dependencies=_roles)
async def list_permissions(session: DbSession) -> PermissionListResponse:
    permissions = await accounts.list_permissions(session)
    return PermissionListResponse(
        items=[
            PermissionResponse(code=p.code, description=p.description) for p in permissions
        ]
    )


# ---------------------------------------------------------------------------
# Terminales y dispositivos (admin.terminals)
# ---------------------------------------------------------------------------
@router.get("/terminals", dependencies=_infra)
async def list_terminals(session: DbSession, include_inactive: bool = False) -> TerminalListResponse:
    terminals = await infra_service.list_terminals(session, include_inactive=include_inactive)
    return TerminalListResponse(items=[_terminal_response(t) for t in terminals])


@router.post("/terminals", dependencies=_infra, status_code=status.HTTP_201_CREATED)
async def create_terminal(
    session: DbSession, principal: CurrentUser, payload: TerminalCreate
) -> TerminalResponse:
    terminal = await infra_service.create_terminal(
        session, principal, code=payload.code, name=payload.name
    )
    return _terminal_response(terminal)


@router.patch("/terminals/{terminal_id}", dependencies=_infra)
async def update_terminal(
    session: DbSession, principal: CurrentUser, terminal_id: UUID, payload: TerminalUpdate
) -> TerminalResponse:
    terminal = await infra_service.update_terminal(
        session, principal, terminal_id, fields=payload.model_dump(exclude_unset=True)
    )
    return _terminal_response(terminal)


@router.delete(
    "/terminals/{terminal_id}",
    dependencies=_infra,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_terminal(
    session: DbSession, principal: CurrentUser, terminal_id: UUID
) -> None:
    """Baja lógica: deja de elegirse en operación; su histórico queda intacto."""
    await infra_service.deactivate_terminal(session, principal, terminal_id)


@router.get("/devices", dependencies=_infra)
async def list_devices(
    session: DbSession,
    include_inactive: bool = False,
    kind: DeviceKind | None = None,
) -> DeviceListResponse:
    devices = await infra_service.list_devices(
        session, include_inactive=include_inactive, kind=kind
    )
    return DeviceListResponse(items=[_device_response(d) for d in devices])


@router.post("/devices", dependencies=_infra, status_code=status.HTTP_201_CREATED)
async def enroll_device(
    session: DbSession, principal: CurrentUser, payload: DeviceCreate
) -> DeviceEnrolledResponse:
    """Alta de dispositivo: el token solo se muestra en esta respuesta."""
    device, token = await infra_service.enroll_device(
        session, principal, name=payload.name, kind=payload.kind, terminal_id=payload.terminal_id
    )
    return DeviceEnrolledResponse(device=_device_response(device), token=token)


@router.patch("/devices/{device_id}", dependencies=_infra)
async def update_device(
    session: DbSession, principal: CurrentUser, device_id: UUID, payload: DeviceUpdate
) -> DeviceResponse:
    device = await infra_service.update_device(
        session, principal, device_id, fields=payload.model_dump(exclude_unset=True)
    )
    return _device_response(device)


@router.delete(
    "/devices/{device_id}",
    dependencies=_infra,
    status_code=status.HTTP_204_NO_CONTENT,
)
async def deactivate_device(
    session: DbSession, principal: CurrentUser, device_id: UUID
) -> None:
    """Baja lógica: el token deja de usarse; el histórico de impresión queda."""
    await infra_service.deactivate_device(session, principal, device_id)


@router.post(
    "/devices/{device_id}/rotate-token",
    dependencies=_infra,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_device_token(
    session: DbSession, principal: CurrentUser, device_id: UUID
) -> DeviceEnrolledResponse:
    """Invalida el token anterior y entrega uno nuevo (una única vez)."""
    device, token = await infra_service.rotate_device_token(session, principal, device_id)
    return DeviceEnrolledResponse(device=_device_response(device), token=token)


# ---------------------------------------------------------------------------
# Auditoría (admin.audit, solo lectura)
# ---------------------------------------------------------------------------
@router.get("/audit", dependencies=_audit)
async def search_audit(
    session: DbSession,
    action: Annotated[str | None, Query(max_length=64)] = None,
    user_id: UUID | None = None,
    entity: Annotated[str | None, Query(max_length=64)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageResponse:
    """Eventos del sistema, el más reciente primero. ``action`` filtra por
    prefijo (``admin.``, ``sales.``…)."""
    entries, total = await audit_service.search(
        session,
        action=action,
        user_id=user_id,
        entity=entity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    return AuditPageResponse(
        items=[_audit_entry(entry, username) for entry, username in entries],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Backups (admin.backups)
# ---------------------------------------------------------------------------
@router.get("/backups", dependencies=_backups)
async def list_backups(request: Request) -> BackupListResponse:
    return BackupListResponse(
        items=[
            BackupFileInfo(**item)
            for item in backups_service.list_backups(request.app.state.settings)
        ]
    )


@router.post(
    "/backups/run",
    dependencies=_backups,
    status_code=status.HTTP_201_CREATED,
)
async def run_backup(
    session: DbSession, principal: CurrentUser, request: Request
) -> BackupFileInfo:
    """Ejecuta ``pg_dump`` (formato custom) contra la base configurada."""
    info = await backups_service.run_backup(
        session, principal, request.app.state.settings
    )
    return BackupFileInfo(**info)

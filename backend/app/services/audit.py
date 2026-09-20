"""Lectura de la auditoría (fase Administración).

``audit_log`` es append-only (REVOKE UPDATE/DELETE al rol de aplicación,
migración 0001): este servicio SOLO consulta. La escritura sigue ocurriendo
dentro de cada operación de negocio vía ``repos.auth.record_audit``.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import AuditLog
from app.repos import admin as repo


async def search(
    session: AsyncSession,
    *,
    action: str | None = None,
    user_id: UUID | None = None,
    entity: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[AuditLog, str | None]], int]:
    """Página de eventos (más recientes primero) + total para el paginador."""
    items = await repo.search_audit(
        session,
        action=action,
        user_id=user_id,
        entity=entity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    total = await repo.count_audit(
        session,
        action=action,
        user_id=user_id,
        entity=entity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    return items, total

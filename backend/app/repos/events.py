"""Repositorio de ``event_log`` (fase 12): append atómico + replay (§8.2).

El ``id`` bigserial de ``event_log`` ES el cursor de replay: el cliente que se
reconecta manda ``since_id`` y recibe, por SUS temas, todo lo publicado después
(ventana limitada; los agregados críticos se re-sincronizan por REST).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system import EventLog


async def append(
    session: AsyncSession,
    *,
    topic: str,
    type: str,
    payload: dict | None,
    actor_user_id: UUID | None,
    event_id: UUID,
    occurred_at: datetime,
) -> int:
    """Inserta el evento y devuelve su ``id`` (cursor) SIN commitear: el
    evento se confirma con la transacción del cambio que lo provoca."""

    row = await session.execute(
        EventLog.__table__.insert()
        .values(
            topic=topic,
            type=type,
            payload=payload,
            actor_user_id=actor_user_id,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        .returning(EventLog.id)
    )
    return row.scalar_one()


async def since(
    session: AsyncSession,
    *,
    topics: set[str],
    after_id: int,
    limit: int,
) -> list[EventLog]:
    """Eventos de ``topics`` posteriores a ``after_id``, en orden de cursor."""

    if not topics:
        return []
    rows = await session.execute(
        select(EventLog)
        .where(EventLog.topic.in_(topics), EventLog.id > after_id)
        .order_by(EventLog.id)
        .limit(limit)
    )
    return list(rows.scalars())

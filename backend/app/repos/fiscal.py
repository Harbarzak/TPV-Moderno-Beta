"""Repositorio del plugin fiscal (fase 34): acceso a datos del plugin.

Patrón de la casa (ARCHITECTURE.md §2): el servicio no escribe SQL; este
módulo concentra las consultas. Las «claims» usan ``FOR UPDATE SKIP LOCKED``
para que dos descargas concurrentes (dos administradores, o un cron y un
administrador) nunca procesen el mismo evento ni el mismo documento.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from uuid import UUID

from app.db.enums import (
    FiscalDocumentStatus,
    FiscalDocumentType,
    FiscalEventKind,
)
from app.db.models.fiscal import FiscalDocument, FiscalEvent
from app.db.models.sales import SaleEvent
from app.domain import fiscal as domain


async def claim_pending_sale_events(
    session: AsyncSession, *, limit: int
) -> list[SaleEvent]:
    """Eventos genéricos de venta aún no consumidos (outbox del motor, fase 06)."""

    stmt = (
        select(SaleEvent)
        .where(SaleEvent.dispatched_at.is_(None))
        .order_by(SaleEvent.created_at, SaleEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def claim_retryable_documents(
    session: AsyncSession, *, limit: int
) -> list[FiscalDocument]:
    """Documentos pendientes de envío (nuevos o reencolados tras rechazo)."""

    stmt = (
        select(FiscalDocument)
        .where(FiscalDocument.status == FiscalDocumentStatus.pending)
        .order_by(FiscalDocument.created_at, FiscalDocument.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def get_document_by_event(
    session: AsyncSession, sale_event_id: UUID
) -> FiscalDocument | None:
    """Idempotencia del consumo: un sale_event se transforma UNA sola vez."""

    stmt = select(FiscalDocument).where(FiscalDocument.sale_event_id == sale_event_id)
    return (await session.scalars(stmt)).first()


def mark_sale_event_dispatched(session: AsyncSession, event: SaleEvent) -> None:
    """Consumido por el plugin: la columna existe desde la fase 06 para esto."""

    event.dispatched_at = func.now()


def create_document(
    session: AsyncSession,
    *,
    sale_event_id: UUID,
    order_id: UUID,
    provider: str,
    snapshot: domain.FiscalPayload,
) -> FiscalDocument:
    """Documento fiscal ``pending`` desde un evento de venta ya validado."""

    document = FiscalDocument(
        sale_event_id=sale_event_id,
        order_id=order_id,
        doc_type=snapshot.doc_type,
        provider=provider,
        status=FiscalDocumentStatus.pending,
        payload=snapshot.data,
    )
    session.add(document)
    return document


def add_fiscal_event(
    session: AsyncSession,
    document: FiscalDocument,
    *,
    kind: FiscalEventKind,
    detail: dict | None = None,
    actor_user_id: UUID | None = None,
) -> FiscalEvent:
    """Traza append-only: toda operación fiscal deja aquí su fila."""

    row = FiscalEvent(
        document_id=document.id,
        kind=kind,
        detail=detail,
        actor_user_id=actor_user_id,
    )
    session.add(row)
    return row


async def list_documents(
    session: AsyncSession,
    *,
    status: FiscalDocumentStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[FiscalDocument], int]:
    """Listado paginado con total (mismo contrato que el resto de la API)."""

    base = select(FiscalDocument)
    if status is not None:
        base = base.where(FiscalDocument.status == status)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    stmt = base.order_by(FiscalDocument.created_at, FiscalDocument.id).limit(limit).offset(offset)
    documents = list((await session.scalars(stmt)).all())
    return documents, int(total or 0)


async def get_document_with_events(
    session: AsyncSession, document_id: UUID
) -> FiscalDocument | None:
    """Documento con su traza completa (detalle auditable)."""

    stmt = (
        select(FiscalDocument)
        .options(selectinload(FiscalDocument.events))
        .where(FiscalDocument.id == document_id)
    )
    return (await session.scalars(stmt)).first()


async def count_pending_sale_events(session: AsyncSession) -> int:
    """Eventos de venta pendientes de consumo (cola del plugin)."""

    return int(
        await session.scalar(
            select(func.count()).select_from(SaleEvent).where(SaleEvent.dispatched_at.is_(None))
        ) or 0
    )


async def count_documents_by_status(session: AsyncSession) -> dict[str, int]:
    """Documentos por estado (todos los estados del enum, con ceros)."""

    rows = await session.execute(
        select(FiscalDocument.status, func.count())
        .group_by(FiscalDocument.status)
    )
    counts = {status.value: 0 for status in FiscalDocumentStatus}
    for status, n in rows:
        counts[status.value] = int(n)
    return counts

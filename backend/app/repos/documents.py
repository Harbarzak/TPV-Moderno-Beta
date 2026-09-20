"""Acceso a datos de documentos (fase 09 · Tickets y facturas).

Solo consultas y escrituras: la política de negocio vive en
``services.documents``. Toda función recibe la ``AsyncSession`` de la
petición. La numeración segura pasa por aquí: ``next_number`` crea la serie
si no existe (``INSERT … ON CONFLICT DO NOTHING``) y reserva el número
siguiente con ``SELECT … FOR UPDATE`` (dos cobros concurrentes en el mismo
terminal serializan sobre la fila de la secuencia).
"""

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import SequenceScope
from app.db.models.catalog import Customer
from app.db.models.sales import (
    DocumentSequence,
    Invoice,
    InvoiceLine,
    Order,
    Refund,
    Ticket,
)
from app.db.models.system import Parameter


# ---------------------------------------------------------------------------
# Parámetros (Configuración/Parámetros: series, datos fiscales, logo…)
# ---------------------------------------------------------------------------
async def get_parameter(session: AsyncSession, key: str) -> Parameter | None:
    return await session.get(Parameter, key)


async def list_parameters(session: AsyncSession) -> list[Parameter]:
    stmt = select(Parameter).order_by(Parameter.key)
    return list((await session.scalars(stmt)).all())


async def upsert_parameter(
    session: AsyncSession,
    *,
    key: str,
    value: object,
    description: str | None = None,
) -> Parameter:
    """Crea o actualiza un parámetro (el valor ya va validado por el servicio)."""

    stmt = (
        pg_insert(Parameter)
        .values(key=key, value=value, description=description)
        .on_conflict_do_update(index_elements=[Parameter.key], set_={"value": value})
    )
    await session.execute(stmt)
    await session.flush()
    return await session.get(Parameter, key)


async def delete_parameter(session: AsyncSession, key: str) -> bool:
    result = await session.execute(delete(Parameter).where(Parameter.key == key))
    await session.flush()
    return bool(result.rowcount)


# ---------------------------------------------------------------------------
# Numeración (document_sequences)
# ---------------------------------------------------------------------------
async def next_number(
    session: AsyncSession,
    *,
    scope: SequenceScope,
    terminal_id: UUID | None,
    year: int | None,
    series: str,
) -> int:
    """Reserva el siguiente número de la serie con bloqueo de fila.

    Tickets: una secuencia por (terminal, serie). Facturas: una por
    (serie, año) — ``terminal_id`` NULL. La fila se crea si aún no existe;
    la carrera entre dos emisores la resuelve el UNIQUE + ON CONFLICT y el
    ``FOR UPDATE`` serializa la reserva.
    """

    stmt = (
        pg_insert(DocumentSequence)
        .values(
            scope=scope.value,
            terminal_id=terminal_id,
            year=year,
            series=series,
        )
        .on_conflict_do_nothing(
            index_elements=["scope", "terminal_id", "year", "series"]
        )
    )
    await session.execute(stmt)

    row = await session.scalar(
        select(DocumentSequence)
        .where(
            DocumentSequence.scope == scope,
            DocumentSequence.terminal_id == terminal_id,
            DocumentSequence.year == year,
            DocumentSequence.series == series,
        )
        .with_for_update()
    )
    assert row is not None  # la insertamos dos líneas arriba
    row.current_value += 1
    await session.flush()
    return row.current_value


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
async def get_ticket(session: AsyncSession, ticket_id: UUID) -> Ticket | None:
    return await session.get(Ticket, ticket_id)


async def get_ticket_by_order(session: AsyncSession, order_id: UUID) -> Ticket | None:
    """Un ticket por venta (UNIQUE en ``tickets.order_id``)."""

    return await session.scalar(select(Ticket).where(Ticket.order_id == order_id))


async def create_ticket(
    session: AsyncSession,
    *,
    order_id: UUID,
    terminal_id: UUID,
    series: str,
    number: int,
    payload: dict,
    printed_at,
) -> Ticket:
    ticket = Ticket(
        order_id=order_id,
        terminal_id=terminal_id,
        series=series,
        number=number,
        payload=payload,
        printed_at=printed_at,
    )
    session.add(ticket)
    await session.flush()
    return ticket


# ---------------------------------------------------------------------------
# Facturas y sus líneas
# ---------------------------------------------------------------------------
async def get_invoice(session: AsyncSession, invoice_id: UUID) -> Invoice | None:
    return await session.get(Invoice, invoice_id)


async def list_invoice_lines(
    session: AsyncSession, invoice_id: UUID
) -> list[InvoiceLine]:
    stmt = select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)
    return list((await session.scalars(stmt)).all())


async def find_invoiced_order_ids(
    session: AsyncSession, order_ids: list[UUID]
) -> set[UUID]:
    """Órdenes del lote que ya están en alguna factura (UNIQUE por orden)."""

    if not order_ids:
        return set()
    stmt = select(InvoiceLine.order_id).where(InvoiceLine.order_id.in_(order_ids))
    return set((await session.scalars(stmt)).all())


async def create_invoice(
    session: AsyncSession,
    *,
    invoice: Invoice,
    order_ids: list[UUID],
) -> Invoice:
    """Persiste la factura y sus líneas (una por venta facturada)."""

    session.add(invoice)
    await session.flush()  # asigna invoice.id
    session.add_all(
        InvoiceLine(invoice_id=invoice.id, order_id=order_id)
        for order_id in order_ids
    )
    await session.flush()
    return invoice


async def list_orders_for_update(
    session: AsyncSession, order_ids: list[UUID]
) -> list[Order]:
    """Bloquea las órdenes a facturar (orden determinista: sin deadlocks)."""

    if not order_ids:
        return []
    stmt = (
        select(Order)
        .where(Order.id.in_(order_ids))
        .order_by(Order.id)
        .with_for_update()
    )
    return list((await session.scalars(stmt)).all())


async def count_invoices_for_customer(
    session: AsyncSession, customer_id: UUID
) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.customer_id == customer_id)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Devoluciones y clientes
# ---------------------------------------------------------------------------
async def get_refund_by_refund_order(
    session: AsyncSession, refund_order_id: UUID
) -> Refund | None:
    """La fila ``refunds`` de una orden negativa (devolución ya registrada)."""

    return await session.scalar(
        select(Refund).where(Refund.refund_order_id == refund_order_id)
    )


async def get_customer(session: AsyncSession, customer_id: UUID) -> Customer | None:
    return await session.get(Customer, customer_id)

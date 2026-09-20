"""Acceso a datos del KDS (fase 32): comandas, líneas y estaciones.

Solo consultas y escrituras: la política de negocio (transiciones, derivación
del estado de cabecera, eventos) vive en ``services.kitchen``. El tablero se
lee como tarjetas ya montadas (:class:`BoardTicket`): la API no expone modelos
ORM.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import KitchenStatus, OrderStatus
from app.db.models.restaurant import KitchenOrder, KitchenOrderLine, KitchenStation
from app.db.models.sales import DiningTable, Order

NON_TERMINAL = (KitchenStatus.pending, KitchenStatus.preparing, KitchenStatus.ready)


# ---------------------------------------------------------------------------
# Tarjetas del tablero (proyección de lectura)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class BoardLine:
    id: UUID
    order_line_id: UUID
    name: str
    quantity: Decimal
    notes: str | None
    status: KitchenStatus
    station_id: UUID | None


@dataclass(slots=True)
class BoardTicket:
    id: UUID
    order_id: UUID
    status: KitchenStatus
    priority: int
    created_at: datetime
    ready_at: datetime | None
    served_at: datetime | None
    order_type: str
    table_name: str | None
    lines: list[BoardLine] = field(default_factory=list)


async def get_kitchen_order(session: AsyncSession, kitchen_order_id: UUID) -> KitchenOrder | None:
    return await session.get(KitchenOrder, kitchen_order_id)


async def get_kitchen_order_by_order(session: AsyncSession, order_id: UUID) -> KitchenOrder | None:
    return await session.scalar(select(KitchenOrder).where(KitchenOrder.order_id == order_id))


async def get_kitchen_line(session: AsyncSession, kitchen_line_id: UUID) -> KitchenOrderLine | None:
    return await session.get(KitchenOrderLine, kitchen_line_id)


async def get_kitchen_line_by_order_line(
    session: AsyncSession, order_line_id: UUID
) -> KitchenOrderLine | None:
    return await session.scalar(
        select(KitchenOrderLine).where(KitchenOrderLine.order_line_id == order_line_id)
    )


async def list_lines_of_ticket(
    session: AsyncSession, kitchen_order_id: UUID, *, include_cancelled: bool = False
) -> list[KitchenOrderLine]:
    stmt = select(KitchenOrderLine).where(KitchenOrderLine.kitchen_order_id == kitchen_order_id)
    if not include_cancelled:
        stmt = stmt.where(KitchenOrderLine.status != KitchenStatus.cancelled)
    stmt = stmt.order_by(KitchenOrderLine.created_at)
    return list((await session.scalars(stmt)).all())


async def board(
    session: AsyncSession, *, served_limit: int = 10
) -> list[BoardTicket]:
    """Tarjetas del tablero: comandas no terminales (abiertas) + las últimas
    ``served_limit`` servidas (para «repasar lo servido»). Las canceladas no se
    listan. Orden: urgentes primero, luego las más antiguas (FIFO de cocina);
    las servidas van al final por ``served_at`` descendente."""

    abiertas = (
        select(KitchenOrder, Order, DiningTable)
        .join(Order, Order.id == KitchenOrder.order_id)
        .outerjoin(DiningTable, DiningTable.id == Order.dining_table_id)
        .where(
            KitchenOrder.status.in_(NON_TERMINAL),
            Order.status != OrderStatus.voided,  # anulado en ventas ⇒ KDS la cancela igualmente
        )
        .order_by(KitchenOrder.priority.desc(), KitchenOrder.created_at)
    )
    servidas = (
        select(KitchenOrder, Order, DiningTable)
        .join(Order, Order.id == KitchenOrder.order_id)
        .outerjoin(DiningTable, DiningTable.id == Order.dining_table_id)
        .where(KitchenOrder.status == KitchenStatus.served)
        .order_by(KitchenOrder.served_at.desc())
        .limit(served_limit)
    )

    tickets: list[BoardTicket] = []
    for ko, order, table in (await session.execute(abiertas)).all():
        tickets.append(_ticket_row(ko, order, table))
    for ko, order, table in (await session.execute(servidas)).all():
        tickets.append(_ticket_row(ko, order, table))

    await _attach_lines(session, tickets)
    return tickets


def _ticket_row(ko: KitchenOrder, order: Order, table: DiningTable | None) -> BoardTicket:
    return BoardTicket(
        id=ko.id,
        order_id=ko.order_id,
        status=ko.status,
        priority=ko.priority,
        created_at=ko.created_at,
        ready_at=ko.ready_at,
        served_at=ko.served_at,
        order_type=str(order.order_type.value) if order.order_type is not None else "sale",
        table_name=table.name if table is not None else None,
    )


async def _attach_lines(
    session: AsyncSession, tickets: list[BoardTicket]
) -> None:
    if not tickets:
        return
    ids = [ticket.id for ticket in tickets]
    stmt = (
        select(KitchenOrderLine)
        .where(
            KitchenOrderLine.kitchen_order_id.in_(ids),
            KitchenOrderLine.status != KitchenStatus.cancelled,
        )
        .order_by(KitchenOrderLine.created_at)
    )
    by_ticket: dict[UUID, list[BoardLine]] = {ticket.id: [] for ticket in tickets}
    for line in (await session.scalars(stmt)).all():
        by_ticket[line.kitchen_order_id].append(
            BoardLine(
                id=line.id,
                order_line_id=line.order_line_id,
                name=line.name,
                quantity=line.quantity,
                notes=line.notes,
                status=line.status,
                station_id=line.station_id,
            )
        )
    for ticket in tickets:
        ticket.lines = by_ticket[ticket.id]


# ---------------------------------------------------------------------------
# Estaciones
# ---------------------------------------------------------------------------
async def list_stations(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[KitchenStation]:
    stmt = select(KitchenStation).order_by(KitchenStation.sort_order, KitchenStation.name)
    if not include_inactive:
        stmt = stmt.where(KitchenStation.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_station(session: AsyncSession, station_id: UUID) -> KitchenStation | None:
    return await session.get(KitchenStation, station_id)

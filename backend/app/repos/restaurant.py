"""Acceso a datos de sala (fase 30 · Modo restaurante): zonas, mesas y el
estado del plano.

Solo consultas y escrituras: la política de negocio vive en
``services.restaurant``. Toda función recibe la ``AsyncSession`` de la
petición. El estado de mesa (libre / abierta / cuenta pedida) se deriva en
lectura desde la comanda abierta (una por mesa, ``uq_orders_open_per_table``)
— sin tablas de estado que sincronizar.
"""

from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import OrderStatus
from app.db.models.sales import DiningTable, Order, OrderLine, Zone
from app.db.models.security import User


# ---------------------------------------------------------------------------
# Zonas
# ---------------------------------------------------------------------------
async def list_zones(session: AsyncSession, *, include_inactive: bool = False) -> list[Zone]:
    stmt = select(Zone).order_by(Zone.sort_order, Zone.name)
    if not include_inactive:
        stmt = stmt.where(Zone.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_zone(session: AsyncSession, zone_id: UUID) -> Zone | None:
    return await session.get(Zone, zone_id)


async def get_zone_for_update(session: AsyncSession, zone_id: UUID) -> Zone | None:
    return await session.scalar(select(Zone).where(Zone.id == zone_id).with_for_update())


async def count_active_tables(session: AsyncSession, zone_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DiningTable)
            .where(DiningTable.zone_id == zone_id, DiningTable.active.is_(True))
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Mesas
# ---------------------------------------------------------------------------
async def list_tables(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[DiningTable]:
    stmt = select(DiningTable).order_by(DiningTable.sort_order, DiningTable.name)
    if not include_inactive:
        stmt = stmt.where(DiningTable.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_table_for_update(session: AsyncSession, table_id: UUID) -> DiningTable | None:
    """Bloquea la fila de la mesa: traspasos y juntadas pasan por aquí."""

    return await session.scalar(
        select(DiningTable).where(DiningTable.id == table_id).with_for_update()
    )


async def count_open_drafts_in_zone(session: AsyncSession, zone_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Order)
            .join(DiningTable, Order.dining_table_id == DiningTable.id)
            .where(
                DiningTable.zone_id == zone_id,
                Order.status == OrderStatus.draft,
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Plano: mesas + su estado derivado en UNA consulta
# ---------------------------------------------------------------------------
async def floor_rows(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[tuple[DiningTable, Zone, Order | None, str | None, int | None]]:
    """Mesas con zona, comanda abierta, camarero y suma abierta de líneas.

    El total abierto no puede venir de ``orders`` (un draft no tiene totales,
    ``ck_orders_draft_no_totals``): se suma ``line_total`` por pedido.
    """

    totals = (
        select(
            OrderLine.order_id.label("order_id"),
            func.sum(OrderLine.line_total).label("open_total"),
        )
        .group_by(OrderLine.order_id)
        .subquery()
    )
    stmt = (
        select(DiningTable, Zone, Order, User.full_name, totals.c.open_total)
        .join(Zone, DiningTable.zone_id == Zone.id)
        .outerjoin(
            Order,
            and_(
                Order.dining_table_id == DiningTable.id,
                Order.status == OrderStatus.draft,
            ),
        )
        .outerjoin(User, Order.user_id == User.id)
        .outerjoin(totals, totals.c.order_id == Order.id)
        .order_by(Zone.sort_order, Zone.name, DiningTable.sort_order, DiningTable.name)
    )
    if not include_inactive:
        stmt = stmt.where(DiningTable.active.is_(True))
    return [tuple(row) for row in (await session.execute(stmt)).all()]

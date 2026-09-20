"""Acceso a datos de ventas (fase 06 · Motor de ventas).

Solo consultas y escrituras: la política de negocio vive en
``services.sales``. Toda función recibe la ``AsyncSession`` de la petición.
Las ventas cobradas o anuladas NUNCA se borran (§3): solo los borradores son
editables, y sus líneas se eliminan físicamente mientras el pedido no tenga
totales (``ck_orders_draft_no_totals``).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import OrderStatus, SaleEventType
from app.db.models.catalog import PaymentMethod, Product, TaxRate
from app.db.models.cash import CashSession
from app.db.models.sales import DiningTable, Order, OrderLine, Payment, Refund, SaleEvent
from app.db.models.security import Terminal
from app.domain.sales import cents_to_decimal, milli_to_qty


# ---------------------------------------------------------------------------
# Órdenes y líneas
# ---------------------------------------------------------------------------
async def get_order(session: AsyncSession, order_id: UUID) -> Order | None:
    return await session.get(Order, order_id)


async def get_order_for_update(session: AsyncSession, order_id: UUID) -> Order | None:
    """Bloquea la fila del pedido: toda transición de estado pasa por aquí."""

    return await session.scalar(
        select(Order).where(Order.id == order_id).with_for_update()
    )


async def list_orders(
    session: AsyncSession,
    *,
    status: OrderStatus | None = None,
    terminal_id: UUID | None = None,
    user_id: UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Order], int]:
    conds = []
    if status is not None:
        conds.append(Order.status == status)
    if terminal_id is not None:
        conds.append(Order.terminal_id == terminal_id)
    if user_id is not None:
        conds.append(Order.user_id == user_id)

    base = select(Order)
    if conds:
        base = base.where(*conds)
    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    stmt = base.order_by(Order.created_at.desc()).limit(limit).offset(offset)
    rows = list((await session.scalars(stmt)).all())
    return rows, int(total or 0)


async def list_lines(session: AsyncSession, order_id: UUID) -> list[OrderLine]:
    stmt = (
        select(OrderLine)
        .where(OrderLine.order_id == order_id)
        .order_by(OrderLine.sort_order, OrderLine.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def get_line(session: AsyncSession, order_id: UUID, line_id: UUID) -> OrderLine | None:
    return await session.scalar(
        select(OrderLine).where(OrderLine.id == line_id, OrderLine.order_id == order_id)
    )


async def max_line_sort_order(session: AsyncSession, order_id: UUID) -> int:
    current = await session.scalar(
        select(func.max(OrderLine.sort_order)).where(OrderLine.order_id == order_id)
    )
    return int(current or 0)


# ---------------------------------------------------------------------------
# Soporte de venta: terminal, caja, producto vendible
# ---------------------------------------------------------------------------
async def get_terminal(session: AsyncSession, terminal_id: UUID) -> Terminal | None:
    return await session.get(Terminal, terminal_id)


async def get_cash_session_for_update(
    session: AsyncSession, cash_session_id: UUID
) -> CashSession | None:
    return await session.scalar(
        select(CashSession).where(CashSession.id == cash_session_id).with_for_update()
    )


async def get_sellable_product(
    session: AsyncSession, product_id: UUID
) -> tuple[Product, TaxRate] | None:
    """Producto activo con su tipo de IVA vigente (``products.tax_rate_id``).

    El precio y el tipo viven desnormalizados en ``products`` (decisión de fase
    04): aquí es exactamente el snapshot que la línea debe congelar.
    """

    stmt = (
        select(Product, TaxRate)
        .join(TaxRate, Product.tax_rate_id == TaxRate.id)
        .where(Product.id == product_id)
    )
    row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


async def find_draft_by_table(session: AsyncSession, dining_table_id: UUID) -> Order | None:
    """Comanda abierta de una mesa (unicidad parcial ``uq_orders_open_per_table``)."""

    return await session.scalar(
        select(Order).where(
            Order.dining_table_id == dining_table_id, Order.status == OrderStatus.draft
        )
    )


async def get_dining_table(session: AsyncSession, table_id: UUID) -> DiningTable | None:
    return await session.get(DiningTable, table_id)


# ---------------------------------------------------------------------------
# Pagos (fase 07)
# ---------------------------------------------------------------------------
async def get_payment_method(session: AsyncSession, method_id: UUID) -> PaymentMethod | None:
    return await session.get(PaymentMethod, method_id)


async def find_payment_method_by_code(
    session: AsyncSession, code: str
) -> PaymentMethod | None:
    return await session.scalar(
        select(PaymentMethod).where(PaymentMethod.code == code)
    )


async def list_payment_methods(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[PaymentMethod]:
    stmt = select(PaymentMethod).order_by(PaymentMethod.sort_order, PaymentMethod.code)
    if not include_inactive:
        stmt = stmt.where(PaymentMethod.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def list_payments_with_method(
    session: AsyncSession, order_id: UUID
) -> list[tuple[Payment, PaymentMethod]]:
    """Pagos de una venta con su forma de pago, en orden de creación."""

    stmt = (
        select(Payment, PaymentMethod)
        .join(PaymentMethod, Payment.payment_method_id == PaymentMethod.id)
        .where(Payment.order_id == order_id)
        .order_by(Payment.created_at, Payment.id)
    )
    rows = (await session.execute(stmt)).all()
    return [(row[0], row[1]) for row in rows]


# ---------------------------------------------------------------------------
# Devoluciones y eventos de venta
# ---------------------------------------------------------------------------
async def get_refund_by_original(
    session: AsyncSession, original_order_id: UUID
) -> Refund | None:
    """Una venta original admite una sola devolución (UNIQUE en ``refunds``)."""

    return await session.scalar(
        select(Refund).where(Refund.original_order_id == original_order_id)
    )


async def record_sale_event(
    session: AsyncSession,
    *,
    order_id: UUID,
    event_type: SaleEventType,
    payload: dict,
) -> SaleEvent:
    """Evento genérico de venta: el futuro FiscalAdapter lo consume (plugin)."""

    event = SaleEvent(order_id=order_id, event_type=event_type, payload=payload)
    session.add(event)
    return event


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "cents_to_decimal",
    "find_draft_by_table",
    "find_payment_method_by_code",
    "get_cash_session_for_update",
    "get_line",
    "get_order",
    "get_order_for_update",
    "get_payment_method",
    "get_refund_by_original",
    "get_sellable_product",
    "get_terminal",
    "list_lines",
    "list_orders",
    "list_payment_methods",
    "list_payments_with_method",
    "max_line_sort_order",
    "milli_to_qty",
    "record_sale_event",
    "utcnow",
]

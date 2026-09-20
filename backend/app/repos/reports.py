"""Acceso a datos de informes (fase 15 · Informes).

Solo consultas: TODAS las métricas se agregan en SQL (``GROUP BY`` + ``SUM``/
``COUNT``) y toda página se sirve con ``LIMIT/OFFSET`` — la API nunca carga
millones de filas para sumarlas en Python (§13). Las ventas del periodo son
las órdenes ``paid`` acotadas por ``paid_at``; las devoluciones son órdenes
pagadas negativas, así que sus sumas NETAN solas (venta neta). Los listados
se sirven del snapshot histórico (payload congelado del documento) extrayendo
en SQL solo el número legible — nunca el JSONB completo por fila.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import InvoiceStatus, OrderStatus
from app.db.models.cash import CashSession
from app.db.models.catalog import Category, PaymentMethod, Product
from app.db.models.sales import Invoice, Order, OrderLine, Payment, Ticket
from app.db.models.security import User

ZERO = Decimal("0")
NOT_CATEGORIZED = "(sin categoría)"

# El dinero de la venta lo firman las órdenes pagadas; las anuladas no venden.
def _paid_window(
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None = None,
) -> list:
    conds = [
        Order.status == OrderStatus.paid,
        Order.paid_at >= paid_from,
        Order.paid_at <= paid_to,
    ]
    if terminal_id is not None:
        conds.append(Order.terminal_id == terminal_id)
    return conds


def _sale_amount() -> case:
    return case((Order.total_amount > 0, Order.total_amount), else_=ZERO)


def _refund_amount() -> case:
    return case((Order.total_amount < 0, Order.total_amount), else_=ZERO)


async def _total(session: AsyncSession, base: Select) -> int:
    """Filas (o grupos) totales de una consulta sin paginar: COUNT sobre subquery."""

    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    return int(total or 0)


# ---------------------------------------------------------------------------
# Tickets emitidos
# ---------------------------------------------------------------------------
async def list_tickets(
    session: AsyncSession,
    *,
    issued_from: datetime,
    issued_to: datetime,
    terminal_id: UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list, int]:
    """Tickets emitidos en el rango (del más nuevo al más viejo) con su total.

    El ``doc_number`` se extrae en SQL del payload congelado; el JSONB completo
    solo lo sirve el detalle del documento (``/documents/tickets/{id}``).
    """

    conds = [Ticket.created_at >= issued_from, Ticket.created_at <= issued_to]
    if terminal_id is not None:
        conds.append(Ticket.terminal_id == terminal_id)

    base = (
        select(
            Ticket.id,
            Ticket.order_id,
            Ticket.terminal_id,
            Ticket.series,
            Ticket.number,
            func.jsonb_extract_path_text(Ticket.payload, "doc_number").label("doc_number"),
            Ticket.printed_at,
            Ticket.reprint_count,
            Ticket.created_at,
            Order.total_amount,
            Order.user_id,
        )
        .select_from(Ticket)
        .join(Order, Ticket.order_id == Order.id)
        .where(*conds)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(base.order_by(Ticket.created_at.desc(), Ticket.id.desc())
                              .limit(limit).offset(offset))
    ).all()
    return rows, total


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------
async def list_invoices(
    session: AsyncSession,
    *,
    issue_from: date,
    issue_to: date,
    status: InvoiceStatus | None = None,
    series: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list, int]:
    """Facturas del rango de emisión, sin el payload (eso es el detalle)."""

    conds = [Invoice.issue_date >= issue_from, Invoice.issue_date <= issue_to]
    if status is not None:
        conds.append(Invoice.status == status)
    if series is not None:
        conds.append(Invoice.series == series)

    base = select(
        Invoice.id,
        Invoice.customer_id,
        Invoice.series,
        Invoice.year,
        Invoice.number,
        Invoice.status,
        Invoice.issue_date,
        Invoice.total_base,
        Invoice.total_tax,
        Invoice.total_amount,
        Invoice.rectified_invoice_id,
        Invoice.voided_at,
        Invoice.void_reason,
        Invoice.created_at,
        func.jsonb_extract_path_text(Invoice.payload, "doc_number").label("doc_number"),
    ).where(*conds)
    total = await _total(session, base)
    rows = (
        await session.execute(
            base.order_by(Invoice.issue_date.desc(), Invoice.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total


# ---------------------------------------------------------------------------
# Cierres pasados (Z)
# ---------------------------------------------------------------------------
async def list_cash_closures(
    session: AsyncSession,
    *,
    closed_from: datetime,
    closed_to: datetime,
    terminal_id: UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list[CashSession], int]:
    """Sesiones de caja CERRADAS en el rango (cuadre Z congelado en la fila)."""

    conds = [
        CashSession.closed_at.is_not(None),
        CashSession.closed_at >= closed_from,
        CashSession.closed_at <= closed_to,
    ]
    if terminal_id is not None:
        conds.append(CashSession.terminal_id == terminal_id)

    base = select(CashSession).where(*conds)
    total = await _total(session, base)
    rows = list(
        (
            await session.scalars(
                base.order_by(CashSession.closed_at.desc()).limit(limit).offset(offset)
            )
        ).all()
    )
    return rows, total


# ---------------------------------------------------------------------------
# Estadísticas del periodo (agregaciones puras de SQL)
# ---------------------------------------------------------------------------
async def sales_summary(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime, terminal_id: UUID | None
) -> tuple[int, Decimal, int, Decimal, int, list[tuple[Decimal, Decimal, Decimal]]]:
    """Totales del periodo y desglose de IVA.

    Devuelve ``(nº ventas, ventas, nº devoluciones, devoluciones (negativo),
    nº anuladas, [(tipo, base, total con IVA)])``. Una sola consulta para los
    totales (``CASE`` por signo) y otra para el IVA por líneas.
    """

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    totals = (
        await session.execute(
            select(
                func.count(case((Order.total_amount > 0, Order.id))),
                func.coalesce(func.sum(_sale_amount()), ZERO),
                func.count(case((Order.total_amount < 0, Order.id))),
                func.coalesce(func.sum(_refund_amount()), ZERO),
            )
            .select_from(Order)
            .where(*conds)
        )
    ).one()

    void_conds = [
        Order.status == OrderStatus.voided,
        Order.voided_at >= paid_from,
        Order.voided_at <= paid_to,
    ]
    if terminal_id is not None:
        void_conds.append(Order.terminal_id == terminal_id)
    voided = (
        await session.scalar(select(func.count()).select_from(Order).where(*void_conds))
    )

    tax_rows = (
        await session.execute(
            select(
                OrderLine.tax_rate,
                func.coalesce(func.sum(OrderLine.line_base), ZERO),
                func.coalesce(func.sum(OrderLine.line_total), ZERO),
            )
            .select_from(OrderLine)
            .join(Order, OrderLine.order_id == Order.id)
            .where(*conds)
            .group_by(OrderLine.tax_rate)
            .order_by(OrderLine.tax_rate)
        )
    ).all()
    return (
        int(totals[0]),
        Decimal(totals[1]),
        int(totals[2]),
        Decimal(totals[3]),
        int(voided or 0),
        [(Decimal(rate), Decimal(base), Decimal(total)) for rate, base, total in tax_rows],
    )


async def sales_by_product(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime,
    terminal_id: UUID | None, limit: int, offset: int,
) -> tuple[list, int]:
    """Ventas netas por producto (el nombre es el snapshot de la línea; los
    artículos sin producto se agrupan por su nombre libre)."""

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    base = (
        select(
            OrderLine.product_id,
            OrderLine.name,
            func.count(Order.id.distinct()).label("orders"),
            func.sum(OrderLine.quantity).label("quantity"),
            func.coalesce(func.sum(OrderLine.line_base), ZERO).label("base"),
            func.coalesce(func.sum(OrderLine.line_total), ZERO).label("total"),
        )
        .select_from(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .where(*conds)
        .group_by(OrderLine.product_id, OrderLine.name)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(
            base.order_by(func.sum(OrderLine.line_total).desc(), OrderLine.name)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total


async def sales_by_category(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime,
    terminal_id: UUID | None, limit: int, offset: int,
) -> tuple[list, int]:
    """Ventas netas por categoría ACTUAL del catálogo (la línea no congela
    categoría: la clasificación estadística es la vigente). Los artículos
    libres y los productos sin categoría caen en ``(sin categoría)``."""

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    base = (
        select(
            Category.id,
            func.coalesce(Category.name, NOT_CATEGORIZED).label("name"),
            func.count(Order.id.distinct()).label("orders"),
            func.sum(OrderLine.quantity).label("quantity"),
            func.coalesce(func.sum(OrderLine.line_base), ZERO).label("base"),
            func.coalesce(func.sum(OrderLine.line_total), ZERO).label("total"),
        )
        .select_from(OrderLine)
        .join(Order, OrderLine.order_id == Order.id)
        .join(Product, OrderLine.product_id == Product.id, isouter=True)
        .join(Category, Product.category_id == Category.id, isouter=True)
        .where(*conds)
        .group_by(Category.id, Category.name)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(
            base.order_by(func.sum(OrderLine.line_total).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total


async def sales_by_waiter(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime,
    terminal_id: UUID | None, limit: int, offset: int,
) -> tuple[list, int]:
    """Ventas netas por camarero (usuario que cobró la orden)."""

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    base = (
        select(
            User.id,
            User.username,
            User.full_name,
            func.count(case((Order.total_amount > 0, Order.id))).label("sales_count"),
            func.count(case((Order.total_amount < 0, Order.id))).label("refunds_count"),
            func.coalesce(func.sum(Order.total_amount), ZERO).label("total"),
        )
        .select_from(Order)
        .join(User, Order.user_id == User.id)
        .where(*conds)
        .group_by(User.id, User.username, User.full_name)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(
            base.order_by(func.sum(Order.total_amount).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total


async def sales_by_payment_method(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime,
    terminal_id: UUID | None, limit: int, offset: int,
) -> tuple[list, int]:
    """Cobros del periodo por forma de pago: ventas y devoluciones por separado."""

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    sale_amount = case((Order.total_amount > 0, Payment.amount), else_=ZERO)
    refund_amount = case((Order.total_amount < 0, Payment.amount), else_=ZERO)
    base = (
        select(
            PaymentMethod.code,
            PaymentMethod.kind,
            func.count(case((Order.total_amount > 0, Payment.id))).label("sales_count"),
            func.coalesce(func.sum(sale_amount), ZERO).label("sales_amount"),
            func.count(case((Order.total_amount < 0, Payment.id))).label("refunds_count"),
            func.coalesce(func.sum(refund_amount), ZERO).label("refunds_amount"),
        )
        .select_from(Payment)
        .join(Order, Payment.order_id == Order.id)
        .join(PaymentMethod, Payment.payment_method_id == PaymentMethod.id)
        .where(*conds)
        .group_by(PaymentMethod.code, PaymentMethod.kind)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(
            base.order_by(func.sum(sale_amount).desc(), PaymentMethod.code)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return rows, total


async def sales_by_period(
    session: AsyncSession, *, paid_from: datetime, paid_to: datetime,
    terminal_id: UUID | None, interval: str, limit: int, offset: int,
) -> tuple[list, int]:
    """Ventas netas agrupadas por tramo temporal (``date_trunc`` en el servidor)."""

    conds = _paid_window(paid_from=paid_from, paid_to=paid_to, terminal_id=terminal_id)
    bucket = func.date_trunc(interval, Order.paid_at).label("bucket")
    base = (
        select(
            bucket,
            func.count(case((Order.total_amount > 0, Order.id))).label("sales_count"),
            func.coalesce(func.sum(_sale_amount()), ZERO).label("sales_amount"),
            func.count(case((Order.total_amount < 0, Order.id))).label("refunds_count"),
            func.coalesce(func.sum(_refund_amount()), ZERO).label("refunds_amount"),
        )
        .select_from(Order)
        .where(*conds)
        .group_by(bucket)
    )
    total = await _total(session, base)
    rows = (
        await session.execute(base.order_by(bucket.desc()).limit(limit).offset(offset))
    ).all()
    return rows, total


__all__ = [
    "list_cash_closures",
    "list_invoices",
    "list_tickets",
    "sales_by_category",
    "sales_by_payment_method",
    "sales_by_period",
    "sales_by_product",
    "sales_by_waiter",
    "sales_summary",
]

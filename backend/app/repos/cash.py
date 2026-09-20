"""Acceso a datos de caja (fase 08 · Caja).

Solo consultas y escrituras: la política de negocio vive en ``services.cash``.
Toda función recibe la ``AsyncSession`` de la petición. El efectivo esperado
NUNCA se calcula aquí: se agregan las piezas (movimientos manuales y pagos en
efectivo de las ventas de la sesión) y la aritmética vive en
:mod:`app.domain.cash`.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import CashMoveKind, PaymentKind
from app.db.models.cash import CashCount, CashCountLine, CashMovement, CashSession
from app.db.models.catalog import PaymentMethod
from app.db.models.sales import Order, Payment


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------
async def get_cash_session(session: AsyncSession, cash_session_id: UUID) -> CashSession | None:
    return await session.get(CashSession, cash_session_id)


async def get_cash_session_for_update(
    session: AsyncSession, cash_session_id: UUID
) -> CashSession | None:
    """Bloquea la fila: movimientos, arqueos y cierre pasan por aquí."""

    return await session.scalar(
        select(CashSession).where(CashSession.id == cash_session_id).with_for_update()
    )


async def find_open_by_terminal(
    session: AsyncSession, terminal_id: UUID
) -> CashSession | None:
    """La sesión abierta del terminal (única: ``uq_cash_sessions_open_per_terminal``)."""

    return await session.scalar(
        select(CashSession).where(
            CashSession.terminal_id == terminal_id, CashSession.closed_at.is_(None)
        )
    )


async def list_sessions(
    session: AsyncSession,
    *,
    terminal_id: UUID | None = None,
    open_only: bool = False,
    closed_only: bool = False,
    opened_from: datetime | None = None,
    opened_to: datetime | None = None,
    limit: int,
    offset: int,
) -> tuple[list[CashSession], int]:
    """Histórico de sesiones (informe Z / cuadre) con filtros opcionales."""

    conds = []
    if terminal_id is not None:
        conds.append(CashSession.terminal_id == terminal_id)
    if open_only:
        conds.append(CashSession.closed_at.is_(None))
    if closed_only:
        conds.append(CashSession.closed_at.is_not(None))
    if opened_from is not None:
        conds.append(CashSession.opened_at >= opened_from)
    if opened_to is not None:
        conds.append(CashSession.opened_at <= opened_to)

    base = select(CashSession)
    if conds:
        base = base.where(*conds)
    total = await session.scalar(
        select(func.count()).select_from(base.order_by(None).subquery())
    )
    stmt = base.order_by(CashSession.opened_at.desc()).limit(limit).offset(offset)
    rows = list((await session.scalars(stmt)).all())
    return rows, int(total or 0)


# ---------------------------------------------------------------------------
# Movimientos y arqueos
# ---------------------------------------------------------------------------
async def list_movements(
    session: AsyncSession, cash_session_id: UUID
) -> list[CashMovement]:
    stmt = (
        select(CashMovement)
        .where(CashMovement.cash_session_id == cash_session_id)
        .order_by(CashMovement.created_at, CashMovement.id)
    )
    return list((await session.scalars(stmt)).all())


async def list_counts(session: AsyncSession, cash_session_id: UUID) -> list[CashCount]:
    """Arqueos de la sesión (parciales y el del cierre), del más antiguo al último."""

    stmt = (
        select(CashCount)
        .where(CashCount.cash_session_id == cash_session_id)
        .order_by(CashCount.created_at, CashCount.id)
    )
    return list((await session.scalars(stmt)).all())


async def list_count_lines(
    session: AsyncSession, cash_count_id: UUID
) -> list[CashCountLine]:
    stmt = (
        select(CashCountLine)
        .where(CashCountLine.cash_count_id == cash_count_id)
        .order_by(CashCountLine.denomination.desc())
    )
    return list((await session.scalars(stmt)).all())


# ---------------------------------------------------------------------------
# Agregados para el esperado y los informes (X/Z)
# ---------------------------------------------------------------------------
async def movement_sums(
    session: AsyncSession, cash_session_id: UUID
) -> dict[CashMoveKind, Decimal]:
    """Importe acumulado por tipo de movimiento manual (in/out)."""

    rows = (
        await session.execute(
            select(CashMovement.kind, func.sum(CashMovement.amount))
            .where(CashMovement.cash_session_id == cash_session_id)
            .group_by(CashMovement.kind)
        )
    ).all()
    return {kind: Decimal(amount) for kind, amount in rows}


async def cash_flow_from_payments(
    session: AsyncSession, cash_session_id: UUID
) -> tuple[Decimal, Decimal]:
    """(ventas en efectivo, devoluciones en efectivo) de la sesión (§5.2).

    Los pagos en efectivo mueven la caja; los demás métodos solo acumulan
    totales para el informe. Una devolución es una orden negativa: el signo de
    ``orders.total_amount`` decide si el pago entra o sale de caja.
    """

    is_sale = case((Order.total_amount > 0, Payment.amount), else_=Decimal("0"))
    is_refund = case((Order.total_amount < 0, Payment.amount), else_=Decimal("0"))
    row = (
        await session.execute(
            select(func.coalesce(func.sum(is_sale), 0), func.coalesce(func.sum(is_refund), 0))
            .select_from(Payment)
            .join(Order, Payment.order_id == Order.id)
            .join(PaymentMethod, Payment.payment_method_id == PaymentMethod.id)
            .where(
                Order.cash_session_id == cash_session_id,
                PaymentMethod.kind == PaymentKind.cash,
            )
        )
    ).one()
    return Decimal(row[0]), Decimal(row[1])


async def method_totals(
    session: AsyncSession, cash_session_id: UUID
) -> list[tuple[str, PaymentKind, Decimal, int, Decimal, int]]:
    """Totales por forma de pago para el informe de la sesión.

    Devuelve ``(code, kind, ventas, nº ventas, devoluciones, nº devoluciones)``.
    """

    sale_amount = case((Order.total_amount > 0, Payment.amount), else_=Decimal("0"))
    sale_count = case((Order.total_amount > 0, Payment.id))
    refund_amount = case((Order.total_amount < 0, Payment.amount), else_=Decimal("0"))
    refund_count = case((Order.total_amount < 0, Payment.id))
    rows = (
        await session.execute(
            select(
                PaymentMethod.code,
                PaymentMethod.kind,
                func.coalesce(func.sum(sale_amount), 0),
                func.count(sale_count),
                func.coalesce(func.sum(refund_amount), 0),
                func.count(refund_count),
            )
            .select_from(Payment)
            .join(Order, Payment.order_id == Order.id)
            .join(PaymentMethod, Payment.payment_method_id == PaymentMethod.id)
            .where(Order.cash_session_id == cash_session_id)
            .group_by(PaymentMethod.code, PaymentMethod.kind)
            .order_by(PaymentMethod.code)
        )
    ).all()
    return [
        (code, kind, Decimal(sales), int(n_sales), Decimal(refunds), int(n_refunds))
        for code, kind, sales, n_sales, refunds, n_refunds in rows
    ]


__all__ = [
    "cash_flow_from_payments",
    "find_open_by_terminal",
    "get_cash_session",
    "get_cash_session_for_update",
    "list_count_lines",
    "list_counts",
    "list_movements",
    "list_sessions",
    "method_totals",
    "movement_sums",
]

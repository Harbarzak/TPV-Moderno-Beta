"""Casos de uso de los informes (fase 15 · Informes).

Capa solo de lectura: valida el rango temporal (obligatorio — §13, no cargar
millones de filas), orquesta las agregaciones de :mod:`app.repos.reports` y
devuelve filas tipadas. Los derivados (venta neta, ticket medio) se calculan
en céntimos enteros con :mod:`app.domain.reports`; el dinero viaja como
``Decimal`` y la capa API lo serializa a string (§3). Sin commits: un informe
nunca escribe.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.models.cash import CashSession
from app.db.models.sales import Invoice, InvoiceLine
from app.db.enums import InvoiceStatus, PaymentKind
from app.domain.reports import average_ticket_cents, bounded_range, net_cents
from app.domain.sales import cents_to_decimal, money_to_cents
from app.repos import reports as repo
from app.services import documents as documents_service

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _validated(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    try:
        return bounded_range(since, until)
    except ValueError as exc:
        raise AppError(
            422, ErrorCode.VALIDATION_ERROR, str(exc)
        ) from exc


# ---------------------------------------------------------------------------
# Filas tipadas que expone el servicio
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TicketRow:
    id: UUID
    order_id: UUID
    terminal_id: UUID
    series: str
    number: int
    doc_number: str | None
    printed_at: datetime | None
    reprint_count: int
    created_at: datetime
    total_amount: Decimal
    user_id: UUID | None


@dataclass(frozen=True, slots=True)
class InvoiceRow:
    id: UUID
    customer_id: UUID
    series: str
    year: int
    number: int
    status: InvoiceStatus
    issue_date: date
    total_base: Decimal
    total_tax: Decimal
    total_amount: Decimal
    rectified_invoice_id: UUID | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    doc_number: str | None


@dataclass(frozen=True, slots=True)
class TaxBreakdownRow:
    tax_rate: Decimal
    base: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class SalesSummary:
    sales_count: int
    sales_amount: Decimal
    refunds_count: int
    refunds_amount: Decimal  # negativo
    voided_count: int
    net_amount: Decimal
    average_ticket: Decimal
    tax_breakdown: list[TaxBreakdownRow]


@dataclass(frozen=True, slots=True)
class ProductRow:
    product_id: UUID | None
    name: str
    orders: int
    quantity: Decimal | None
    base: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class CategoryRow:
    category_id: UUID | None
    name: str
    orders: int
    quantity: Decimal | None
    base: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class WaiterRow:
    user_id: UUID
    username: str
    full_name: str | None
    sales_count: int
    refunds_count: int
    total: Decimal


@dataclass(frozen=True, slots=True)
class PaymentMethodRow:
    code: str
    kind: PaymentKind
    sales_count: int
    sales_amount: Decimal
    refunds_count: int
    refunds_amount: Decimal


@dataclass(frozen=True, slots=True)
class PeriodRow:
    bucket: datetime
    sales_count: int
    sales_amount: Decimal
    refunds_count: int
    refunds_amount: Decimal


# ---------------------------------------------------------------------------
# Listados
# ---------------------------------------------------------------------------
async def tickets_page(
    session: AsyncSession,
    *,
    issued_from: datetime,
    issued_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[TicketRow], int]:
    _validated(issued_from, issued_to)
    rows, total = await repo.list_tickets(
        session,
        issued_from=issued_from,
        issued_to=issued_to,
        terminal_id=terminal_id,
        limit=limit,
        offset=offset,
    )
    return [
        TicketRow(
            id=row.id,
            order_id=row.order_id,
            terminal_id=row.terminal_id,
            series=row.series,
            number=row.number,
            doc_number=row.doc_number,
            printed_at=row.printed_at,
            reprint_count=row.reprint_count,
            created_at=row.created_at,
            total_amount=Decimal(row.total_amount),
            user_id=row.user_id,
        )
        for row in rows
    ], total


async def invoices_page(
    session: AsyncSession,
    *,
    issue_from: date,
    issue_to: date,
    status: InvoiceStatus | None,
    series: str | None,
    limit: int,
    offset: int,
) -> tuple[list[InvoiceRow], int]:
    _validated(
        datetime.combine(issue_from, time.min), datetime.combine(issue_to, time.min)
    )
    rows, total = await repo.list_invoices(
        session,
        issue_from=issue_from,
        issue_to=issue_to,
        status=status,
        series=series,
        limit=limit,
        offset=offset,
    )
    return [
        InvoiceRow(
            id=row.id,
            customer_id=row.customer_id,
            series=row.series,
            year=row.year,
            number=row.number,
            status=row.status,
            issue_date=row.issue_date,
            total_base=Decimal(row.total_base),
            total_tax=Decimal(row.total_tax),
            total_amount=Decimal(row.total_amount),
            rectified_invoice_id=row.rectified_invoice_id,
            voided_at=row.voided_at,
            void_reason=row.void_reason,
            created_at=row.created_at,
            doc_number=row.doc_number,
        )
        for row in rows
    ], total


async def invoice_detail(
    session: AsyncSession, invoice_id: UUID
) -> tuple[Invoice, list[InvoiceLine]]:
    """Detalle completo de una factura (404 si no existe) — mismo servicio que
    usa la API de documentos: un solo criterio de «factura detallada»."""

    return await documents_service.get_invoice_detail(session, invoice_id)


async def closures_page(
    session: AsyncSession,
    *,
    closed_from: datetime,
    closed_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[CashSession], int]:
    _validated(closed_from, closed_to)
    return await repo.list_cash_closures(
        session,
        closed_from=closed_from,
        closed_to=closed_to,
        terminal_id=terminal_id,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Estadísticas
# ---------------------------------------------------------------------------
async def summary(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
) -> SalesSummary:
    since, until = _validated(paid_from, paid_to)
    (
        sales_count,
        sales_amount,
        refunds_count,
        refunds_amount,
        voided_count,
        tax_rows,
    ) = await repo.sales_summary(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id
    )
    # La neta sale del dominio en céntimos: se vuelve a euros como el resto.
    net = cents_to_decimal(
        net_cents(money_to_cents(sales_amount), abs(money_to_cents(refunds_amount)))
    )
    return SalesSummary(
        sales_count=sales_count,
        sales_amount=sales_amount,
        refunds_count=refunds_count,
        refunds_amount=refunds_amount,
        voided_count=voided_count,
        net_amount=net,
        average_ticket=cents_to_decimal(
            average_ticket_cents(money_to_cents(sales_amount), sales_count)
        ),
        tax_breakdown=[
            TaxBreakdownRow(tax_rate=rate, base=base, total=total)
            for rate, base, total in tax_rows
        ],
    )


async def by_product(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[ProductRow], int]:
    since, until = _validated(paid_from, paid_to)
    rows, total = await repo.sales_by_product(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return [
        ProductRow(
            product_id=row.product_id,
            name=row.name,
            orders=int(row.orders),
            quantity=Decimal(row.quantity) if row.quantity is not None else None,
            base=Decimal(row.base),
            total=Decimal(row.total),
        )
        for row in rows
    ], total


async def by_category(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[CategoryRow], int]:
    since, until = _validated(paid_from, paid_to)
    rows, total = await repo.sales_by_category(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return [
        CategoryRow(
            category_id=row[0],
            name=row.name,
            orders=int(row.orders),
            quantity=Decimal(row.quantity) if row.quantity is not None else None,
            base=Decimal(row.base),
            total=Decimal(row.total),
        )
        for row in rows
    ], total


async def by_waiter(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[WaiterRow], int]:
    since, until = _validated(paid_from, paid_to)
    rows, total = await repo.sales_by_waiter(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return [
        WaiterRow(
            user_id=row.id,
            username=row.username,
            full_name=row.full_name,
            sales_count=int(row.sales_count),
            refunds_count=int(row.refunds_count),
            total=Decimal(row.total),
        )
        for row in rows
    ], total


async def by_payment_method(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
    limit: int,
    offset: int,
) -> tuple[list[PaymentMethodRow], int]:
    since, until = _validated(paid_from, paid_to)
    rows, total = await repo.sales_by_payment_method(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return [
        PaymentMethodRow(
            code=row.code,
            kind=row.kind,
            sales_count=int(row.sales_count),
            sales_amount=Decimal(row.sales_amount),
            refunds_count=int(row.refunds_count),
            refunds_amount=Decimal(row.refunds_amount),
        )
        for row in rows
    ], total


async def by_period(
    session: AsyncSession,
    *,
    paid_from: datetime,
    paid_to: datetime,
    terminal_id: UUID | None,
    interval: str,
    limit: int,
    offset: int,
) -> tuple[list[PeriodRow], int]:
    since, until = _validated(paid_from, paid_to)
    rows, total = await repo.sales_by_period(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        interval=interval, limit=limit, offset=offset,
    )
    return [
        PeriodRow(
            bucket=row.bucket,
            sales_count=int(row.sales_count),
            sales_amount=Decimal(row.sales_amount),
            refunds_count=int(row.refunds_count),
            refunds_amount=Decimal(row.refunds_amount),
        )
        for row in rows
    ], total

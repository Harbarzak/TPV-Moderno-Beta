"""Informes (fase 15) — ``/api/v1/reports``, todo bajo ``reports.view``.

Capa de consulta: tickets emitidos, facturas (listado + detalle), cierres Z
pasados y estadísticas del periodo. Reglas de la casa:

- ``from``/``to`` OBLIGATORIOS en todas las consultas y acotados a 366 días
  (§13: los informes nunca escanean la tabla entera ni cargan millones de
  filas en el cliente).
- Agregaciones en SQL y páginas de hasta 200 filas (``limit``/``offset``);
  ``total`` cuenta filas o grupos, no órdenes crudas.
- Dinero como string con 2 decimales (§3); métricas derivadas (venta neta,
  ticket medio) calculadas en el backend.
- Sin auditoría ni eventos: un informe no muta estado (como los informes X/Z
  de caja).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.api.v1.cash import CashSessionResponse, _session_response
from app.api.v1.documents import _invoice_response
from app.db.enums import InvoiceStatus
from app.services import reports as service

router = APIRouter(prefix="/reports", tags=["reports"])
_reports = [Depends(require_permission("reports.view"))]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Tramos temporales admitidos por el informe por periodo (date_trunc en SQL).
Interval = Literal["hour", "day", "week", "month"]


def _money(value: Decimal) -> str:
    return format(value, "f")


# ---------------------------------------------------------------------------
# Paginación común de informes
# ---------------------------------------------------------------------------
def _limit_offset(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> tuple[int, int]:
    return limit, offset


LimitOffset = Annotated[tuple[int, int], Depends(_limit_offset)]


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class TicketReportRow(BaseModel):
    id: UUID
    order_id: UUID
    terminal_id: UUID
    series: str
    number: int
    doc_number: str | None
    printed_at: datetime | None
    reprint_count: int
    created_at: datetime
    total_amount: str
    user_id: UUID | None


class TicketListResponse(BaseModel):
    items: list[TicketReportRow]
    total: int
    limit: int
    offset: int


class InvoiceReportRow(BaseModel):
    id: UUID
    customer_id: UUID
    series: str
    year: int
    number: int
    status: str
    issue_date: date
    total_base: str
    total_tax: str
    total_amount: str
    rectified_invoice_id: UUID | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    doc_number: str | None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceReportRow]
    total: int
    limit: int
    offset: int


class ClosureListResponse(BaseModel):
    """Cierres Z pasados: la fila de sesión con el cuadre congelado."""

    items: list[CashSessionResponse]
    total: int
    limit: int
    offset: int


class TaxLineResponse(BaseModel):
    tax_rate: str
    base: str
    total: str


class SummaryResponse(BaseModel):
    sales_count: int
    sales_amount: str
    refunds_count: int
    refunds_amount: str  # negativo
    voided_count: int
    net_amount: str
    average_ticket: str
    tax_breakdown: list[TaxLineResponse]


class ProductRowResponse(BaseModel):
    product_id: UUID | None
    name: str
    orders: int
    quantity: str | None
    base: str
    total: str


class ProductListResponse(BaseModel):
    items: list[ProductRowResponse]
    total: int
    limit: int
    offset: int


class CategoryRowResponse(BaseModel):
    category_id: UUID | None
    name: str
    orders: int
    quantity: str | None
    base: str
    total: str


class CategoryListResponse(BaseModel):
    items: list[CategoryRowResponse]
    total: int
    limit: int
    offset: int


class WaiterRowResponse(BaseModel):
    user_id: UUID
    username: str
    full_name: str | None
    sales_count: int
    refunds_count: int
    total: str


class WaiterListResponse(BaseModel):
    items: list[WaiterRowResponse]
    total: int
    limit: int
    offset: int


class PaymentMethodRowResponse(BaseModel):
    code: str
    kind: str
    sales_count: int
    sales_amount: str
    refunds_count: int
    refunds_amount: str


class PaymentMethodListResponse(BaseModel):
    items: list[PaymentMethodRowResponse]
    total: int
    limit: int
    offset: int


class PeriodRowResponse(BaseModel):
    bucket: datetime
    sales_count: int
    sales_amount: str
    refunds_count: int
    refunds_amount: str


class PeriodListResponse(BaseModel):
    items: list[PeriodRowResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Tickets emitidos
# ---------------------------------------------------------------------------
@router.get("/tickets", dependencies=_reports)
async def list_tickets(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> TicketListResponse:
    limit, offset = limits
    rows, total = await service.tickets_page(
        session,
        issued_from=since,
        issued_to=until,
        terminal_id=terminal_id,
        limit=limit,
        offset=offset,
    )
    return TicketListResponse(
        items=[
            TicketReportRow(
                id=row.id,
                order_id=row.order_id,
                terminal_id=row.terminal_id,
                series=row.series,
                number=row.number,
                doc_number=row.doc_number,
                printed_at=row.printed_at,
                reprint_count=row.reprint_count,
                created_at=row.created_at,
                total_amount=_money(row.total_amount),
                user_id=row.user_id,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------
@router.get("/invoices", dependencies=_reports)
async def list_invoices(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[date, Query(alias="from")],
    until: Annotated[date, Query(alias="to")],
    status: InvoiceStatus | None = None,
    series: str | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> InvoiceListResponse:
    limit, offset = limits
    rows, total = await service.invoices_page(
        session,
        issue_from=since,
        issue_to=until,
        status=status,
        series=series,
        limit=limit,
        offset=offset,
    )
    return InvoiceListResponse(
        items=[
            InvoiceReportRow(
                id=row.id,
                customer_id=row.customer_id,
                series=row.series,
                year=row.year,
                number=row.number,
                status=row.status.value,
                issue_date=row.issue_date,
                total_base=_money(row.total_base),
                total_tax=_money(row.total_tax),
                total_amount=_money(row.total_amount),
                rectified_invoice_id=row.rectified_invoice_id,
                voided_at=row.voided_at,
                void_reason=row.void_reason,
                created_at=row.created_at,
                doc_number=row.doc_number,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/invoices/{invoice_id}", dependencies=_reports)
async def invoice_detail(
    session: DbSession, _user: CurrentUser, invoice_id: UUID
) -> object:
    """Factura detallada: el snapshot congelado con el que se emitió — el
    mismo render que la API de documentos, aquí bajo ``reports.view``."""

    invoice, lines = await service.invoice_detail(session, invoice_id)
    return _invoice_response(invoice, lines)


# ---------------------------------------------------------------------------
# Cierres pasados (Z)
# ---------------------------------------------------------------------------
@router.get("/cash-closures", dependencies=_reports)
async def list_cash_closures(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> ClosureListResponse:
    limit, offset = limits
    rows, total = await service.closures_page(
        session,
        closed_from=since,
        closed_to=until,
        terminal_id=terminal_id,
        limit=limit,
        offset=offset,
    )
    return ClosureListResponse(
        items=[_session_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Estadísticas
# ---------------------------------------------------------------------------
@router.get("/stats/summary", dependencies=_reports)
async def stats_summary(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
) -> SummaryResponse:
    s = await service.summary(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id
    )
    return SummaryResponse(
        sales_count=s.sales_count,
        sales_amount=_money(s.sales_amount),
        refunds_count=s.refunds_count,
        refunds_amount=_money(s.refunds_amount),
        voided_count=s.voided_count,
        net_amount=_money(s.net_amount),
        average_ticket=_money(s.average_ticket),
        tax_breakdown=[
            TaxLineResponse(
                tax_rate=_money(t.tax_rate),
                base=_money(t.base),
                total=_money(t.total),
            )
            for t in s.tax_breakdown
        ],
    )


@router.get("/stats/by-product", dependencies=_reports)
async def stats_by_product(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> ProductListResponse:
    limit, offset = limits
    rows, total = await service.by_product(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return ProductListResponse(
        items=[
            ProductRowResponse(
                product_id=row.product_id,
                name=row.name,
                orders=row.orders,
                quantity=str(row.quantity) if row.quantity is not None else None,
                base=_money(row.base),
                total=_money(row.total),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats/by-category", dependencies=_reports)
async def stats_by_category(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> CategoryListResponse:
    limit, offset = limits
    rows, total = await service.by_category(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return CategoryListResponse(
        items=[
            CategoryRowResponse(
                category_id=row.category_id,
                name=row.name,
                orders=row.orders,
                quantity=str(row.quantity) if row.quantity is not None else None,
                base=_money(row.base),
                total=_money(row.total),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats/by-waiter", dependencies=_reports)
async def stats_by_waiter(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> WaiterListResponse:
    limit, offset = limits
    rows, total = await service.by_waiter(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return WaiterListResponse(
        items=[
            WaiterRowResponse(
                user_id=row.user_id,
                username=row.username,
                full_name=row.full_name,
                sales_count=row.sales_count,
                refunds_count=row.refunds_count,
                total=_money(row.total),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats/by-payment-method", dependencies=_reports)
async def stats_by_payment_method(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> PaymentMethodListResponse:
    limit, offset = limits
    rows, total = await service.by_payment_method(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        limit=limit, offset=offset,
    )
    return PaymentMethodListResponse(
        items=[
            PaymentMethodRowResponse(
                code=row.code,
                kind=row.kind.value,
                sales_count=row.sales_count,
                sales_amount=_money(row.sales_amount),
                refunds_count=row.refunds_count,
                refunds_amount=_money(row.refunds_amount),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats/by-period", dependencies=_reports)
async def stats_by_period(
    session: DbSession,
    _user: CurrentUser,
    since: Annotated[datetime, Query(alias="from")],
    until: Annotated[datetime, Query(alias="to")],
    interval: Interval = "day",
    terminal_id: UUID | None = None,
    limits: LimitOffset = (DEFAULT_LIMIT, 0),
) -> PeriodListResponse:
    limit, offset = limits
    rows, total = await service.by_period(
        session, paid_from=since, paid_to=until, terminal_id=terminal_id,
        interval=interval, limit=limit, offset=offset,
    )
    return PeriodListResponse(
        items=[
            PeriodRowResponse(
                bucket=row.bucket,
                sales_count=row.sales_count,
                sales_amount=_money(row.sales_amount),
                refunds_count=row.refunds_count,
                refunds_amount=_money(row.refunds_amount),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )

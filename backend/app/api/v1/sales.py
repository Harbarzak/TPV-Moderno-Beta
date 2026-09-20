"""Motor de ventas (fase 06): ``/api/v1/sales``.

- ``POST   /sales/orders``                          crear borrador (guardar venta)
- ``GET    /sales/orders?status=draft``             recuperar ventas (borradores, cobradas…)
- ``GET    /sales/orders/{id}``                     detalle con líneas
- ``POST   /sales/orders/{id}/lines``               añadir línea (producto o artículo libre)
- ``PATCH  /sales/orders/{id}/lines/{line_id}``     cambiar cantidad/descuento/notas
- ``DELETE /sales/orders/{id}/lines/{line_id}``     quitar línea del borrador
- ``POST   /sales/orders/{id}/close``               cerrar la venta (carga en caja)
- ``POST   /sales/orders/{id}/void``                anular (motivo obligatorio, autor)
- ``POST   /sales/orders/{id}/refund``              devolución (orden negativa enlazada)

Contrato: el dinero viaja SIEMPRE como string (nunca float, §3). Operaciones
críticas transaccionales (servicio); las ventas cobradas o anuladas nunca se
borran. Cada cierre/anulación/devolución emite un evento genérico en
``sale_events`` (interfaz para el futuro FiscalAdapter, sin implementar aquí).

Permisos: ``sales.sell`` (crear y editar líneas), ``payments.take`` (cobrar),
``payments.refund`` (devolver), ``sales.void`` (anular); el descuento > 0 exige
además ``orders.discount`` (comprobado en el servicio).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status as http_status
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.api.idempotency import idempotency_of
from app.api.v1.payments import PaymentResponse, payment_response
from app.db.enums import OrderStatus, OrderType
from app.db.models.catalog import PaymentMethod
from app.db.models.sales import Order, OrderLine, Payment
from app.services import sales as sales_service
from app.services.idempotency import Replay
from app.services.sales import ClosePayment, NewLine, RefundLine

router = APIRouter(prefix="/sales", tags=["sales"])

_sell = [Depends(require_permission("sales.sell"))]
_take = [Depends(require_permission("payments.take"))]  # cobrar (fase 07)
_void = [Depends(require_permission("sales.void"))]
_refund = [Depends(require_permission("payments.refund"))]


# ---------------------------------------------------------------------------
# Dinero y cantidades: Decimal en Python, STRING en JSON (un float se rechaza).
# ---------------------------------------------------------------------------
def _reject_float(value):
    if isinstance(value, float):
        raise ValueError("El dinero se envía como string (nunca float)")
    return value


def _money_str(value: Decimal) -> str:
    return format(value, "f")


_MONEY_MAX = Decimal("9999999.99")   # numeric(12,2) con margen por pedido
_QTY_MAX = Decimal("9999999.999")    # numeric(10,3)

Money = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, le=_MONEY_MAX, decimal_places=2),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
]
RatePercent = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, le=100, decimal_places=2),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
]
Qty = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(gt=0, le=_QTY_MAX, decimal_places=3),
    PlainSerializer(_money_str, return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class OrderCreate(BaseModel):
    terminal_id: UUID
    order_type: OrderType = OrderType.bar
    dining_table_id: UUID | None = None
    customer_id: UUID | None = None
    guest_count: int | None = Field(None, ge=1, le=999)
    note: str | None = Field(None, max_length=500)


class LineCreate(BaseModel):
    """Línea: producto de catálogo (el precio/tipo lo congela el servicio)
    o artículo libre (name + unit_price + tax_rate en la petición)."""

    product_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=120)
    unit_price: Money | None = None
    tax_rate: RatePercent | None = None
    quantity: Qty
    discount_pct: RatePercent = Decimal("0")
    notes: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _exclusive_sources(self) -> "LineCreate":
        if self.product_id is not None:
            if self.name or self.unit_price is not None or self.tax_rate is not None:
                raise ValueError(
                    "Con product_id el nombre, precio y tipo se toman del catálogo"
                )
        elif self.name is None or self.unit_price is None or self.tax_rate is None:
            raise ValueError("El artículo libre requiere name, unit_price y tax_rate")
        return self


class LineUpdate(BaseModel):
    quantity: Qty | None = None
    discount_pct: RatePercent | None = None
    notes: str | None = Field(None, max_length=200)


class ClosePaymentIn(BaseModel):
    """Pago del cierre: forma + importe; ``tendered`` solo en efectivo."""

    payment_method_id: UUID
    amount: Money                      # lo que este pago aplica al total
    tendered: Money | None = None      # solo efectivo: lo entregado (cambio)
    external_ref: str | None = Field(None, max_length=120)


class OrderClose(BaseModel):
    cash_session_id: UUID
    # La suma de importes debe cubrir EXACTAMENTE el total (lo valida el backend).
    payments: list[ClosePaymentIn] = Field(min_length=1, max_length=20)


class OrderVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RefundLineIn(BaseModel):
    line_id: UUID
    quantity: Qty  # cantidad a devolver (positiva; ≤ vendida)


class RefundCreate(BaseModel):
    cash_session_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    lines: list[RefundLineIn] = Field(min_length=1, max_length=200)
    # Cómo sale el dinero devuelto (suma exacta del importe devuelto, sin cambio).
    payments: list[ClosePaymentIn] = Field(min_length=1, max_length=20)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class TaxSliceResponse(BaseModel):
    rate_bp: int
    base: str
    tax: str
    total: str


class TotalsResponse(BaseModel):
    base: str
    tax: str
    total: str
    slices: list[TaxSliceResponse]


class OrderLineResponse(BaseModel):
    id: UUID
    order_id: UUID
    product_id: UUID | None
    name: str
    unit_price: str
    tax_rate: str
    quantity: str
    discount_pct: str
    base: str
    total: str
    notes: str | None
    sort_order: int


class OrderResponse(BaseModel):
    id: UUID
    terminal_id: UUID
    user_id: UUID
    cash_session_id: UUID | None
    customer_id: UUID | None
    dining_table_id: UUID | None
    status: str
    order_type: str
    guest_count: int | None
    note: str | None
    total_base: str | None
    total_tax: str | None
    total_amount: str | None
    tax_summary: TotalsResponse | None
    paid_at: datetime | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime


class OrderDetailResponse(OrderResponse):
    lines: list[OrderLineResponse]
    payments: list[PaymentResponse]


class TicketRefResponse(BaseModel):
    """Referencia del ticket emitido con el cobro (fase 09): el documento
    completo está en ``GET /documents/tickets/{id}``."""

    id: UUID
    doc_number: str


class OrderCloseResponse(OrderResponse):
    """Respuesta del cobro: la venta con sus pagos, el cambio a devolver y el
    ticket emitido automáticamente."""

    payments: list[PaymentResponse]
    change_total: str
    ticket: TicketRefResponse


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    limit: int
    offset: int


def _line_response(line: OrderLine) -> OrderLineResponse:
    return OrderLineResponse(
        id=line.id,
        order_id=line.order_id,
        product_id=line.product_id,
        name=line.name,
        unit_price=format(line.unit_price, "f"),
        tax_rate=format(line.tax_rate, "f"),
        quantity=format(line.quantity, "f"),
        discount_pct=format(line.discount_pct, "f"),
        base=format(line.line_base, "f"),
        total=format(line.line_total, "f"),
        notes=line.notes,
        sort_order=line.sort_order,
    )


def _order_response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        terminal_id=order.terminal_id,
        user_id=order.user_id,
        cash_session_id=order.cash_session_id,
        customer_id=order.customer_id,
        dining_table_id=order.dining_table_id,
        status=order.status.value,
        order_type=order.order_type.value,
        guest_count=order.guest_count,
        note=order.note,
        total_base=format(order.total_base, "f") if order.total_base is not None else None,
        total_tax=format(order.total_tax, "f") if order.total_tax is not None else None,
        total_amount=(
            format(order.total_amount, "f") if order.total_amount is not None else None
        ),
        tax_summary=TotalsResponse.model_validate(order.tax_summary)
        if order.tax_summary
        else None,
        paid_at=order.paid_at,
        voided_at=order.voided_at,
        void_reason=order.void_reason,
        created_at=order.created_at,
    )


def _close_payments(items: list[ClosePaymentIn]) -> list[ClosePayment]:
    """Convierte la petición en los pagos que espera el servicio."""

    return [
        ClosePayment(
            payment_method_id=item.payment_method_id,
            amount=item.amount,
            tendered=item.tendered,
            external_ref=item.external_ref,
        )
        for item in items
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/orders", dependencies=_sell, response_model=OrderResponse,
             status_code=http_status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, session: DbSession, user: CurrentUser,
                       request: Request):
    # Fase 14: clave opcional — el reintento de «abrir ticket» no duplica pedidos.
    idem = idempotency_of(request, payload, endpoint="sales.create_order")
    result = await sales_service.create_order(
        session,
        user,
        terminal_id=payload.terminal_id,
        order_type=payload.order_type,
        dining_table_id=payload.dining_table_id,
        customer_id=payload.customer_id,
        guest_count=payload.guest_count,
        note=payload.note,
        idempotency=idem,
        snapshot=lambda order: _order_response(order).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _order_response(result)


@router.get("/orders", dependencies=_sell, response_model=OrderListResponse)
async def list_orders(
    session: DbSession,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OrderListResponse:
    rows, total = await sales_service.list_orders(
        session, status=status_filter, limit=limit, offset=offset
    )
    return OrderListResponse(
        items=[_order_response(order) for order in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/orders/{order_id}", dependencies=_sell, response_model=OrderDetailResponse)
async def get_order(order_id: UUID, session: DbSession) -> OrderDetailResponse:
    order, lines, payments = await sales_service.get_order_detail(session, order_id)
    return OrderDetailResponse(
        **_order_response(order).model_dump(),
        lines=[_line_response(line) for line in lines],
        payments=[payment_response(p, m) for p, m in payments],
    )


@router.post("/orders/{order_id}/lines", dependencies=_sell,
             response_model=OrderLineResponse,
             status_code=http_status.HTTP_201_CREATED)
async def add_line(
    order_id: UUID, payload: LineCreate, session: DbSession, user: CurrentUser,
    request: Request,
):
    # Fase 14: clave opcional — el reintento de «añadir línea» no duplica líneas.
    idem = idempotency_of(request, payload, endpoint="sales.add_line")
    result = await sales_service.add_line(
        session,
        user,
        order_id,
        NewLine(
            quantity=payload.quantity,
            discount_pct=payload.discount_pct,
            product_id=payload.product_id,
            name=payload.name,
            unit_price=payload.unit_price,
            tax_rate=payload.tax_rate,
            notes=payload.notes,
        ),
        idempotency=idem,
        snapshot=lambda line: _line_response(line).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _line_response(result)


@router.patch(
    "/orders/{order_id}/lines/{line_id}", dependencies=_sell,
    response_model=OrderLineResponse,
)
async def update_line(
    order_id: UUID, line_id: UUID, payload: LineUpdate,
    session: DbSession, user: CurrentUser,
):
    fields = payload.model_fields_set & {"quantity", "discount_pct", "notes"}
    line = await sales_service.update_line(
        session,
        user,
        order_id,
        line_id,
        fields={key: getattr(payload, key) for key in fields},
    )
    return _line_response(line)


@router.delete(
    "/orders/{order_id}/lines/{line_id}", dependencies=_sell,
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def remove_line(
    order_id: UUID, line_id: UUID, session: DbSession, user: CurrentUser
) -> Response:
    await sales_service.remove_line(session, user, order_id, line_id)
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


def _close_response(result: sales_service.CloseResult) -> OrderCloseResponse:
    """Cuerpo del cobro: también el snapshot que congela la idempotencia."""

    return OrderCloseResponse(
        **_order_response(result.order).model_dump(),
        payments=[payment_response(p, m) for p, m in result.payments],
        change_total=format(result.change_total, "f"),
        ticket=TicketRefResponse(
            id=result.ticket.id,
            doc_number=result.ticket.payload.get("doc_number", ""),
        ),
    )


@router.post("/orders/{order_id}/close", dependencies=_take,
             response_model=OrderCloseResponse)
async def close_order(
    order_id: UUID, payload: OrderClose, session: DbSession, user: CurrentUser,
    request: Request,
):
    # Fase 14 (§4.1): el cobro EXIGE Idempotency-Key — si se pierde la
    # respuesta, reintentar con la misma clave devuelve el mismo ticket.
    idem = idempotency_of(request, payload, endpoint="sales.close_order", required=True)
    hardware = getattr(request.app.state, "hardware", None)
    result = await sales_service.close_order(
        session,
        user,
        order_id,
        cash_session_id=payload.cash_session_id,
        payments=_close_payments(payload.payments),
        drawer_adapter=hardware.drawer if hardware else None,
        idempotency=idem,
        snapshot=lambda r: _close_response(r).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _close_response(result)


@router.post("/orders/{order_id}/void", dependencies=_void, response_model=OrderResponse)
async def void_order(
    order_id: UUID, payload: OrderVoid, session: DbSession, user: CurrentUser
):
    order = await sales_service.void_order(session, user, order_id, reason=payload.reason)
    return _order_response(order)


@router.post("/orders/{order_id}/refund", dependencies=_refund, response_model=OrderResponse,
             status_code=http_status.HTTP_201_CREATED)
async def refund_order(
    order_id: UUID, payload: RefundCreate, session: DbSession, user: CurrentUser
):
    refund = await sales_service.refund_order(
        session,
        user,
        order_id,
        cash_session_id=payload.cash_session_id,
        reason=payload.reason,
        lines=[
            RefundLine(line_id=item.line_id, quantity=item.quantity)
            for item in payload.lines
        ],
        payments=_close_payments(payload.payments),
    )
    return _order_response(refund)

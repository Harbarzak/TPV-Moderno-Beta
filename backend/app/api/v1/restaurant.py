"""Sala y mesas (fase 30 · Modo restaurante): ``/api/v1/restaurant``.

- ``GET    /restaurant/tables``                     el plano: mesas + estado derivado
- ``GET    /restaurant/zones``                      zonas de sala
- ``POST   /restaurant/zones``                      crear zona
- ``PATCH  /restaurant/zones/{id}``                 renombrar/ordenar/activar
- ``POST   /restaurant/tables``                     crear mesa
- ``PATCH  /restaurant/tables/{id}``                renombrar/mover/mover de zona
- ``POST   /restaurant/tables/{id}/open``           abrir sesión de mesa (borrador)
- ``POST   /restaurant/tables/{id}/transfer``       traspasar comanda a otra mesa
- ``POST   /restaurant/tables/{id}/merge``          juntar con la comanda de otra mesa
- ``POST   /restaurant/tables/{id}/split``          dividir cuenta hacia otra mesa
- ``PATCH  /restaurant/orders/{id}``                notas/comensales de la sesión
- ``POST   /restaurant/orders/{id}/bill``           «cuenta pedida»
- ``POST   /restaurant/orders/{id}/bill/cancel``    deshacer «cuenta pedida»

Las LÍNEAS del pedido se gestionan por ``/sales`` (la sesión de mesa ES un
borrador de ventas): añadir producto, cantidades, notas de línea y el cobro
siguen siendo el mismo flujo (§4.2).

Permisos: todo el módulo pide ``restaurant.operate`` (waiter y manager lo
traen de semilla; el POS de mostrador lo usa para el mapa). El dinero sigue
viajando como string (§3) y las posiciones del plano como string Decimal.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status as http_status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.api.idempotency import idempotency_of
from app.api.v1.sales import OrderLineResponse, OrderResponse, _line_response, _order_response
from app.db.models.sales import DiningTable, Zone
from app.services import restaurant as restaurant_service
from app.services.idempotency import Replay
from app.services.restaurant import SplitMove

router = APIRouter(prefix="/restaurant", tags=["restaurant"])

_operate = [Depends(require_permission("restaurant.operate"))]


def _reject_float(value):
    if isinstance(value, float):
        raise ValueError("Las posiciones se envían como string (nunca float)")
    return value


def _pos_str(value: Decimal) -> str:
    return format(value, "f")


# Posición en el plano: porcentaje del lienzo (0-100), string en JSON.
Pos = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, le=100, decimal_places=2),
    PlainSerializer(_pos_str, return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = 0


class ZoneUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    sort_order: int | None = None
    active: bool | None = None


class TableCreate(BaseModel):
    zone_id: UUID
    name: str = Field(min_length=1, max_length=120)
    seats: int = Field(ge=1, le=99)
    sort_order: int = 0
    pos_x: Pos | None = None
    pos_y: Pos | None = None


class TableUpdate(BaseModel):
    zone_id: UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=120)
    seats: int | None = Field(None, ge=1, le=99)
    sort_order: int | None = None
    pos_x: Pos | None = None
    pos_y: Pos | None = None
    active: bool | None = None


class TableOpen(BaseModel):
    terminal_id: UUID
    guest_count: int | None = Field(None, ge=1, le=999)
    note: str | None = Field(None, max_length=500)


class TableTransfer(BaseModel):
    target_table_id: UUID


class TableMerge(BaseModel):
    target_table_id: UUID


class SplitLine(BaseModel):
    line_id: UUID
    quantity: Annotated[
        Decimal,
        BeforeValidator(_reject_float),
        Field(gt=0, decimal_places=3),
        PlainSerializer(_pos_str, return_type=str, when_used="json"),
    ]


class TableSplit(BaseModel):
    target_table_id: UUID
    lines: list[SplitLine] = Field(min_length=1, max_length=200)


class SessionUpdate(BaseModel):
    guest_count: int | None = Field(None, ge=1, le=999)
    note: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class ZoneResponse(BaseModel):
    id: UUID
    name: str
    sort_order: int
    active: bool


class TableStateResponse(BaseModel):
    """Una mesa del plano: configuración + estado derivado del semáforo.

    ``status``: ``free`` (libre) | ``open`` (comanda abierta + tiempo) |
    ``bill`` (cuenta pedida + importe). Nunca solo color (§3.2)."""

    id: UUID
    zone_id: UUID
    zone_name: str
    name: str
    seats: int
    sort_order: int
    pos_x: str | None
    pos_y: str | None
    active: bool
    status: str
    order_id: UUID | None
    guest_count: int | None
    note: str | None
    waiter: str | None
    opened_at: datetime | None
    bill_requested_at: datetime | None
    open_total: str | None


class ZoneListResponse(BaseModel):
    items: list[ZoneResponse]


class TableListResponse(BaseModel):
    items: list[TableStateResponse]


class RestaurantOrderDetailResponse(OrderResponse):
    """Pedido de sala con sus líneas (juntada/división)."""

    lines: list[OrderLineResponse]


def _zone_response(zone: Zone) -> ZoneResponse:
    return ZoneResponse(
        id=zone.id, name=zone.name, sort_order=zone.sort_order, active=zone.active
    )


def _table_response(state: restaurant_service.TableState) -> TableStateResponse:
    table: DiningTable = state.table
    order = state.order
    return TableStateResponse(
        id=table.id,
        zone_id=table.zone_id,
        zone_name=state.zone.name,
        name=table.name,
        seats=table.seats,
        sort_order=table.sort_order,
        pos_x=format(table.pos_x, "f") if table.pos_x is not None else None,
        pos_y=format(table.pos_y, "f") if table.pos_y is not None else None,
        active=table.active,
        status=state.status,
        order_id=order.id if order else None,
        guest_count=order.guest_count if order else None,
        note=order.note if order else None,
        waiter=state.waiter,
        opened_at=order.created_at if order else None,
        bill_requested_at=order.bill_requested_at if order else None,
        open_total=format(state.open_total, "f") if state.open_total is not None else None,
    )


def _detail_response(order, lines) -> RestaurantOrderDetailResponse:
    return RestaurantOrderDetailResponse(
        **_order_response(order).model_dump(),
        lines=[_line_response(line) for line in lines],
    )


# ---------------------------------------------------------------------------
# Plano y configuración de sala
# ---------------------------------------------------------------------------
@router.get("/tables", dependencies=_operate, response_model=TableListResponse)
async def list_tables(
    session: DbSession,
    include_inactive: Annotated[bool, Query()] = False,
) -> TableListResponse:
    _, states = await restaurant_service.floor(
        session, include_inactive=include_inactive
    )
    return TableListResponse(items=[_table_response(state) for state in states])


@router.get("/zones", dependencies=_operate, response_model=ZoneListResponse)
async def list_zones(
    session: DbSession,
    include_inactive: Annotated[bool, Query()] = False,
) -> ZoneListResponse:
    zones = await restaurant_service.zones(session, include_inactive=include_inactive)
    return ZoneListResponse(items=[_zone_response(zone) for zone in zones])


@router.post("/zones", dependencies=_operate, response_model=ZoneResponse,
             status_code=http_status.HTTP_201_CREATED)
async def create_zone(payload: ZoneCreate, session: DbSession, user: CurrentUser):
    zone = await restaurant_service.create_zone(
        session, user, name=payload.name, sort_order=payload.sort_order
    )
    return _zone_response(zone)


@router.patch("/zones/{zone_id}", dependencies=_operate, response_model=ZoneResponse)
async def update_zone(
    zone_id: UUID, payload: ZoneUpdate, session: DbSession, user: CurrentUser
):
    fields = payload.model_fields_set & {"name", "sort_order", "active"}
    zone = await restaurant_service.update_zone(
        session, user, zone_id, fields={key: getattr(payload, key) for key in fields}
    )
    return _zone_response(zone)


@router.post("/tables", dependencies=_operate, response_model=TableStateResponse,
             status_code=http_status.HTTP_201_CREATED)
async def create_table(payload: TableCreate, session: DbSession, user: CurrentUser):
    table = await restaurant_service.create_table(
        session,
        user,
        zone_id=payload.zone_id,
        name=payload.name,
        seats=payload.seats,
        sort_order=payload.sort_order,
        pos_x=payload.pos_x,
        pos_y=payload.pos_y,
    )
    # Recién creada: libre, sin comanda (una lectura por coherencia de estado).
    zone = await restaurant_service.get_zone(session, table.zone_id)
    state = restaurant_service.TableState(
        table=table, zone=zone, order=None, waiter=None,
        open_total=None, status=restaurant_service.FREE,
    )
    return _table_response(state)


@router.patch("/tables/{table_id}", dependencies=_operate, response_model=TableStateResponse)
async def update_table(
    table_id: UUID, payload: TableUpdate, session: DbSession, user: CurrentUser
):
    fields = payload.model_fields_set & {
        "zone_id", "name", "seats", "sort_order", "pos_x", "pos_y", "active",
    }
    table = await restaurant_service.update_table(
        session, user, table_id, fields={key: getattr(payload, key) for key in fields}
    )
    zone = await restaurant_service.get_zone(session, table.zone_id)
    state = restaurant_service.TableState(
        table=table, zone=zone, order=None, waiter=None,
        open_total=None, status=restaurant_service.FREE,
    )
    return _table_response(state)


# ---------------------------------------------------------------------------
# Sesión de mesa y movimientos
# ---------------------------------------------------------------------------
@router.post("/tables/{table_id}/open", dependencies=_operate,
             response_model=OrderResponse, status_code=http_status.HTTP_201_CREATED)
async def open_table(
    table_id: UUID, payload: TableOpen, session: DbSession, user: CurrentUser,
    request: Request,
):
    # Clave opcional: el reintento de «abrir mesa» no duplica la comanda.
    idem = idempotency_of(request, payload, endpoint="restaurant.open_table")
    result = await restaurant_service.open_table(
        session,
        user,
        table_id,
        terminal_id=payload.terminal_id,
        guest_count=payload.guest_count,
        note=payload.note,
        idempotency=idem,
        snapshot=lambda order: _order_response(order).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _order_response(result)


@router.post("/tables/{table_id}/transfer", dependencies=_operate,
             response_model=OrderResponse)
async def transfer_table(
    table_id: UUID, payload: TableTransfer, session: DbSession, user: CurrentUser
):
    order = await restaurant_service.transfer_table(
        session, user, table_id, target_table_id=payload.target_table_id
    )
    return _order_response(order)


@router.post("/tables/{table_id}/merge", dependencies=_operate,
             response_model=RestaurantOrderDetailResponse)
async def merge_tables(
    table_id: UUID, payload: TableMerge, session: DbSession, user: CurrentUser
):
    order, lines = await restaurant_service.merge_tables(
        session, user, table_id, target_table_id=payload.target_table_id
    )
    return _detail_response(order, lines)


@router.post("/tables/{table_id}/split", dependencies=_operate,
             response_model=RestaurantOrderDetailResponse)
async def split_table(
    table_id: UUID, payload: TableSplit, session: DbSession, user: CurrentUser
):
    order, lines = await restaurant_service.split_table(
        session,
        user,
        table_id,
        target_table_id=payload.target_table_id,
        moves=[
            SplitMove(line_id=item.line_id, quantity=item.quantity)
            for item in payload.lines
        ],
    )
    return _detail_response(order, lines)


@router.patch("/orders/{order_id}", dependencies=_operate, response_model=OrderResponse)
async def update_session(
    order_id: UUID, payload: SessionUpdate, session: DbSession, user: CurrentUser
):
    fields = payload.model_fields_set & {"guest_count", "note"}
    order = await restaurant_service.update_session(
        session, user, order_id, fields={key: getattr(payload, key) for key in fields}
    )
    return _order_response(order)


@router.post("/orders/{order_id}/bill", dependencies=_operate, response_model=OrderResponse)
async def request_bill(order_id: UUID, session: DbSession, user: CurrentUser):
    order = await restaurant_service.request_bill(session, user, order_id)
    return _order_response(order)


@router.post("/orders/{order_id}/bill/cancel", dependencies=_operate,
             response_model=OrderResponse)
async def cancel_bill(order_id: UUID, session: DbSession, user: CurrentUser):
    order = await restaurant_service.cancel_bill(session, user, order_id)
    return _order_response(order)

"""Kitchen Display System (fase 32): ``/api/v1/kds``.

- ``GET   /kds/board``                       el tablero: comandas abiertas + últimas servidas
- ``GET   /kds/stations``                    estaciones de cocina
- ``POST  /kds/stations``                    crear estación
- ``PATCH /kds/stations/{id}``               renombrar/ordenar/activar
- ``PATCH /kds/lines/{id}/status``           avanzar/retroceder UNA línea (toque)
- ``PATCH /kds/tickets/{id}/status``         «todo listo» / «servido» masivo
- ``PATCH /kds/tickets/{id}/priority``       urgencia de la comanda
- ``POST  /kds/tickets/{id}/print``          reimprimir KOT completo

Pantalla de COCINA, separada del frontend principal del TPV: su SPA vive en
``frontend/kds`` y solo consume este módulo + el hub WebSocket (topic ``kds``).
Las comandas NACEN en ``/sales`` (al añadir un producto de cocina a un
borrador) — aquí solo se cocinan y se sirven; no hay creación manual.

Permisos: todo el módulo pide ``kds.operate`` (waiter y manager lo traen de
semilla). El dinero no aparece: el KDS no cobra. Las cantidades viajan como
string «X.XXX» (§3) y los estados como ``kitchenstatus`` (NUEVO/PREPARANDO/
LISTO/SERVIDO/CANCELADA).
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status as http_status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.core.errors import AppError, ErrorCode
from app.api.v1.printing import PrintJobResponse, _job_response
from app.db.enums import KitchenStatus
from app.db.models.restaurant import KitchenStation
from app.services import kitchen as kitchen_service

router = APIRouter(prefix="/kds", tags=["kds"])

_operate = [Depends(require_permission("kds.operate"))]

#: El KDS nunca marca CANCELADA a mano: la cancelación llega de /sales
#: (rectificación o anulación de la venta).
LineTarget = Literal["pending", "preparing", "ready", "served"]
TicketTarget = Literal["pending", "preparing", "ready", "served"]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class StationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sort_order: int = Field(default=0, ge=0)


class StationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    sort_order: int | None = Field(None, ge=0)
    active: bool | None = None


class LineStatusPatch(BaseModel):
    status: LineTarget


class TicketStatusPatch(BaseModel):
    status: TicketTarget


class TicketPriorityPatch(BaseModel):
    priority: int = Field(ge=0, le=1)  # 0 = normal, 1 = urgente


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class StationResponse(BaseModel):
    id: UUID
    name: str
    sort_order: int
    active: bool
    created_at: datetime


class StationListResponse(BaseModel):
    items: list[StationResponse]


class BoardLineResponse(BaseModel):
    id: UUID
    order_line_id: UUID
    name: str
    quantity: str
    notes: str | None
    status: str
    station_id: UUID | None


class BoardTicketResponse(BaseModel):
    id: UUID
    order_id: UUID
    status: str
    priority: int
    created_at: datetime
    ready_at: datetime | None
    served_at: datetime | None
    order_type: str
    table_name: str | None
    lines: list[BoardLineResponse]


class BoardResponse(BaseModel):
    items: list[BoardTicketResponse]


def _station_response(station: KitchenStation) -> StationResponse:
    return StationResponse(
        id=station.id,
        name=station.name,
        sort_order=station.sort_order,
        active=station.active,
        created_at=station.created_at,
    )


def _qty(value: Decimal) -> str:
    return format(value, "f")


def _ticket_response(ticket) -> BoardTicketResponse:
    return BoardTicketResponse(
        id=ticket.id,
        order_id=ticket.order_id,
        status=ticket.status.value,
        priority=ticket.priority,
        created_at=ticket.created_at,
        ready_at=ticket.ready_at,
        served_at=ticket.served_at,
        order_type=ticket.order_type,
        table_name=ticket.table_name,
        lines=[
            BoardLineResponse(
                id=line.id,
                order_line_id=line.order_line_id,
                name=line.name,
                quantity=_qty(line.quantity),
                notes=line.notes,
                status=line.status.value,
                station_id=line.station_id,
            )
            for line in ticket.lines
        ],
    )


# ---------------------------------------------------------------------------
# Tablero
# ---------------------------------------------------------------------------
@router.get("/board", response_model=BoardResponse, dependencies=_operate)
async def get_board(
    session: DbSession,
    served_limit: int = Query(default=10, ge=0, le=50),
) -> BoardResponse:
    """Comandas abiertas (urgentes primero, luego FIFO) + últimas servidas."""
    tickets = await kitchen_service.get_board(session, served_limit=served_limit)
    return BoardResponse(items=[_ticket_response(ticket) for ticket in tickets])


@router.patch(
    "/lines/{line_id}/status",
    response_model=BoardTicketResponse,
    dependencies=_operate,
)
async def set_line_status(
    line_id: UUID, payload: LineStatusPatch, session: DbSession, user: CurrentUser
) -> BoardTicketResponse:
    """Toque en una línea: un paso adelante (o atrás). La cabecera se deriva."""
    ticket = await kitchen_service.set_line_status(
        session, user, line_id, KitchenStatus(payload.status)
    )
    if ticket is None:  # servida y fuera de la ventana reciente
        raise AppError(404, ErrorCode.NOT_FOUND, "La comanda ya no está en el tablero")
    return _ticket_response(ticket)


@router.patch(
    "/tickets/{ticket_id}/status",
    response_model=BoardTicketResponse,
    dependencies=_operate,
)
async def set_ticket_status(
    ticket_id: UUID, payload: TicketStatusPatch, session: DbSession, user: CurrentUser
) -> BoardTicketResponse:
    """«Todo listo» / «Servido»: pone todas las líneas activas en el estado."""
    ticket = await kitchen_service.set_ticket_status(
        session, user, ticket_id, KitchenStatus(payload.status)
    )
    if ticket is None:
        raise AppError(404, ErrorCode.NOT_FOUND, "La comanda ya no está en el tablero")
    return _ticket_response(ticket)


@router.patch("/tickets/{ticket_id}/priority", status_code=http_status.HTTP_204_NO_CONTENT, dependencies=_operate)
async def set_ticket_priority(
    ticket_id: UUID, payload: TicketPriorityPatch, session: DbSession, user: CurrentUser
) -> None:
    await kitchen_service.set_ticket_priority(session, user, ticket_id, priority=payload.priority)


@router.post(
    "/tickets/{ticket_id}/print",
    response_model=PrintJobResponse,
    status_code=http_status.HTTP_201_CREATED,
    dependencies=_operate,
)
async def reprint_kot(
    ticket_id: UUID, session: DbSession, user: CurrentUser
) -> PrintJobResponse:
    """Reimpresión completa del KOT (todas las líneas activas)."""
    job = await kitchen_service.reprint_kot(session, user, ticket_id)
    return _job_response(job)


# ---------------------------------------------------------------------------
# Estaciones
# ---------------------------------------------------------------------------
@router.get("/stations", response_model=StationListResponse, dependencies=_operate)
async def list_stations(
    session: DbSession, include_inactive: bool = False
) -> StationListResponse:
    stations = await kitchen_service.list_stations(session, include_inactive=include_inactive)
    return StationListResponse(items=[_station_response(station) for station in stations])


@router.post(
    "/stations",
    response_model=StationResponse,
    status_code=http_status.HTTP_201_CREATED,
    dependencies=_operate,
)
async def create_station(
    payload: StationCreate, session: DbSession, user: CurrentUser
) -> StationResponse:
    station = await kitchen_service.create_station(
        session, user, name=payload.name, sort_order=payload.sort_order
    )
    return _station_response(station)


@router.patch("/stations/{station_id}", response_model=StationResponse, dependencies=_operate)
async def update_station(
    station_id: UUID, payload: StationUpdate, session: DbSession, user: CurrentUser
) -> StationResponse:
    station = await kitchen_service.update_station(
        session,
        user,
        station_id,
        name=payload.name,
        sort_order=payload.sort_order,
        active=payload.active,
    )
    return _station_response(station)

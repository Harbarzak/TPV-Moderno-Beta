"""Caja (fase 08): ``/api/v1/cash``.

- ``POST /cash/sessions``                        apertura con fondo inicial
- ``GET  /cash/sessions``                        histórico y cuadres Z (informes)
- ``GET  /cash/sessions/current?terminal_id=``   sesión abierta del terminal
- ``GET  /cash/sessions/{id}``                   la sesión (¿sigue abierta?)
- ``GET  /cash/sessions/{id}/report``            listado X (abierta) / Z (cerrada)
- ``POST /cash/sessions/{id}/movements``         entrada/salida de efectivo
- ``POST /cash/sessions/{id}/counts``            arqueo parcial (no cierra)
- ``POST /cash/sessions/{id}/close``             cierre Z con arqueo

Contrato: el dinero viaja SIEMPRE como string (nunca float, §3). El efectivo
esperado lo calcula el backend (fondo inicial + ventas en efectivo −
devoluciones en efectivo + entradas − salidas) y la diferencia del arqueo es
contado − esperado; el cliente solo aporta el recuento físico. Una sola sesión
abierta por terminal (índice parcial en BD; carrera → 409).

Permisos: ``cash.open`` (abrir y consultar sesión), ``cash.movements``
(entradas/salidas), ``cash.close`` (arqueos y cierre), ``reports.view``
(histórico/cuadres). Auditoría: ``cash.*``.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status as http_status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.api.idempotency import idempotency_of
from app.api.v1.sales import Money
from app.db.enums import CashMoveKind
from app.db.models.cash import CashCount, CashCountLine, CashMovement, CashSession
from app.services import cash as cash_service
from app.services.cash import CashReport
from app.services.idempotency import Replay

router = APIRouter(prefix="/cash", tags=["cash"])

_open = [Depends(require_permission("cash.open"))]
_move = [Depends(require_permission("cash.movements"))]
_close = [Depends(require_permission("cash.close"))]
_reports = [Depends(require_permission("reports.view"))]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class SessionOpen(BaseModel):
    terminal_id: UUID
    opening_amount: Money = Decimal("0")  # fondo inicial contado


class MovementCreate(BaseModel):
    kind: CashMoveKind  # in = entrada de efectivo, out = salida
    amount: Money
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _positive(self) -> "MovementCreate":
        if self.amount <= 0:
            raise ValueError("El importe del movimiento debe ser mayor que cero")
        return self


class CountLineIn(BaseModel):
    denomination: Money  # valor facial de la pieza
    quantity: int = Field(ge=0)

    @model_validator(mode="after")
    def _positive(self) -> "CountLineIn":
        if self.denomination <= 0:
            raise ValueError("La denominación debe ser mayor que cero")
        return self


class CountCreate(BaseModel):
    lines: list[CountLineIn] = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
def _money(value: Decimal) -> str:
    return format(value, "f")


class CashSessionResponse(BaseModel):
    id: UUID
    terminal_id: UUID
    opened_by: UUID
    closed_by: UUID | None
    opening_amount: str
    expected_amount: str | None
    counted_amount: str | None
    difference: str | None
    status: str  # "open" | "closed"
    opened_at: datetime
    closed_at: datetime | None


class MovementResponse(BaseModel):
    id: UUID
    cash_session_id: UUID
    kind: str
    amount: str
    reason: str
    user_id: UUID
    created_at: datetime


class CountLineResponse(BaseModel):
    denomination: str
    quantity: int


class CountResponse(BaseModel):
    id: UUID
    cash_session_id: UUID
    counted_by: UUID
    counted_amount: str
    # Congelado solo en la respuesta del arqueo recién registrado; en listados
    # el cuadre vigente es el del informe, no el de cada recuento.
    expected_amount: str | None = None
    difference: str | None = None
    created_at: datetime
    lines: list[CountLineResponse]


class MethodTotalResponse(BaseModel):
    code: str
    kind: str
    sales_total: str
    sales_count: int
    refunds_total: str
    refunds_count: int


class CashReportResponse(BaseModel):
    """Listado X (sesión abierta) o Z (cerrada)."""

    session: CashSessionResponse
    opening_amount: str
    cash_in: str
    cash_out: str
    cash_sales: str
    cash_refunds: str
    expected_cash: str
    difference: str | None  # solo congelada al cierre
    method_totals: list[MethodTotalResponse]
    movements: list[MovementResponse]
    counts: list[CountResponse]


class SessionListResponse(BaseModel):
    items: list[CashSessionResponse]
    total: int
    limit: int
    offset: int


def _session_response(cash: CashSession) -> CashSessionResponse:
    return CashSessionResponse(
        id=cash.id,
        terminal_id=cash.terminal_id,
        opened_by=cash.opened_by,
        closed_by=cash.closed_by,
        opening_amount=_money(cash.opening_amount),
        expected_amount=_money(cash.expected_amount)
        if cash.expected_amount is not None
        else None,
        counted_amount=_money(cash.counted_amount)
        if cash.counted_amount is not None
        else None,
        difference=_money(cash.difference) if cash.difference is not None else None,
        status="open" if cash.closed_at is None else "closed",
        opened_at=cash.opened_at,
        closed_at=cash.closed_at,
    )


def _movement_response(movement: CashMovement) -> MovementResponse:
    return MovementResponse(
        id=movement.id,
        cash_session_id=movement.cash_session_id,
        kind=movement.kind.value,
        amount=_money(movement.amount),
        reason=movement.reason,
        user_id=movement.user_id,
        created_at=movement.created_at,
    )


def _count_response(
    count: CashCount,
    lines: list[CashCountLine],
    *,
    expected: Decimal | None = None,
    difference: Decimal | None = None,
) -> CountResponse:
    return CountResponse(
        id=count.id,
        cash_session_id=count.cash_session_id,
        counted_by=count.counted_by,
        counted_amount=_money(count.counted_amount),
        expected_amount=_money(expected) if expected is not None else None,
        difference=_money(difference) if difference is not None else None,
        created_at=count.created_at,
        lines=[
            CountLineResponse(denomination=_money(line.denomination), quantity=line.quantity)
            for line in lines
        ],
    )


def _report_response(report: CashReport) -> CashReportResponse:
    cash = report.session
    return CashReportResponse(
        session=_session_response(cash),
        opening_amount=_money(cash.opening_amount),
        cash_in=_money(report.cash_in),
        cash_out=_money(report.cash_out),
        cash_sales=_money(report.cash_sales),
        cash_refunds=_money(report.cash_refunds),
        expected_cash=_money(report.expected_cash),
        difference=_money(cash.difference) if cash.difference is not None else None,
        method_totals=[
            MethodTotalResponse(
                code=total.code,
                kind=total.kind,
                sales_total=_money(total.sales_total),
                sales_count=total.sales_count,
                refunds_total=_money(total.refunds_total),
                refunds_count=total.refunds_count,
            )
            for total in report.method_totals
        ],
        movements=[_movement_response(movement) for movement in report.movements],
        counts=[
            _count_response(count, lines) for count, lines in report.counts
        ],
    )


def _count_lines(lines: list[CountLineIn]) -> list[cash_service.CountLine]:
    return [
        cash_service.CountLine(denomination=line.denomination, quantity=line.quantity)
        for line in lines
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/sessions", dependencies=_open, response_model=CashSessionResponse,
             status_code=http_status.HTTP_201_CREATED)
async def open_session(payload: SessionOpen, session: DbSession, user: CurrentUser,
                       request: Request):
    # Fase 14: clave opcional — el reintento de la apertura no choca con 409.
    idem = idempotency_of(request, payload, endpoint="cash.open_session")
    result = await cash_service.open_session(
        session,
        user,
        terminal_id=payload.terminal_id,
        opening_amount=payload.opening_amount,
        idempotency=idem,
        snapshot=lambda cash: _session_response(cash).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _session_response(result)


@router.get("/sessions", dependencies=_reports, response_model=SessionListResponse)
async def list_sessions(
    session: DbSession,
    terminal_id: UUID | None = None,
    status_filter: Annotated[str | None, Query(alias="status",
                                               pattern="^(open|closed)$")] = None,
    opened_from: Annotated[datetime | None, Query()] = None,
    opened_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionListResponse:
    rows, total = await cash_service.list_sessions(
        session,
        terminal_id=terminal_id,
        open_only=status_filter == "open",
        closed_only=status_filter == "closed",
        opened_from=opened_from,
        opened_to=opened_to,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(
        items=[_session_response(cash) for cash in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/sessions/current", dependencies=_open, response_model=CashSessionResponse)
async def current_session(session: DbSession, terminal_id: UUID) -> CashSessionResponse:
    cash = await cash_service.current_session(session, terminal_id)
    return _session_response(cash)


@router.get("/sessions/{session_id}", dependencies=_open, response_model=CashSessionResponse)
async def get_session(session_id: UUID, session: DbSession) -> CashSessionResponse:
    cash = await cash_service.get_session(session, session_id)
    return _session_response(cash)


@router.get("/sessions/{session_id}/report", dependencies=_open,
            response_model=CashReportResponse)
async def session_report(session_id: UUID, session: DbSession) -> CashReportResponse:
    report = await cash_service.session_report(session, session_id)
    return _report_response(report)


@router.post("/sessions/{session_id}/movements", dependencies=_move,
             response_model=MovementResponse,
             status_code=http_status.HTTP_201_CREATED)
async def add_movement(
    session_id: UUID, payload: MovementCreate, session: DbSession, user: CurrentUser,
    request: Request,
):
    # Fase 14: clave opcional — el reintento no duplica el movimiento.
    idem = idempotency_of(request, payload, endpoint="cash.add_movement")
    result = await cash_service.add_movement(
        session,
        user,
        session_id,
        kind=payload.kind,
        amount=payload.amount,
        reason=payload.reason,
        idempotency=idem,
        snapshot=lambda movement: _movement_response(movement).model_dump(mode="json"),
    )
    if isinstance(result, Replay):
        return JSONResponse(content=result.body, status_code=result.status)
    return _movement_response(result)


@router.post("/sessions/{session_id}/counts", dependencies=_close,
             response_model=CountResponse,
             status_code=http_status.HTTP_201_CREATED)
async def add_count(
    session_id: UUID, payload: CountCreate, session: DbSession, user: CurrentUser
):
    result = await cash_service.add_count(
        session, user, session_id, lines=_count_lines(payload.lines)
    )
    return _count_response(
        result.count,
        result.lines,
        expected=result.expected,
        difference=result.difference,
    )


@router.post("/sessions/{session_id}/close", dependencies=_close,
             response_model=CashSessionResponse)
async def close_session(
    session_id: UUID, payload: CountCreate, session: DbSession, user: CurrentUser
):
    result = await cash_service.close_session(
        session, user, session_id, lines=_count_lines(payload.lines)
    )
    return _session_response(result.session)

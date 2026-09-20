"""Servicio de caja (fase 08 · Caja): sesiones, movimientos, arqueos, cierre
e informes X/Z (ARCHITECTURE.md §5).

Reglas transversales:
- **Una sola sesión abierta por terminal**: la regla la fija el índice parcial
  ``uq_cash_sessions_open_per_terminal``; el servicio la comprueba antes y deja
  que la BD resuelva la carrera (dos aperturas concurrentes: una gana, la otra
  409).
- Toda operación sobre una sesión la bloquea (``SELECT … FOR UPDATE``) y
  rechaza si ya está cerrada: ni movimientos ni arqueos sobre el histórico.
- El efectivo **esperado** se recalcula SIEMPRE desde las piezas (fondo inicial
  + ventas en efectivo − devoluciones en efectivo + entradas − salidas), nunca
  se confía en el frontend; la **diferencia** del arqueo es contado − esperado.
- El cierre exige recuento completo (``ck_cash_sessions_close_complete``):
  queda congelado en la fila de la sesión y el recuento se persiste como
  ``cash_counts`` (inmutable).
- Auditoría obligatoria de movimientos, arqueos, aperturas y cierres (§6).
- Reintentos idempotentes (fase 14): apertura y movimientos aceptan una
  ``Idempotency-Key`` opcional (respuesta congelada, sin duplicar efectos).

Nunca importa de ``api``.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.db.enums import CashMoveKind
from app.db.models.cash import CashCount, CashCountLine, CashMovement, CashSession
from app.domain import cash as cash_calc
from app.domain.sales import cents_to_decimal, money_to_cents
from app.repos import auth as auth_repo
from app.repos import cash as repo
from app.repos.sales import get_terminal, utcnow
from app.services import events as events_service
from app.services.auth import Principal
from app.services.idempotency import (
    Idempotency,
    Replay,
    Snapshot,
    lookup as idem_lookup,
    replay_after_conflict as idem_replay,
    store as idem_store,
)


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


@dataclass(frozen=True, slots=True)
class CountLine:
    """Una denominación del recuento físico (denominación > 0, cantidad ≥ 0)."""

    denomination: Decimal
    quantity: int


@dataclass(frozen=True, slots=True)
class CountResult:
    """Arqueo registrado con el cuadre del momento."""

    count: CashCount
    lines: list[CashCountLine]
    counted: Decimal
    expected: Decimal
    difference: Decimal


@dataclass(frozen=True, slots=True)
class CloseCashResult:
    """Cierre Z: la sesión congelada y su cuadre."""

    session: CashSession
    counted: Decimal
    expected: Decimal
    difference: Decimal
    count: CashCount


@dataclass(frozen=True, slots=True)
class MethodTotal:
    """Totales de una forma de pago dentro de la sesión."""

    code: str
    kind: str
    sales_total: Decimal
    sales_count: int
    refunds_total: Decimal
    refunds_count: int


@dataclass(frozen=True, slots=True)
class CashReport:
    """Listado X (sesión abierta) o Z (cerrada): todo el dinero de la sesión."""

    session: CashSession
    cash_in: Decimal
    cash_out: Decimal
    cash_sales: Decimal
    cash_refunds: Decimal
    expected_cash: Decimal
    method_totals: list[MethodTotal]
    movements: list[CashMovement]
    counts: list[tuple[CashCount, list[CashCountLine]]]


# ---------------------------------------------------------------------------
# Piezas del cuadre (recalculadas siempre, nunca confiadas al cliente)
# ---------------------------------------------------------------------------
def _counted_cents(lines: list[CountLine]) -> int:
    return cash_calc.count_total_cents(
        [(money_to_cents(line.denomination), line.quantity) for line in lines]
    )


async def _cash_flow_cents(
    session: AsyncSession, cash: CashSession
) -> tuple[int, int, int, int]:
    """(ventas efectivo, devoluciones efectivo, entradas, salidas) en céntimos."""

    cash_sales, cash_refunds = await repo.cash_flow_from_payments(session, cash.id)
    sums = await repo.movement_sums(session, cash.id)
    return (
        money_to_cents(cash_sales),
        money_to_cents(cash_refunds),
        money_to_cents(sums.get(CashMoveKind.cash_in, Decimal("0"))),
        money_to_cents(sums.get(CashMoveKind.cash_out, Decimal("0"))),
    )


async def _expected_cents(session: AsyncSession, cash: CashSession) -> int:
    cash_sales, cash_refunds, cash_in, cash_out = await _cash_flow_cents(session, cash)
    return cash_calc.expected_cash_cents(
        opening_cents=money_to_cents(cash.opening_amount),
        cash_sales_cents=cash_sales,
        cash_refunds_cents=cash_refunds,
        cash_in_cents=cash_in,
        cash_out_cents=cash_out,
    )


async def _require_open(session: AsyncSession, cash_session_id: UUID) -> CashSession:
    """Sesión viva y bloqueada: 404 si no existe, 409 si ya está cerrada."""

    cash = await repo.get_cash_session_for_update(session, cash_session_id)
    if cash is None:
        raise _not_found("Sesión de caja no encontrada")
    if cash.closed_at is not None:
        raise _conflict("La sesión de caja está cerrada")
    return cash


async def get_session(session: AsyncSession, cash_session_id: UUID) -> CashSession:
    """La sesión tal cual (consulta; sin bloqueo)."""

    cash = await repo.get_cash_session(session, cash_session_id)
    if cash is None:
        raise _not_found("Sesión de caja no encontrada")
    return cash


# ---------------------------------------------------------------------------
# Apertura
# ---------------------------------------------------------------------------
async def open_session(
    session: AsyncSession,
    principal: Principal,
    *,
    terminal_id: UUID,
    opening_amount: Decimal,
    idempotency: Idempotency | None = None,
    snapshot: Snapshot | None = None,
) -> CashSession | Replay:
    """Apertura con fondo inicial; una sola sesión abierta por terminal.

    Con ``idempotency`` (fase 14): el reintento de una apertura ya hecha
    devuelve la respuesta congelada en vez de un 409.
    """

    if idempotency is not None:
        replay = await idem_lookup(session, idempotency)
        if replay is not None:
            return replay

    terminal = await get_terminal(session, terminal_id)
    if terminal is None:
        raise _not_found("Terminal no encontrado")
    if not terminal.active:
        raise _conflict("El terminal no está activo")
    if await repo.find_open_by_terminal(session, terminal_id):
        raise _conflict("El terminal ya tiene una sesión de caja abierta")

    cash = CashSession(
        terminal_id=terminal_id,
        opened_by=principal.user_id,
        opening_amount=opening_amount,
    )
    session.add(cash)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Carrera de aperturas: el índice parcial es la última palabra (salvo
        # que el choque sea de idempotencia: el ganador congeló la respuesta).
        await session.rollback()
        if idempotency is not None:
            replay = await idem_lookup(session, idempotency)
            if replay is not None:
                return replay
        raise _conflict("El terminal ya tiene una sesión de caja abierta") from exc

    await auth_repo.record_audit(
        session,
        action="cash.session_opened",
        entity="cash_session",
        user_id=principal.user_id,
        entity_id=cash.id,
        after_data={
            "terminal_id": str(terminal_id),
            "opening_amount": format(opening_amount, "f"),
        },
    )
    await events_service.record(
        session,
        topic="cash",
        type="cash.opened",
        payload={"cash_session_id": str(cash.id), "terminal_id": str(terminal_id)},
        actor_user_id=principal.user_id,
    )
    try:
        if idempotency is not None:
            await idem_store(session, idempotency, status=201, body=snapshot(cash))
        await session.commit()
    except IntegrityError as exc:
        if idempotency is None:
            raise
        return await idem_replay(
            session,
            idempotency,
            _conflict("El terminal ya tiene una sesión de caja abierta"),
        )
    return cash


# ---------------------------------------------------------------------------
# Movimientos manuales (entradas/salidas de efectivo)
# ---------------------------------------------------------------------------
async def add_movement(
    session: AsyncSession,
    principal: Principal,
    cash_session_id: UUID,
    *,
    kind: CashMoveKind,
    amount: Decimal,
    reason: str,
    idempotency: Idempotency | None = None,
    snapshot: Snapshot | None = None,
) -> CashMovement | Replay:
    """Entrada o salida de efectivo con motivo obligatorio (§5.3).

    Con ``idempotency`` (fase 14): el replay gana incluso si la sesión ya se
    cerró desde entonces — el movimiento SÍ se creó en su día.
    """

    if idempotency is not None:
        replay = await idem_lookup(session, idempotency)
        if replay is not None:
            return replay

    cash = await _require_open(session, cash_session_id)

    movement = CashMovement(
        cash_session_id=cash.id,
        kind=kind,
        amount=amount,
        reason=reason,
        user_id=principal.user_id,
    )
    session.add(movement)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="cash.movement_created",
        entity="cash_movement",
        user_id=principal.user_id,
        entity_id=movement.id,
        after_data={
            "cash_session_id": str(cash.id),
            "kind": kind.value,
            "amount": format(amount, "f"),
            "reason": reason,
        },
    )
    await events_service.record(
        session,
        topic="cash",
        type="cash.movement_created",
        payload={
            "cash_session_id": str(cash.id),
            "movement_id": str(movement.id),
            "kind": kind.value,
            "amount": format(amount, "f"),
        },
        actor_user_id=principal.user_id,
    )
    try:
        if idempotency is not None:
            await idem_store(session, idempotency, status=201, body=snapshot(movement))
        await session.commit()
    except IntegrityError as exc:
        if idempotency is None:
            raise
        return await idem_replay(
            session,
            idempotency,
            _conflict("El movimiento choca con otra operación concurrente"),
        )
    return movement


# ---------------------------------------------------------------------------
# Arqueos (parciales y el del cierre)
# ---------------------------------------------------------------------------
async def _record_count(
    session: AsyncSession,
    principal: Principal,
    cash: CashSession,
    lines: list[CountLine],
    action: str,
) -> tuple[CashCount, list[CashCountLine], Decimal, Decimal, Decimal]:
    """Persiste el recuento por denominaciones y calcula el cuadre del momento."""

    counted = cents_to_decimal(_counted_cents(lines))
    expected = cents_to_decimal(await _expected_cents(session, cash))
    difference = cents_to_decimal(
        cash_calc.difference_cents(money_to_cents(counted), money_to_cents(expected))
    )

    count = CashCount(
        cash_session_id=cash.id, counted_by=principal.user_id, counted_amount=counted
    )
    session.add(count)
    await session.flush()  # asigna count.id para las líneas
    rows = [
        CashCountLine(
            cash_count_id=count.id,
            denomination=line.denomination,
            quantity=line.quantity,
        )
        for line in lines
    ]
    session.add_all(rows)
    await auth_repo.record_audit(
        session,
        action=action,
        entity="cash_count",
        user_id=principal.user_id,
        entity_id=count.id,
        after_data={
            "cash_session_id": str(cash.id),
            "counted_amount": format(counted, "f"),
            "expected_amount": format(expected, "f"),
            "difference": format(difference, "f"),
        },
    )
    return count, rows, counted, expected, difference


async def add_count(
    session: AsyncSession,
    principal: Principal,
    cash_session_id: UUID,
    *,
    lines: list[CountLine],
) -> CountResult:
    """Arqueo parcial (listado X puntual): no cierra la sesión (§5.4)."""

    cash = await _require_open(session, cash_session_id)
    count, rows, counted, expected, difference = await _record_count(
        session, principal, cash, lines, action="cash.count_recorded"
    )
    await events_service.record(
        session,
        topic="cash",
        type="cash.count_recorded",
        payload={
            "cash_session_id": str(cash.id),
            "count_id": str(count.id),
            "counted": format(counted, "f"),
            "expected": format(expected, "f"),
            "difference": format(difference, "f"),
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return CountResult(
        count=count, lines=rows, counted=counted, expected=expected, difference=difference
    )


# ---------------------------------------------------------------------------
# Cierre (Z)
# ---------------------------------------------------------------------------
async def close_session(
    session: AsyncSession,
    principal: Principal,
    cash_session_id: UUID,
    *,
    lines: list[CountLine],
) -> CloseCashResult:
    """Cierre con arqueo: congela esperado/contado/diferencia en la sesión."""

    cash = await _require_open(session, cash_session_id)
    count, _rows, counted, expected, difference = await _record_count(
        session, principal, cash, lines, action="cash.count_recorded"
    )

    closed_at = utcnow()
    cash.expected_amount = expected
    cash.counted_amount = counted
    cash.difference = difference
    cash.closed_at = closed_at
    cash.closed_by = principal.user_id

    await auth_repo.record_audit(
        session,
        action="cash.session_closed",
        entity="cash_session",
        user_id=principal.user_id,
        entity_id=cash.id,
        after_data={
            "expected_amount": format(expected, "f"),
            "counted_amount": format(counted, "f"),
            "difference": format(difference, "f"),
        },
    )
    await events_service.record(
        session,
        topic="cash",
        type="cash.session_closed",
        payload={
            "cash_session_id": str(cash.id),
            "terminal_id": str(cash.terminal_id),
            "expected": format(expected, "f"),
            "counted": format(counted, "f"),
            "difference": format(difference, "f"),
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return CloseCashResult(
        session=cash, counted=counted, expected=expected, difference=difference, count=count
    )


# ---------------------------------------------------------------------------
# Informes (X sobre sesión abierta, Z sobre cerrada) y listado histórico
# ---------------------------------------------------------------------------
async def session_report(session: AsyncSession, cash_session_id: UUID) -> CashReport:
    """Listado de la sesión: movimientos, flujo de efectivo y totales por forma."""

    cash = await repo.get_cash_session(session, cash_session_id)
    if cash is None:
        raise _not_found("Sesión de caja no encontrada")
    return await build_report(session, cash)


async def build_report(session: AsyncSession, cash: CashSession) -> CashReport:
    cash_sales, cash_refunds, cash_in, cash_out = await _cash_flow_cents(session, cash)
    expected = cash_calc.expected_cash_cents(
        opening_cents=money_to_cents(cash.opening_amount),
        cash_sales_cents=cash_sales,
        cash_refunds_cents=cash_refunds,
        cash_in_cents=cash_in,
        cash_out_cents=cash_out,
    )
    method_totals = [
        MethodTotal(
            code=code,
            kind=kind.value,
            sales_total=sales_total,
            sales_count=sales_count,
            refunds_total=refunds_total,
            refunds_count=refunds_count,
        )
        for code, kind, sales_total, sales_count, refunds_total, refunds_count in (
            await repo.method_totals(session, cash.id)
        )
    ]
    movements = await repo.list_movements(session, cash.id)
    counts = [
        (count, await repo.list_count_lines(session, count.id))
        for count in await repo.list_counts(session, cash.id)
    ]
    return CashReport(
        session=cash,
        cash_in=cents_to_decimal(cash_in),
        cash_out=cents_to_decimal(cash_out),
        cash_sales=cents_to_decimal(cash_sales),
        cash_refunds=cents_to_decimal(cash_refunds),
        expected_cash=cents_to_decimal(expected),
        method_totals=method_totals,
        movements=movements,
        counts=counts,
    )


async def current_session(session: AsyncSession, terminal_id: UUID) -> CashSession:
    """La sesión abierta del terminal (para saber si la caja está abierta)."""

    cash = await repo.find_open_by_terminal(session, terminal_id)
    if cash is None:
        raise _not_found("El terminal no tiene una sesión de caja abierta")
    return cash


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
    """Histórico de sesiones (cuadres Z) con filtros."""

    return await repo.list_sessions(
        session,
        terminal_id=terminal_id,
        open_only=open_only,
        closed_only=closed_only,
        opened_from=opened_from,
        opened_to=opened_to,
        limit=limit,
        offset=offset,
    )

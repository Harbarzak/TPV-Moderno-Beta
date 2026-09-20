"""Servicio de sala (fase 30 · Modo restaurante): zonas, mesas, plano 2D,
sesión de mesa, traspasos, juntadas y división de cuenta.

Reglas (ARCHITECTURE.md §4.2, docs/design-system.md §3.2/§5.4):
- El estado de la mesa NO se almacena: se deriva de su comanda abierta
  (``uq_orders_open_per_table`` garantiza una sola por mesa). Libre = sin
  draft; abierta = draft; «cuenta pedida» = draft con ``bill_requested_at``.
- La sesión de mesa ES el borrador de ventas (``Order`` en ``draft``, tipo
  ``restaurant``): abrirla reutiliza ``services.sales.create_order`` con su
  idempotencia, auditoría y eventos; las líneas se gestionan por ``/sales``.
- Toda transición de mesa bloquea filas (``SELECT … FOR UPDATE``, mesas por
  orden de id para evitar deadlocks) y avisa en el tema WS ``restaurant``;
  los cambios visibles también en el ticket repiten aviso en ``sales``.
- Traspasar mueve el pedido entero a otra mesa libre; juntar mueve las
  líneas del pedido origen al destino y anula el origen con motivo; dividir
  crea un pedido nuevo en la mesa destino moviendo líneas enteras o
  parciales (mili-unidades, recálculo con el motor puro ``domain.sales``).

Nunca importa de ``api``: los controladores llaman a estas funciones.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.db.enums import OrderStatus, OrderType, SaleEventType
from app.db.models.sales import DiningTable, Order, OrderLine, Zone
from app.domain import sales as calc
from app.repos import auth as auth_repo
from app.repos import restaurant as repo
from app.repos import sales as sales_repo
from app.services import events as events_service
from app.services import sales as sales_service
from app.services.auth import Principal
from app.services.idempotency import Idempotency, Replay, Snapshot

logger = get_logger("tpv.restaurant")


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


def _ensure_draft(order: Order) -> None:
    """Solo el borrador participa en la sala (patrón de ``sales._ensure_draft``)."""

    if order.status == OrderStatus.paid:
        raise _conflict("La venta ya está cobrada")
    if order.status == OrderStatus.voided:
        raise _conflict("La venta está anulada")


# ---------------------------------------------------------------------------
# Plano: estados del semáforo (§3.2) derivados de la comanda abierta
# ---------------------------------------------------------------------------
FREE = "free"      # verde: sin comanda asociada
OPEN = "open"      # ámbar: draft en servidor (+ tiempo transcurrido)
BILL = "bill"      # azul: cuenta pedida (+ importe abierto)


@dataclass(frozen=True, slots=True)
class TableState:
    """Una mesa del plano con su estado derivado (nunca almacenado)."""

    table: DiningTable
    zone: Zone
    order: Order | None
    waiter: str | None      # nombre del camarero que abrió la sesión
    open_total: Decimal | None
    status: str


async def floor(
    session: AsyncSession, *, include_inactive: bool = False
) -> tuple[list[Zone], list[TableState]]:
    """El plano completo: zonas y mesas con estado, en una consulta."""

    zones = await repo.list_zones(session, include_inactive=include_inactive)
    states: list[TableState] = []
    for table, zone, order, waiter, open_total in await repo.floor_rows(
        session, include_inactive=include_inactive
    ):
        if order is None:
            status = FREE
        elif order.bill_requested_at is not None:
            status = BILL
        else:
            status = OPEN
        states.append(
            TableState(
                table=table,
                zone=zone,
                order=order,
                waiter=waiter,
                open_total=open_total,
                status=status,
            )
        )
    return zones, states


# ---------------------------------------------------------------------------
# Zonas y mesas (configuración de sala: colocar el plano es una operación
# diaria del gestor de sala, no administración del sistema)
# ---------------------------------------------------------------------------
async def zones(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[Zone]:
    return await repo.list_zones(session, include_inactive=include_inactive)


async def get_zone(session: AsyncSession, zone_id: UUID) -> Zone | None:
    return await repo.get_zone(session, zone_id)


async def create_zone(
    session: AsyncSession, principal: Principal, *, name: str, sort_order: int
) -> Zone:
    zone = Zone(name=name, sort_order=sort_order)
    session.add(zone)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="restaurant.zone_created",
        entity="zone",
        user_id=principal.user_id,
        entity_id=zone.id,
        after_data={"name": zone.name, "sort_order": zone.sort_order},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.floor_changed",
        payload={"zone_id": str(zone.id)}, actor_user_id=principal.user_id,
    )
    await session.commit()
    logger.info("restaurant.zone_created", zone_id=str(zone.id))
    return zone


async def update_zone(
    session: AsyncSession, principal: Principal, zone_id: UUID, *, fields: dict
) -> Zone:
    zone = await repo.get_zone(session, zone_id)
    if zone is None:
        raise _not_found("Zona no encontrada")

    if "name" in fields:
        zone.name = fields["name"]
    if "sort_order" in fields:
        zone.sort_order = fields["sort_order"]
    if fields.get("active") is False and zone.active:
        if await repo.count_active_tables(session, zone.id):
            raise _conflict("La zona tiene mesas activas; muévelas o desactívalas antes")
    if "active" in fields:
        zone.active = fields["active"]

    await auth_repo.record_audit(
        session,
        action="restaurant.zone_updated",
        entity="zone",
        user_id=principal.user_id,
        entity_id=zone.id,
        after_data={"fields": sorted(fields), "name": zone.name, "active": zone.active},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.floor_changed",
        payload={"zone_id": str(zone.id)}, actor_user_id=principal.user_id,
    )
    await session.commit()
    return zone


async def create_table(
    session: AsyncSession,
    principal: Principal,
    *,
    zone_id: UUID,
    name: str,
    seats: int,
    sort_order: int = 0,
    pos_x: Decimal | None = None,
    pos_y: Decimal | None = None,
) -> DiningTable:
    zone = await repo.get_zone(session, zone_id)
    if zone is None:
        raise _not_found("Zona no encontrada")
    if not zone.active:
        raise _conflict("La zona no está activa")
    # UNIQUE (zone_id, name): 409 propio antes del IntegrityError de la BD
    # (mismo criterio que ``update_table``).
    dup = await session.scalar(
        select(DiningTable.id).where(
            DiningTable.zone_id == zone_id, DiningTable.name == name
        )
    )
    if dup is not None:
        raise _conflict("Ya existe una mesa con ese nombre en la zona")

    table = DiningTable(
        zone_id=zone_id, name=name, seats=seats, sort_order=sort_order,
        pos_x=pos_x, pos_y=pos_y,
    )
    session.add(table)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="restaurant.table_created",
        entity="dining_table",
        user_id=principal.user_id,
        entity_id=table.id,
        after_data={"zone_id": str(zone_id), "name": name, "seats": seats},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.floor_changed",
        payload={"zone_id": str(zone_id), "table_id": str(table.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return table


async def update_table(
    session: AsyncSession, principal: Principal, table_id: UUID, *, fields: dict
) -> DiningTable:
    table = await repo.get_table_for_update(session, table_id)
    if table is None:
        raise _not_found("Mesa no encontrada")

    if "zone_id" in fields and fields["zone_id"] != table.zone_id:
        zone = await repo.get_zone(session, fields["zone_id"])
        if zone is None:
            raise _not_found("Zona no encontrada")
        if not zone.active:
            raise _conflict("La zona de destino no está activa")
        table.zone_id = fields["zone_id"]
    if "name" in fields:
        table.name = fields["name"]
    if "seats" in fields:
        table.seats = fields["seats"]
    if "sort_order" in fields:
        table.sort_order = fields["sort_order"]
    if "pos_x" in fields:
        table.pos_x = fields["pos_x"]
    if "pos_y" in fields:
        table.pos_y = fields["pos_y"]
    if fields.get("active") is False and table.active:
        if await sales_repo.find_draft_by_table(session, table.id):
            raise _conflict("La mesa tiene una comanda abierta; traspásala o ciérrala antes")
    if "active" in fields:
        table.active = fields["active"]

    # UNIQUE (zone_id, name): 409 propio antes del IntegrityError de la BD.
    dup = await session.scalar(
        select(DiningTable.id).where(
            DiningTable.zone_id == table.zone_id,
            DiningTable.name == table.name,
            DiningTable.id != table.id,
        )
    )
    if dup is not None:
        raise _conflict("Ya existe una mesa con ese nombre en la zona")

    await auth_repo.record_audit(
        session,
        action="restaurant.table_updated",
        entity="dining_table",
        user_id=principal.user_id,
        entity_id=table.id,
        after_data={"fields": sorted(fields), "zone_id": str(table.zone_id)},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.floor_changed",
        payload={"zone_id": str(table.zone_id), "table_id": str(table.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return table


# ---------------------------------------------------------------------------
# Sesión de mesa (el borrador de ventas)
# ---------------------------------------------------------------------------
async def get_open_draft_for_update(session: AsyncSession, table_id: UUID) -> Order | None:
    """Comanda abierta de la mesa, bloqueada (o ``None`` si está libre)."""

    return await session.scalar(
        select(Order)
        .where(
            Order.dining_table_id == table_id,
            Order.status == OrderStatus.draft,
        )
        .with_for_update()
    )


async def open_table(
    session: AsyncSession,
    principal: Principal,
    table_id: UUID,
    *,
    terminal_id: UUID,
    guest_count: int | None,
    note: str | None,
    idempotency: Idempotency | None,
    snapshot: Snapshot | None,
) -> Order | Replay:
    """Abrir sesión de mesa = crear el borrador (tipo ``restaurant``).

    Reutiliza ``sales.create_order``: valida mesa activa y libre, guarda la
    clave de idempotencia en la misma transacción y emite ``sales.created``.
    """

    return await sales_service.create_order(
        session,
        principal,
        terminal_id=terminal_id,
        order_type=OrderType.restaurant,
        dining_table_id=table_id,
        guest_count=guest_count,
        note=note,
        idempotency=idempotency,
        snapshot=snapshot,
    )


async def update_session(
    session: AsyncSession, principal: Principal, order_id: UUID, *, fields: dict
) -> Order:
    """Notas y comensales de la sesión (solo en borrador)."""

    order = await sales_repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    if "guest_count" in fields:
        order.guest_count = fields["guest_count"]
    if "note" in fields:
        order.note = fields["note"]

    await auth_repo.record_audit(
        session,
        action="restaurant.session_updated",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"fields": sorted(fields), "note": order.note,
                    "guest_count": order.guest_count},
    )
    # Visible en el ticket (tema sales) y en el mapa (tema restaurant).
    for topic, type_ in (("sales", "sales.updated"),
                         ("restaurant", "restaurant.session_updated")):
        await events_service.record(
            session, topic=topic, type=type_,
            payload={
                "order_id": str(order.id),
                "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            },
            actor_user_id=principal.user_id,
        )
    await session.commit()
    return order


async def request_bill(session: AsyncSession, principal: Principal, order_id: UUID) -> Order:
    """«Cuenta pedida»: estado del semáforo hasta que se cobra la mesa."""

    order = await sales_repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)
    if order.bill_requested_at is not None:
        raise _conflict("La cuenta ya está pedida")

    order.bill_requested_at = sales_repo.utcnow()
    await auth_repo.record_audit(
        session,
        action="restaurant.bill_requested",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"table_id": str(order.dining_table_id)
                    if order.dining_table_id else None},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.bill_requested",
        payload={
            "order_id": str(order.id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return order


async def cancel_bill(session: AsyncSession, principal: Principal, order_id: UUID) -> Order:
    order = await sales_repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    order.bill_requested_at = None
    await auth_repo.record_audit(
        session,
        action="restaurant.bill_cleared",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"table_id": str(order.dining_table_id)
                    if order.dining_table_id else None},
    )
    await events_service.record(
        session, topic="restaurant", type="restaurant.bill_cleared",
        payload={
            "order_id": str(order.id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return order


# ---------------------------------------------------------------------------
# Traspasar, juntar y dividir
# ---------------------------------------------------------------------------
async def _lock_two_tables(
    session: AsyncSession, source_id: UUID, target_id: UUID
) -> tuple[DiningTable, DiningTable]:
    """Bloquea ambas mesas en orden de id (orden canónico: sin deadlocks)."""

    first_id, second_id = sorted((source_id, target_id))
    first = await repo.get_table_for_update(session, first_id)
    second = await repo.get_table_for_update(session, second_id)
    if first is None or second is None:
        raise _not_found("Mesa no encontrada")
    source = first if first.id == source_id else second
    target = second if second.id == target_id else first
    return source, target


def _validate_move_target(source: DiningTable, target: DiningTable) -> None:
    if target.id == source.id:
        raise _validation("La mesa de destino es la misma que la de origen")
    if not target.active:
        raise _conflict("La mesa de destino no está activa")


async def _void_draft_in_place(
    session: AsyncSession, principal: Principal, order: Order, *, reason: str
) -> None:
    """Anula un borrador sin líneas (juntada/división): mismo rastro que
    ``sales.void_order`` — campos de anulación, evento fiscal yWS del tema
    ``sales`` los emite la operación llamante si procede."""

    order.status = OrderStatus.voided
    order.voided_at = sales_repo.utcnow()
    order.voided_by = principal.user_id
    order.void_reason = reason
    # El evento fiscal (sale_events) lo consume el futuro FiscalAdapter; un
    # draft nunca tuvo totales que conservar.
    await sales_repo.record_sale_event(
        session,
        order_id=order.id,
        event_type=SaleEventType.sale_voided,
        payload={
            "order_id": str(order.id),
            "previous_status": OrderStatus.draft.value,
            "reason": reason,
            "voided_by": str(principal.user_id),
            "totals": None,
        },
    )


async def _move_events(
    session: AsyncSession,
    principal: Principal,
    *,
    type_: str,
    order_id: UUID,
    from_table_id: UUID | None,
    to_table_id: UUID | None,
) -> None:
    """Aviso doble: ``sales.updated`` (quien tiene el ticket abierto) y el
    tipo propio en ``restaurant`` (quien mira el plano)."""

    for topic, type in (("sales", "sales.updated"), ("restaurant", type_)):
        await events_service.record(
            session,
            topic=topic,
            type=type,
            payload={
                "order_id": str(order_id),
                "table_id": str(to_table_id) if to_table_id else None,
                "from_table_id": str(from_table_id) if from_table_id else None,
                "to_table_id": str(to_table_id) if to_table_id else None,
            },
            actor_user_id=principal.user_id,
        )


async def transfer_table(
    session: AsyncSession, principal: Principal, table_id: UUID, *, target_table_id: UUID
) -> Order:
    """Traspasar la comanda de una mesa a otra libre (mismo pedido, otra mesa)."""

    source, target = await _lock_two_tables(session, table_id, target_table_id)
    _validate_move_target(source, target)

    order = await get_open_draft_for_update(session, source.id)
    if order is None:
        raise _not_found("La mesa no tiene una comanda abierta")
    if await get_open_draft_for_update(session, target.id) is not None:
        raise _conflict("La mesa de destino ya tiene una comanda abierta")

    from_table_id = order.dining_table_id
    order.dining_table_id = target.id
    await auth_repo.record_audit(
        session,
        action="restaurant.table_transferred",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"from_table_id": str(from_table_id), "to_table_id": str(target.id)},
    )
    await _move_events(
        session, principal,
        type_="restaurant.table_transferred",
        order_id=order.id, from_table_id=from_table_id, to_table_id=target.id,
    )
    await session.commit()
    logger.info("restaurant.table_transferred", order_id=str(order.id))
    return order


async def merge_tables(
    session: AsyncSession, principal: Principal, table_id: UUID, *, target_table_id: UUID
) -> tuple[Order, list[OrderLine]]:
    """Juntar mesas: las líneas del origen pasan al destino; el origen se
    anula con motivo (traza completa; su mesa queda libre)."""

    source, target = await _lock_two_tables(session, table_id, target_table_id)
    _validate_move_target(source, target)

    source_order = await get_open_draft_for_update(session, source.id)
    if source_order is None:
        raise _not_found("La mesa no tiene una comanda abierta")
    target_order = await get_open_draft_for_update(session, target.id)
    if target_order is None:
        raise _not_found("La mesa de destino no tiene una comanda abierta")
    if target_order.id == source_order.id:
        raise _validation("La mesa de destino es la misma que la de origen")

    lines = await sales_repo.list_lines(session, source_order.id)
    if not lines:
        raise _conflict("La comanda de origen no tiene líneas; traspásala o anúlala")

    next_sort = await sales_repo.max_line_sort_order(session, target_order.id)
    for line in lines:
        next_sort += 1
        line.order_id = target_order.id
        line.sort_order = next_sort
    # Comensales: si el destino no los declaró, hereda los del origen.
    if target_order.guest_count is None:
        target_order.guest_count = source_order.guest_count

    await _void_draft_in_place(
        session, principal, source_order,
        reason=f"Junta de mesas hacia {target.name}",
    )
    await auth_repo.record_audit(
        session,
        action="restaurant.orders_merged",
        entity="order",
        user_id=principal.user_id,
        entity_id=target_order.id,
        after_data={
            "source_order_id": str(source_order.id),
            "source_table_id": str(source.id),
            "target_table_id": str(target.id),
            "moved_lines": len(lines),
        },
    )
    await _move_events(
        session, principal,
        type_="restaurant.orders_merged",
        order_id=target_order.id, from_table_id=source.id, to_table_id=target.id,
    )
    await session.commit()
    merged = await sales_repo.list_lines(session, target_order.id)
    return target_order, merged


@dataclass(frozen=True, slots=True)
class SplitMove:
    """Cantidad a mover de una línea (entera o parcial)."""

    line_id: UUID
    quantity: Decimal


async def split_table(
    session: AsyncSession,
    principal: Principal,
    table_id: UUID,
    *,
    target_table_id: UUID,
    moves: list[SplitMove],
) -> tuple[Order, list[OrderLine]]:
    """División de cuenta: crea un pedido nuevo en la mesa destino y mueve
    líneas enteras o parciales (el motor puro recalcula cada importe)."""

    if not moves:
        raise _validation("La división requiere al menos una línea")
    if len({move.line_id for move in moves}) != len(moves):
        raise _validation("Cada línea solo puede aparecer una vez en la división")

    source, target = await _lock_two_tables(session, table_id, target_table_id)
    _validate_move_target(source, target)

    source_order = await get_open_draft_for_update(session, source.id)
    if source_order is None:
        raise _not_found("La mesa no tiene una comanda abierta")
    if await get_open_draft_for_update(session, target.id) is not None:
        raise _conflict("La mesa de destino ya tiene una comanda abierta")

    lines = {line.id: line for line in await sales_repo.list_lines(session, source_order.id)}
    total_source = sum(calc.qty_to_milli(line.quantity) for line in lines.values())
    move_total = sum(calc.qty_to_milli(move.quantity) for move in moves)
    if move_total <= 0:
        raise _validation("Las cantidades a separar deben ser positivas")
    if move_total >= total_source:
        raise _conflict(
            "No puedes separar la comanda completa; traspasa la mesa o anula el pedido"
        )

    # Pedido nuevo en la mesa destino: hereda terminal y tipo; el camarero es
    # quien separa (la cuenta dividida suele cobrarse por separado).
    new_order = Order(
        terminal_id=source_order.terminal_id,
        user_id=principal.user_id,
        status=OrderStatus.draft,
        order_type=OrderType.restaurant,
        dining_table_id=target.id,
    )
    session.add(new_order)
    await session.flush()

    next_sort = 0
    for move in moves:
        line = lines.get(move.line_id)
        if line is None:
            raise _not_found("Línea no encontrada en la comanda de origen")
        available = calc.qty_to_milli(line.quantity)
        moving = calc.qty_to_milli(move.quantity)
        if moving <= 0 or moving > available:
            raise _validation(
                f"Cantidad inválida para «{line.name}»: 0 < cantidad ≤ {format(line.quantity, 'f')}"
            )
        next_sort += 1
        if moving == available:                     # línea entera: se reasigna
            line.order_id = new_order.id
            line.sort_order = next_sort
            continue
        # Parcial: la línea origen se queda con el resto (recalculado); la
        # nueva conserva el snapshot (precio/tipo/descuento/notas).
        remaining_milli = available - moving
        remaining_amounts = calc.line_amounts(
            price_cents=calc.money_to_cents(line.unit_price),
            qty_milli=remaining_milli,
            discount_bp=calc.pct_to_bp(line.discount_pct),
            tax_rate_bp=calc.pct_to_bp(line.tax_rate),
        )
        line.quantity = calc.milli_to_qty(remaining_milli)
        line.line_base = calc.cents_to_decimal(remaining_amounts.base)
        line.line_total = calc.cents_to_decimal(remaining_amounts.total)

        moved_amounts = calc.line_amounts(
            price_cents=calc.money_to_cents(line.unit_price),
            qty_milli=moving,
            discount_bp=calc.pct_to_bp(line.discount_pct),
            tax_rate_bp=calc.pct_to_bp(line.tax_rate),
        )
        session.add(
            OrderLine(
                order_id=new_order.id,
                product_id=line.product_id,
                name=line.name,
                unit_price=line.unit_price,
                tax_rate=line.tax_rate,
                quantity=calc.milli_to_qty(moving),
                discount_pct=line.discount_pct,
                line_base=calc.cents_to_decimal(moved_amounts.base),
                line_total=calc.cents_to_decimal(moved_amounts.total),
                notes=line.notes,
                sort_order=next_sort,
            )
        )

    # El guardo de arriba impide separar TODO; si aun así el origen queda sin
    # líneas (cantidades parciales que cubren el total), se anula con motivo.
    remaining = await sales_repo.list_lines(session, source_order.id)
    if not remaining:
        await _void_draft_in_place(
            session, principal, source_order,
            reason=f"División de cuenta hacia {target.name}",
        )

    await auth_repo.record_audit(
        session,
        action="restaurant.order_split",
        entity="order",
        user_id=principal.user_id,
        entity_id=new_order.id,
        after_data={
            "source_order_id": str(source_order.id),
            "source_table_id": str(source.id),
            "target_table_id": str(target.id),
            "moves": [
                {"line_id": str(move.line_id), "quantity": format(move.quantity, "f")}
                for move in moves
            ],
        },
    )
    await _move_events(
        session, principal,
        type_="restaurant.order_split",
        order_id=new_order.id, from_table_id=source.id, to_table_id=target.id,
    )
    await session.commit()
    moved_lines = await sales_repo.list_lines(session, new_order.id)
    return new_order, moved_lines

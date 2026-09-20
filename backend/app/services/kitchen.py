"""Servicio KDS (fase 32 · Kitchen Display System).

Dos caras:

- **Productor** (``on_sale_*``): lo llama ``services.sales`` DENTRO de su
  transacción cuando una línea de producto de cocina se añade, rectifica,
  retira o la venta se anula. La comanda nace y muere con la venta: si el
  cobro revierte, el trabajo de impresión nunca existió. Solo los productos
  con ``kitchen=true`` generan comanda; las líneas de texto libre, no.
- **Tablero** (resto): lo que consume ``api.kds`` — leer el tablero, avanzar
  líneas, «todo listo» masivo, prioridad, estaciones y reimprimir KOT.

Reglas:
- El estado de la comanda (cabecera) se DERIVA de sus líneas
  (:func:`app.domain.kitchen.derive_ticket_status`); nunca se marca a mano.
- Cada cambio registra un evento ``kds.*`` (topic ``kds``, permiso
  ``kds.operate``) antes del commit: los tableros vivos se enteran por
  WebSocket y releen.
- La impresión KOT usa la cola de impresión existente (kind ``kitchen``):
  sin impresora de cocina configurada NO falla la venta — la comanda sigue en
  pantalla y se puede reimprimir. KDT por línea nueva (bombas/comandas de
  producto) + reimpresión completa por comanda.
- Las pulsaciones son idempotentes en la práctica: marcar dos veces LISTO una
  línea ya lista es un no-op exitoso (evita 409 por doble toque).
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.db.enums import KitchenStatus, PrinterKind, PrintJobKind
from app.db.models.catalog import Product
from app.db.models.printing import PrintJob
from app.db.models.restaurant import KitchenOrder, KitchenOrderLine, KitchenStation
from app.db.models.sales import DiningTable, Order, OrderLine
from app.domain import kitchen as domain
from app.repos import auth as auth_repo
from app.repos import kitchen as repo
from app.repos import printing as printing_repo
from app.services import events as events_service
from app.services import printing as printing_service
from app.services.auth import Principal

logger = get_logger("tpv.kitchen")

_TERMINAL = (KitchenStatus.served, KitchenStatus.cancelled)


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Cabecera derivada de las líneas (común a todas las rutas de cambio)
# ---------------------------------------------------------------------------
async def _sync_ticket_header(
    session: AsyncSession, ticket: KitchenOrder, *, actor_user_id: UUID | None
) -> None:
    lines = await repo.list_lines_of_ticket(session, ticket.id)
    target = domain.derive_ticket_status([line.status for line in lines])
    if ticket.status == target:
        return
    if not domain.can_transition_ticket(ticket.status, target):
        raise _conflict(
            f"La comanda en «{ticket.status.value}» no puede pasar a «{target.value}»"
        )
    ticket.status = target
    if target is KitchenStatus.ready:
        ticket.ready_at = ticket.ready_at or _now()
    elif target is KitchenStatus.preparing:
        ticket.ready_at = None  # paso atrás: se vuelve a marcar al llegar a LISTO
    elif target is KitchenStatus.served:
        ticket.ready_at = ticket.ready_at or _now()
        ticket.served_at = _now()
    await events_service.record(
        session,
        topic="kds",
        type="kds.ticket_status",
        payload={"kitchen_order_id": str(ticket.id), "status": target.value},
        actor_user_id=actor_user_id,
    )


# ---------------------------------------------------------------------------
# Productor: hooks de services.sales (misma transacción, sin commit aquí)
# ---------------------------------------------------------------------------
def _kot_payload(
    ticket: KitchenOrder,
    table_name: str | None,
    lines: list[tuple[KitchenOrderLine, str | None]],
    *, kind: str,
) -> dict:
    """Payload congelado de KOT (Kitchen Order Ticket). Igual que los tickets
    de venta: datos planos, sin referencias a ORM ni a futuro."""
    return {
        "kind": kind,
        "kitchen_order_id": str(ticket.id),
        "order_id": str(ticket.order_id),
        "priority": int(ticket.priority),
        "table": table_name,
        "lines": [
            {
                "name": line.name,
                "quantity": format(line.quantity, "f"),
                "notes": line.notes,
                "station": station_name,
            }
            for line, station_name in lines
        ],
    }


async def _table_name_for(session: AsyncSession, order_id: UUID) -> str | None:
    row = await session.execute(
        select(DiningTable.name)
        .join(Order, Order.dining_table_id == DiningTable.id)
        .where(Order.id == order_id)
    )
    return row.scalar_one_or_none()


async def on_sale_line_added(
    session: AsyncSession,
    *,
    order: Order,
    line: OrderLine,
    product: Product | None,
    actor_user_id: UUID | None = None,
) -> None:
    """Línea de producto de cocina añadida a un borrador: asegura la comanda,
    crea la línea de cocina (NUEVO) y encola su KOT."""
    if product is None or not product.kitchen:
        return
    ticket = await repo.get_kitchen_order_by_order(session, order.id)
    created = ticket is None
    if ticket is None:
        ticket = KitchenOrder(order_id=order.id)
        session.add(ticket)
        await session.flush()
    station_name = None
    if product.kitchen_station_id is not None:
        station = await repo.get_station(session, product.kitchen_station_id)
        station_name = station.name if station is not None else None
    kitchen_line = KitchenOrderLine(
        kitchen_order_id=ticket.id,
        order_line_id=line.id,
        name=line.name,
        quantity=line.quantity,
        notes=line.notes,
        status=KitchenStatus.pending,
        station_id=product.kitchen_station_id,
    )
    session.add(kitchen_line)
    await session.flush()

    if created:
        await events_service.record(
            session,
            topic="kds",
            type="kds.ticket_created",
            payload={"kitchen_order_id": str(ticket.id), "order_id": str(order.id)},
            actor_user_id=actor_user_id,
        )
    await events_service.record(
        session,
        topic="kds",
        type="kds.line_added",
        payload={"kitchen_order_id": str(ticket.id), "line_id": str(kitchen_line.id)},
        actor_user_id=actor_user_id,
    )
    # KOT por línea nueva; doc_key = línea de cocina (dedupe natural). Sin
    # impresora de cocina configurada devuelve None: la venta sigue.
    table_name = await _table_name_for(session, order.id)
    await printing_service.enqueue_document(
        session,
        kind=PrintJobKind.kitchen,
        payload=_kot_payload(ticket, table_name, [(kitchen_line, station_name)], kind="kot"),
        doc_key=str(kitchen_line.id),
    )


async def on_sale_line_updated(
    session: AsyncSession, *, line: OrderLine, actor_user_id: UUID | None = None
) -> None:
    """Rectificación en borrador (cantidad/nota): se sincroniza SOLO si la
    cocina aún no marcó la línea LISTO/SERVIDO — lo ya cocinado no se reescribe."""
    kitchen_line = await repo.get_kitchen_line_by_order_line(session, line.id)
    if kitchen_line is None or kitchen_line.status in (KitchenStatus.ready, *_TERMINAL):
        return
    changed = False
    if kitchen_line.quantity != line.quantity:
        kitchen_line.quantity = line.quantity
        changed = True
    if kitchen_line.notes != line.notes:
        kitchen_line.notes = line.notes
        changed = True
    if not changed:
        return
    await events_service.record(
        session,
        topic="kds",
        type="kds.line_updated",
        payload={"kitchen_order_id": str(kitchen_line.kitchen_order_id), "line_id": str(kitchen_line.id)},
        actor_user_id=actor_user_id,
    )


async def on_sale_line_removed(
    session: AsyncSession, *, line: OrderLine, actor_user_id: UUID | None = None
) -> None:
    """Línea retirada del borrador: su línea de comanda DESAPARECE con ella.

    La FK a ``order_lines`` no es cascade a propósito (fase 03): no puede
    quedar una fila de cocina apuntando a una línea de venta borrada, y la
    trazabilidad ya queda en ``event_log`` (``kds.line_cancelled``). Si no
    queda ninguna línea, la comanda entera se borra; si quedan, la cabecera
    se re-deriva — NUNCA hacia SERVIDO por vaciado."""
    kitchen_line = await repo.get_kitchen_line_by_order_line(session, line.id)
    if kitchen_line is None:
        return
    ticket = await repo.get_kitchen_order(session, kitchen_line.kitchen_order_id)
    await session.delete(kitchen_line)
    await events_service.record(
        session,
        topic="kds",
        type="kds.line_cancelled",
        payload={"kitchen_order_id": str(kitchen_line.kitchen_order_id), "line_id": str(kitchen_line.id)},
        actor_user_id=actor_user_id,
    )
    if ticket is None:
        return
    remaining = await repo.list_lines_of_ticket(session, ticket.id)
    if not remaining:  # no quedó nada: la comanda nunca existió para la cocina
        await session.delete(ticket)
        return
    await _sync_ticket_header(session, ticket, actor_user_id=actor_user_id)


async def on_sale_order_voided(
    session: AsyncSession, *, order: Order, actor_user_id: UUID | None = None
) -> None:
    """Venta anulada: comanda y líneas no terminales quedan CANCELADAS."""
    ticket = await repo.get_kitchen_order_by_order(session, order.id)
    if ticket is None or domain.is_terminal(ticket.status):
        return
    lines = await repo.list_lines_of_ticket(session, ticket.id)
    for kitchen_line in lines:
        if not domain.is_terminal(kitchen_line.status):
            kitchen_line.status = KitchenStatus.cancelled
    ticket.status = KitchenStatus.cancelled
    await events_service.record(
        session,
        topic="kds",
        type="kds.ticket_cancelled",
        payload={"kitchen_order_id": str(ticket.id), "order_id": str(order.id)},
        actor_user_id=actor_user_id,
    )


# ---------------------------------------------------------------------------
# Tablero: lectura
# ---------------------------------------------------------------------------
async def get_board(session: AsyncSession, *, served_limit: int = 10):
    return await repo.board(session, served_limit=served_limit)


async def list_stations(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[KitchenStation]:
    return await repo.list_stations(session, include_inactive=include_inactive)


# ---------------------------------------------------------------------------
# Tablero: líneas
# ---------------------------------------------------------------------------
async def set_line_status(
    session: AsyncSession,
    principal: Principal,
    kitchen_line_id: UUID,
    target: KitchenStatus,
):
    """Avance/retroceso de UNA línea + re-derivación de la cabecera.

    Devuelve la comanda actualizada (la tarjeta que la SPA repinta)."""
    kitchen_line = await repo.get_kitchen_line(session, kitchen_line_id)
    if kitchen_line is None:
        raise _not_found("Línea de comanda no encontrada")
    ticket = await repo.get_kitchen_order(session, kitchen_line.kitchen_order_id)
    if ticket is None:
        raise _not_found("Comanda no encontrada")
    if domain.is_terminal(ticket.status):
        raise _conflict("La comanda ya está cerrada (servida o cancelada)")
    if kitchen_line.status != target:
        if not domain.can_transition_line(kitchen_line.status, target):
            raise _conflict(
                f"Una línea «{kitchen_line.status.value}» no puede pasar a «{target.value}»"
            )
        kitchen_line.status = target
        await events_service.record(
            session,
            topic="kds",
            type="kds.line_status",
            payload={
                "kitchen_order_id": str(ticket.id),
                "line_id": str(kitchen_line.id),
                "status": target.value,
            },
            actor_user_id=principal.user_id,
        )
        await _sync_ticket_header(session, ticket, actor_user_id=principal.user_id)
    await session.commit()
    return await _board_ticket(session, ticket.id)


async def _board_ticket(session: AsyncSession, ticket_id: UUID):
    """La tarjeta repintada tras un cambio. ``served_limit`` holgado: si el
    cambio acaba de SERVIR la comanda, ya no está entre las abiertas."""
    tickets = await repo.board(session, served_limit=50)
    for ticket in tickets:
        if ticket.id == ticket_id:
            return ticket
    return None


# ---------------------------------------------------------------------------
# Tablero: comandas completas
# ---------------------------------------------------------------------------
async def set_ticket_status(
    session: AsyncSession, principal: Principal, ticket_id: UUID, target: KitchenStatus
):
    """«Todo listo» / «Servido» / reabrir: pone TODAS las líneas activas en el
    estado pedido (salto NUEVO→LISTO permitido, como en cocina real) y deriva
    la cabecera. Las líneas canceladas no se tocan."""
    ticket = await repo.get_kitchen_order(session, ticket_id)
    if ticket is None:
        raise _not_found("Comanda no encontrada")
    if domain.is_terminal(ticket.status):
        raise _conflict("La comanda ya está cerrada (servida o cancelada)")
    if not domain.can_transition_ticket(ticket.status, target) and ticket.status != target:
        raise _conflict(
            f"La comanda en «{ticket.status.value}» no puede pasar a «{target.value}»"
        )
    lines = await repo.list_lines_of_ticket(session, ticket.id)
    for kitchen_line in lines:
        if kitchen_line.status != target:
            if not domain.can_transition_line(kitchen_line.status, target):
                raise _conflict(
                    f"La línea «{kitchen_line.name}» está «{kitchen_line.status.value}»"
                    f" y no puede pasar a «{target.value}»"
                )
            kitchen_line.status = target
    await events_service.record(
        session,
        topic="kds",
        type="kds.ticket_bulk_status",
        payload={"kitchen_order_id": str(ticket.id), "status": target.value},
        actor_user_id=principal.user_id,
    )
    await _sync_ticket_header(session, ticket, actor_user_id=principal.user_id)
    await session.commit()
    return await _board_ticket(session, ticket.id)


async def set_ticket_priority(
    session: AsyncSession, principal: Principal, ticket_id: UUID, *, priority: int
) -> None:
    ticket = await repo.get_kitchen_order(session, ticket_id)
    if ticket is None:
        raise _not_found("Comanda no encontrada")
    if domain.is_terminal(ticket.status):
        raise _conflict("La comanda ya está cerrada (servida o cancelada)")
    if ticket.priority != priority:
        ticket.priority = priority
        await auth_repo.record_audit(
            session,
            action="kds.priority_set",
            entity="kitchen_order",
            user_id=principal.user_id,
            entity_id=ticket.id,
            after_data={"priority": priority},
        )
        await events_service.record(
            session,
            topic="kds",
            type="kds.ticket_priority",
            payload={"kitchen_order_id": str(ticket.id), "priority": priority},
            actor_user_id=principal.user_id,
        )
        await session.commit()


async def reprint_kot(session: AsyncSession, principal: Principal, ticket_id: UUID) -> PrintJob:
    """Reimpresión completa del KOT de una comanda (todas sus líneas activas).
    A diferencia del KOT automático (no falla sin impresora), aquí el cochero
    pide explícitamente imprimir: sin impresora de cocina es 409."""
    ticket = await repo.get_kitchen_order(session, ticket_id)
    if ticket is None:
        raise _not_found("Comanda no encontrada")
    printer = await printing_repo.get_default_printer(session, PrinterKind.kitchen)
    if printer is None:
        raise _conflict("No hay impresora de cocina configurada")
    lines = await repo.list_lines_of_ticket(session, ticket.id)
    station_names: dict[UUID | None, str | None] = {}
    line_pairs: list[tuple[KitchenOrderLine, str | None]] = []
    for kitchen_line in lines:
        if kitchen_line.station_id not in station_names:
            station = (
                await repo.get_station(session, kitchen_line.station_id)
                if kitchen_line.station_id is not None
                else None
            )
            station_names[kitchen_line.station_id] = station.name if station else None
        line_pairs.append((kitchen_line, station_names[kitchen_line.station_id]))
    table_name = await _table_name_for(session, ticket.order_id)
    job = await printing_service.PrintQueue(session).enqueue_copy(
        kind=PrintJobKind.kitchen,
        payload=_kot_payload(ticket, table_name, line_pairs, kind="kot"),
        printer=printer,
    )
    await auth_repo.record_audit(
        session,
        action="kds.kot_reprinted",
        entity="kitchen_order",
        user_id=principal.user_id,
        entity_id=ticket.id,
        after_data={"print_job_id": str(job.id)},
    )
    await events_service.record(
        session,
        topic="kds",
        type="kds.ticket_printed",
        payload={"kitchen_order_id": str(ticket.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Estaciones
# ---------------------------------------------------------------------------
async def create_station(
    session: AsyncSession, principal: Principal, *, name: str, sort_order: int
) -> KitchenStation:
    station = KitchenStation(name=name, sort_order=sort_order)
    session.add(station)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="kds.station_created",
        entity="kitchen_station",
        user_id=principal.user_id,
        entity_id=station.id,
        after_data={"name": station.name, "sort_order": station.sort_order},
    )
    await events_service.record(
        session,
        topic="kds",
        type="kds.stations_changed",
        payload={"station_id": str(station.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    logger.info("kds.station_created", station_id=str(station.id))
    return station


async def update_station(
    session: AsyncSession,
    principal: Principal,
    station_id: UUID,
    *,
    name: str | None,
    sort_order: int | None,
    active: bool | None,
) -> KitchenStation:
    station = await repo.get_station(session, station_id)
    if station is None:
        raise _not_found("Estación no encontrada")
    if name is not None:
        station.name = name
    if sort_order is not None:
        station.sort_order = sort_order
    if active is not None:
        station.active = active
    await auth_repo.record_audit(
        session,
        action="kds.station_updated",
        entity="kitchen_station",
        user_id=principal.user_id,
        entity_id=station.id,
        after_data={"name": station.name, "sort_order": station.sort_order, "active": station.active},
    )
    await events_service.record(
        session,
        topic="kds",
        type="kds.stations_changed",
        payload={"station_id": str(station.id)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return station

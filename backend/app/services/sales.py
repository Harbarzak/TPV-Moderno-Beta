"""Servicio de ventas (fase 06 · Motor de ventas): borradores, líneas, cierre,
anulación y devolución.

Reglas transversales (ARCHITECTURE.md §4-§7):
- El borrador vive en BD (auto-guardado en cada operación): "guardar venta" es
  persistir el ``Order`` en ``draft``; "recuperar venta" es listar
  ``status=draft`` y leer su detalle.
- Toda transición crítica es transaccional: el pedido se bloquea
  (``SELECT … FOR UPDATE``), se recalculan los totales con el motor puro
  (:mod:`app.domain.sales`) y se emite el evento genérico de venta
  (``sale_events``) que un futuro FiscalAdapter consumirá. Nada de adaptadores
  fiscales concretos en esta fase.
- Las ventas cobradas o anuladas NUNCA se borran; la anulación exige motivo y
  autor. La devolución es una orden nueva negativa enlazada en ``refunds``
  (una única devolución por venta original).
- Descuento distinto de cero exige el permiso ``orders.discount`` (comprobación
  dinámica aquí, no en la capa API).
- Reintentos idempotentes (fase 14 · Offline): con la misma
  ``Idempotency-Key`` la operación no se repite — la primera respuesta queda
  congelada en ``idempotency_keys`` DENTRO de la misma transacción y se
  reenvía tal cual; el cobro (§4.1) exige siempre la clave.

Nunca importa de ``api``: los controladores llaman a estas funciones.
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.hardware import CashDrawerAdapter, HardwareError
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.db.enums import OrderStatus, OrderType, PaymentKind, PaymentStatus, SaleEventType
from app.db.models.catalog import PaymentMethod
from app.db.models.sales import Order, OrderLine, Payment, Refund, Ticket
from app.domain import payments as pay
from app.domain import sales as calc
from app.repos import auth as auth_repo
from app.repos import sales as repo
from app.services import documents as documents_service
from app.services import events as events_service
from app.services import kitchen as kitchen_service
from app.services.auth import Principal
from app.services.idempotency import (
    Idempotency,
    Replay,
    Snapshot,
    lookup as idem_lookup,
    replay_after_conflict as idem_replay,
    store as idem_store,
)

logger = get_logger("tpv.sales")


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


def _already_paid(detail: str) -> AppError:
    return AppError(409, ErrorCode.SALE_ALREADY_PAID, detail)


def _settle_error(exc: pay.SettleError) -> AppError:
    """Mapea un cobro imposible a su ErrorCode estable (fase 07)."""

    mapping = {
        "insufficient": (422, ErrorCode.PAYMENT_INSUFFICIENT),
        "excess": (422, ErrorCode.PAYMENT_EXCESS),
    }
    status_code, code = mapping.get(exc.reason, (422, ErrorCode.VALIDATION_ERROR))
    return AppError(status_code, code, str(exc))


async def _load_payment_methods(
    session: AsyncSession, payments: list["ClosePayment"]
) -> dict[UUID, PaymentMethod]:
    """Formas de pago del cobro: todas existen y están activas."""

    methods: dict[UUID, PaymentMethod] = {}
    for item in payments:
        if item.payment_method_id in methods:
            continue
        method = await repo.get_payment_method(session, item.payment_method_id)
        if method is None:
            raise _not_found("Forma de pago no encontrada")
        if not method.active:
            raise _conflict("La forma de pago no está activa")
        methods[method.id] = method
    return methods


def _settle(
    total_cents: int, payments: list["ClosePayment"], methods: dict[UUID, PaymentMethod]
) -> tuple[tuple[pay.SettledPayment, ...], int]:
    """Valida el cobro contra el dominio puro y traduce sus errores."""

    try:
        return pay.settle_payments(
            total_cents,
            [
                pay.PaymentInput(
                    amount_cents=calc.money_to_cents(item.amount),
                    tendered_cents=calc.money_to_cents(item.tendered)
                    if item.tendered is not None
                    else None,
                    is_cash=methods[item.payment_method_id].kind == PaymentKind.cash,
                )
                for item in payments
            ],
        )
    except pay.SettleError as exc:
        raise _settle_error(exc) from exc


def _payment_dicts(
    payments: list["ClosePayment"],
    methods: dict[UUID, PaymentMethod],
    settled: tuple[pay.SettledPayment, ...],
) -> list[dict]:
    """Desglose de pagos para eventos/auditoría: dinero SIEMPRE string (§3)."""

    return [
        {
            "payment_method_id": str(item.payment_method_id),
            "code": methods[item.payment_method_id].code,
            "kind": methods[item.payment_method_id].kind.value,
            "amount": format(calc.cents_to_decimal(s.amount_cents), "f"),
            # tendered ya es Decimal en euros (no céntimos): solo se normaliza.
            "tendered": format(calc.cents_to_decimal(calc.money_to_cents(item.tendered)), "f")
            if item.tendered is not None
            else None,
            "change": format(calc.cents_to_decimal(s.change_cents), "f"),
        }
        for item, s in zip(payments, settled)
    ]


def _ensure_discount_permission(principal: Principal, discount_pct: Decimal) -> None:
    """Aplicar (o fijar) un descuento > 0 exige ``orders.discount``."""

    # El código del catálogo (seed.sql) es «orders.discount»: comprobar otro
    # código haría el permiso inalcanzable para cualquier rol, incluido admin.
    if discount_pct > 0 and "orders.discount" not in principal.permissions:
        raise AppError(
            403, ErrorCode.PERMISSION_DENIED, "No tienes permiso para aplicar descuentos"
        )


def _line_amounts(line: OrderLine) -> calc.LineAmounts:
    """Recalcula la línea desde su snapshot (nunca confía en importes guardados)."""

    return calc.line_amounts(
        price_cents=calc.money_to_cents(line.unit_price),
        qty_milli=calc.qty_to_milli(line.quantity),
        discount_bp=calc.pct_to_bp(line.discount_pct),
        tax_rate_bp=calc.pct_to_bp(line.tax_rate),
    )


def _totals_dict(totals: calc.OrderTotals) -> dict:
    """Totales como payload JSONB/respuesta: dinero SIEMPRE string (§3)."""

    return {
        "base": format(calc.cents_to_decimal(totals.base), "f"),
        "tax": format(calc.cents_to_decimal(totals.tax), "f"),
        "total": format(calc.cents_to_decimal(totals.total), "f"),
        "slices": [
            {
                "rate_bp": s.rate_bp,
                "base": format(calc.cents_to_decimal(s.base), "f"),
                "tax": format(calc.cents_to_decimal(s.tax), "f"),
                "total": format(calc.cents_to_decimal(s.total), "f"),
            }
            for s in totals.slices
        ],
    }


def _line_dict(line: OrderLine) -> dict:
    return {
        "id": str(line.id),
        "product_id": str(line.product_id) if line.product_id else None,
        "name": line.name,
        "unit_price": format(line.unit_price, "f"),
        "tax_rate": format(line.tax_rate, "f"),
        "quantity": format(line.quantity, "f"),
        "discount_pct": format(line.discount_pct, "f"),
        "base": format(line.line_base, "f"),
        "total": format(line.line_total, "f"),
    }


def _ensure_draft(order: Order) -> None:
    if order.status == OrderStatus.paid:
        raise _already_paid("La venta ya está cobrada; un cobrado ya no se edita")
    if order.status == OrderStatus.voided:
        raise _conflict("La venta está anulada")


async def _cash_session_for_sale(
    session: AsyncSession, cash_session_id: UUID, terminal_id: UUID
) -> None:
    """Sesión de caja válida para cobrar: existe, abierta y del terminal de la venta."""

    cash = await repo.get_cash_session_for_update(session, cash_session_id)
    if cash is None:
        raise _not_found("Sesión de caja no encontrada")
    if cash.closed_at is not None:
        raise _conflict("La sesión de caja está cerrada")
    if cash.terminal_id != terminal_id:
        raise _conflict("La sesión de caja pertenece a otro terminal")


# ---------------------------------------------------------------------------
# Crear / leer borradores
# ---------------------------------------------------------------------------
async def create_order(
    session: AsyncSession,
    principal: Principal,
    *,
    terminal_id: UUID,
    order_type: OrderType = OrderType.bar,
    dining_table_id: UUID | None = None,
    customer_id: UUID | None = None,
    guest_count: int | None = None,
    note: str | None = None,
    idempotency: Idempotency | None = None,
    snapshot: Snapshot | None = None,
) -> Order | Replay:
    """Crea el borrador de venta ("abrir ticket"); se auto-guarda en cada paso.

    Con ``idempotency`` (fase 14): un reintento con la misma clave devuelve la
    respuesta congelada (``Replay``) sin crear un segundo pedido.
    """

    if idempotency is not None:
        replay = await idem_lookup(session, idempotency)
        if replay is not None:
            return replay

    terminal = await repo.get_terminal(session, terminal_id)
    if terminal is None:
        raise _not_found("Terminal no encontrado")
    if not terminal.active:
        raise _conflict("El terminal no está activo")

    if dining_table_id is not None:
        table = await repo.get_dining_table(session, dining_table_id)
        if table is None or not table.active:
            raise _not_found("Mesa no encontrada")
        if await repo.find_draft_by_table(session, dining_table_id):
            raise _conflict("La mesa ya tiene una comanda abierta")

    order = Order(
        terminal_id=terminal_id,
        user_id=principal.user_id,
        status=OrderStatus.draft,
        order_type=order_type,
        dining_table_id=dining_table_id,
        customer_id=customer_id,
        guest_count=guest_count,
        note=note,
    )
    session.add(order)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="sales.order_created",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"terminal_id": str(terminal_id), "order_type": order_type.value},
    )
    # Hub WS (fase 12): aviso mínimo; el detalle se lee por REST.
    await events_service.record(
        session,
        topic="sales",
        type="sales.created",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(terminal_id),
            "table_id": str(dining_table_id) if dining_table_id else None,
            "status": order.status.value,
        },
        actor_user_id=principal.user_id,
    )
    try:
        if idempotency is not None:
            # La clave viaja EN la transacción del pedido: sin ventana de
            # caída donde el pedido exista y el reintento no lo sepa.
            await idem_store(session, idempotency, status=201, body=snapshot(order))
        await session.commit()
    except IntegrityError as exc:
        if idempotency is None:
            raise
        return await idem_replay(
            session, idempotency, _conflict("La operación choca con otra concurrente")
        )
    return order


async def get_order_detail(
    session: AsyncSession, order_id: UUID
) -> tuple[Order, list[OrderLine], list[tuple[Payment, PaymentMethod]]]:
    order = await repo.get_order(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    payments = await repo.list_payments_with_method(session, order_id)
    return order, await repo.list_lines(session, order_id), payments


async def list_orders(
    session: AsyncSession,
    *,
    status: OrderStatus | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Order], int]:
    """Listado (recuperar borradores con ``status=draft``)."""

    return await repo.list_orders(session, status=status, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Líneas del borrador
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NewLine:
    """Línea de borrador: producto de catálogo (snapshot) o artículo libre."""

    quantity: Decimal                       # > 0 (la devolución va por su flujo)
    discount_pct: Decimal = Decimal("0")    # 0-100
    product_id: UUID | None = None
    name: str | None = None                 # artículo libre
    unit_price: Decimal | None = None       # artículo libre
    tax_rate: Decimal | None = None         # artículo libre (0-100)
    notes: str | None = None


async def add_line(
    session: AsyncSession,
    principal: Principal,
    order_id: UUID,
    data: NewLine,
    *,
    idempotency: Idempotency | None = None,
    snapshot: Snapshot | None = None,
) -> OrderLine | Replay:
    """Añade una línea al borrador; idempotente con clave (fase 14)."""

    if idempotency is not None:
        replay = await idem_lookup(session, idempotency)
        if replay is not None:
            return replay

    order = await repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    _ensure_discount_permission(principal, data.discount_pct)

    if data.product_id is not None:
        found = await repo.get_sellable_product(session, data.product_id)
        if found is None:
            raise _not_found("Producto no encontrado o inactivo")
        product, tax_rate = found
        name = product.short_name or product.name
        unit_price = product.price
        tax = tax_rate.rate
    else:
        # Artículo libre: el precio y el tipo los trae la petición (validados en API).
        if data.name is None or data.unit_price is None or data.tax_rate is None:
            raise _validation(
                "El artículo libre requiere name, unit_price y tax_rate"
            )
        name, unit_price, tax = data.name, data.unit_price, data.tax_rate

    qty_milli = calc.qty_to_milli(data.quantity)
    amounts = calc.line_amounts(
        price_cents=calc.money_to_cents(unit_price),
        qty_milli=qty_milli,
        discount_bp=calc.pct_to_bp(data.discount_pct),
        tax_rate_bp=calc.pct_to_bp(tax),
    )
    line = OrderLine(
        order_id=order.id,
        product_id=data.product_id,
        name=name,
        unit_price=unit_price,
        tax_rate=tax,
        quantity=calc.milli_to_qty(qty_milli),
        discount_pct=data.discount_pct,
        line_base=calc.cents_to_decimal(amounts.base),
        line_total=calc.cents_to_decimal(amounts.total),
        notes=data.notes,
        sort_order=await repo.max_line_sort_order(session, order.id) + 1,
    )
    session.add(line)
    await session.flush()
    await auth_repo.record_audit(
        session,
        action="sales.line_added",
        entity="order_line",
        user_id=principal.user_id,
        entity_id=line.id,
        after_data={"order_id": str(order.id), **_line_dict(line)},
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.updated",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            "status": order.status.value,
        },
        actor_user_id=principal.user_id,
    )
    # KDS (fase 32): si el producto es de cocina, su línea de comanda (y el
    # KOT) nace en ESTA transacción: si todo revierte, nunca existió.
    await kitchen_service.on_sale_line_added(
        session,
        order=order,
        line=line,
        product=product if data.product_id is not None else None,
        actor_user_id=principal.user_id,
    )
    try:
        if idempotency is not None:
            await idem_store(session, idempotency, status=201, body=snapshot(line))
        await session.commit()
    except IntegrityError as exc:
        if idempotency is None:
            raise
        return await idem_replay(
            session, idempotency, _conflict("La operación choca con otra concurrente")
        )
    return line


async def update_line(
    session: AsyncSession,
    principal: Principal,
    order_id: UUID,
    line_id: UUID,
    *,
    fields: dict,
) -> OrderLine:
    """Edita cantidad, descuento o notas de una línea del borrador."""

    order = await repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    line = await repo.get_line(session, order.id, line_id)
    if line is None:
        raise _not_found("Línea no encontrada")

    if "discount_pct" in fields:
        _ensure_discount_permission(principal, fields["discount_pct"])

    if "quantity" in fields:
        quantity: Decimal = fields["quantity"]
        if quantity <= 0:
            raise _validation(
                "La cantidad debe ser positiva (para devolver, usa el flujo de devolución)"
            )
        line.quantity = quantity
    if "discount_pct" in fields:
        line.discount_pct = fields["discount_pct"]
    if "notes" in fields:
        line.notes = fields["notes"]

    amounts = _line_amounts(line)
    line.line_base = calc.cents_to_decimal(amounts.base)
    line.line_total = calc.cents_to_decimal(amounts.total)

    # KDS (fase 32): cantidad/nota se sincronizan en la comanda mientras la
    # cocina no la haya marcado lista; lo ya cocinado no se reescribe.
    if "quantity" in fields or "notes" in fields:
        await kitchen_service.on_sale_line_updated(
            session, line=line, actor_user_id=principal.user_id
        )

    await auth_repo.record_audit(
        session,
        action="sales.line_updated",
        entity="order_line",
        user_id=principal.user_id,
        entity_id=line.id,
        after_data={"order_id": str(order.id), "fields": sorted(fields), **_line_dict(line)},
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.updated",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            "status": order.status.value,
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return line


async def remove_line(
    session: AsyncSession, principal: Principal, order_id: UUID, line_id: UUID
) -> None:
    """Quita una línea del borrador (borrado físico: el draft aún no es histórico)."""

    order = await repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    line = await repo.get_line(session, order.id, line_id)
    if line is None:
        raise _not_found("Línea no encontrada")

    # KDS (fase 32): la línea de comanda se retira (o cancela) ANTES de borrar
    # la línea de venta, a la que referencia por FK.
    await kitchen_service.on_sale_line_removed(
        session, line=line, actor_user_id=principal.user_id
    )

    await session.delete(line)
    await auth_repo.record_audit(
        session,
        action="sales.line_removed",
        entity="order_line",
        user_id=principal.user_id,
        entity_id=line_id,
        after_data={"order_id": str(order.id), **_line_dict(line)},
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.updated",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            "status": order.status.value,
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Cerrar (cobrar), anular y devolver
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ClosePayment:
    """Pago de un cierre o devolución: forma, importe y (efectivo) entregado."""

    payment_method_id: UUID
    amount: Decimal                     # > 0: lo que aplica al total
    tendered: Decimal | None = None     # solo efectivo: lo entregado (cambio)
    external_ref: str | None = None     # referencia del pinpad (fase 14)


@dataclass(frozen=True, slots=True)
class CloseResult:
    """Resultado del cobro: la venta, sus pagos (con forma), el cambio total
    y el ticket emitido (fase 09: todo cobro genera su ticket)."""

    order: Order
    payments: list[tuple[Payment, PaymentMethod]]
    change_total: Decimal
    ticket: Ticket


async def close_order(
    session: AsyncSession,
    principal: Principal,
    order_id: UUID,
    *,
    cash_session_id: UUID,
    payments: list[ClosePayment],
    drawer_adapter: CashDrawerAdapter | None = None,
    idempotency: Idempotency | None = None,
    snapshot: Snapshot | None = None,
) -> CloseResult | Replay:
    """Cobra y cierra la venta: status=paid, totales congelados y pagos.

    El backend es la ÚNICA autoridad del estado pagado: recalcula el total de
    las líneas y exige que la suma de pagos lo cubra EXACTAMENTE (422
    ``PAYMENT_INSUFFICIENT`` si falta, ``PAYMENT_EXCESS`` si sobra sin efectivo
    que lo absorba; el exceso real se entrega como «importe entregado» en
    efectivo y vuelve como cambio). Pagos, totales y evento ``sale_closed``
    se escriben en la MISMA transacción: un cobro es todo o nada. Un cobrado
    no se vuelve a cobrar (409 ``SALE_ALREADY_PAID``). Tras el commit abre el
    cajón si algún pago es de una forma que lo abre (fase 11: best-effort —
    el hardware nunca revierte una venta ya cobrada).

    El cobro exige ``idempotency`` (fase 14, §4.1): es la única llamada cuyo
    reintento puede duplicar dinero. Con la misma clave y contenido devuelve
    la respuesta congelada (mismo ticket) sin repetir pagos, eventos, cajón
    ni auditoría; la API es quien la exige (cabecera HTTP).
    """

    if idempotency is not None:
        # El replay gana incluso sobre un 409 de negocio: si hay respuesta
        # congelada, el cobro YA se hizo y repetirlo sería el error.
        replay = await idem_lookup(session, idempotency)
        if replay is not None:
            return replay

    order = await repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    _ensure_draft(order)

    await _cash_session_for_sale(session, cash_session_id, order.terminal_id)

    lines = await repo.list_lines(session, order.id)
    if not lines:
        raise _conflict("No se puede cerrar una venta sin líneas")

    if not payments:
        raise _validation("El cobro requiere al menos un pago")
    methods = await _load_payment_methods(session, payments)

    totals = calc.order_totals(
        [(calc.pct_to_bp(line.tax_rate), _line_amounts(line)) for line in lines]
    )
    settled, change_total = _settle(totals.total, payments, methods)

    paid_at = repo.utcnow()
    order.status = OrderStatus.paid
    order.cash_session_id = cash_session_id
    order.paid_at = paid_at
    order.total_base = calc.cents_to_decimal(totals.base)
    order.total_tax = calc.cents_to_decimal(totals.tax)
    order.total_amount = calc.cents_to_decimal(totals.total)
    order.tax_summary = _totals_dict(totals)

    created: list[Payment] = []
    for item, s in zip(payments, settled):
        row = Payment(
            order_id=order.id,
            payment_method_id=item.payment_method_id,
            terminal_id=order.terminal_id,
            amount=calc.cents_to_decimal(s.amount_cents),
            status=PaymentStatus.confirmed,
            external_ref=item.external_ref,
            confirmed_at=paid_at,
        )
        session.add(row)
        created.append(row)

    await repo.record_sale_event(
        session,
        order_id=order.id,
        event_type=SaleEventType.sale_closed,
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "user_id": str(order.user_id),
            "cash_session_id": str(cash_session_id),
            "paid_at": paid_at.isoformat(),
            "totals": _totals_dict(totals),
            "lines": [_line_dict(line) for line in lines],
            "payments": _payment_dicts(payments, methods, settled),
            "change_total": format(calc.cents_to_decimal(change_total), "f"),
        },
    )
    # Ticket del cobro (fase 09): en la MISMA transacción — una venta cobrada
    # sin su ticket no existe. También el de devolución (orden negativa).
    ticket = await documents_service.issue_ticket_for_order(
        session,
        principal,
        order=order,
        lines=lines,
        payment_dicts=_payment_dicts(payments, methods, settled),
        change_total=calc.cents_to_decimal(change_total),
        issued_at=paid_at,
    )
    await auth_repo.record_audit(
        session,
        action="sales.order_closed",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={
            "total": format(order.total_amount, "f"),
            "lines": len(lines),
            "payments": len(payments),
            "change_total": format(calc.cents_to_decimal(change_total), "f"),
        },
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.closed",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            "cash_session_id": str(cash_session_id),
            "total": format(order.total_amount, "f"),
        },
        actor_user_id=principal.user_id,
    )
    result = CloseResult(
        order=order,
        payments=[(row, methods[row.payment_method_id]) for row in created],
        change_total=calc.cents_to_decimal(change_total),
        ticket=ticket,
    )
    try:
        if idempotency is not None:
            # La clave viaja EN la transacción del cobro (§4.1): sin ventana
            # de caída donde el dinero esté cobrado y el reintento no lo sepa.
            await idem_store(session, idempotency, status=200, body=snapshot(result))
        await session.commit()
    except IntegrityError as exc:
        if idempotency is None:
            raise
        return await idem_replay(
            session,
            idempotency,
            _conflict("El cobro choca con otra operación concurrente"),
        )
    await _open_drawer_if_cash(drawer_adapter, order.terminal_id, created, methods)
    return result


async def _open_drawer_if_cash(
    adapter: CashDrawerAdapter | None,
    terminal_id: UUID,
    payments: list[Payment],
    methods: dict[UUID, PaymentMethod],
) -> None:
    """Apertura del cajón (fase 11) SIEMPRE posterior al commit: el cajón es
    un periférico inyectado (:class:`CashDrawerAdapter`, cero fabricantes en
    el motor) y su fallo se registra pero no toca al cobro ya confirmado."""

    if adapter is None:
        return
    if not any(methods[p.payment_method_id].opens_drawer for p in payments):
        return
    try:
        await adapter.open_drawer(terminal_id=terminal_id)
        logger.info("hardware.drawer_opened", terminal_id=str(terminal_id))
    except HardwareError as exc:
        logger.warning(
            "hardware.drawer_open_failed",
            terminal_id=str(terminal_id),
            code=exc.code,
            error=str(exc),
        )


async def void_order(
    session: AsyncSession,
    principal: Principal,
    order_id: UUID,
    *,
    reason: str,
) -> Order:
    """Anula la venta (borrador o cobrada): motivo y autor obligatorios.

    Nunca borra la venta; conserva los totales si ya estaba cobrada y emite el
    evento inverso ``sale_voided`` para el futuro FiscalAdapter.
    """

    order = await repo.get_order_for_update(session, order_id)
    if order is None:
        raise _not_found("Venta no encontrada")
    if order.status == OrderStatus.voided:
        raise _conflict("La venta ya está anulada")

    previous_status = order.status.value
    order.status = OrderStatus.voided
    order.voided_at = repo.utcnow()
    order.voided_by = principal.user_id
    order.void_reason = reason

    await repo.record_sale_event(
        session,
        order_id=order.id,
        event_type=SaleEventType.sale_voided,
        payload={
            "order_id": str(order.id),
            "previous_status": previous_status,
            "reason": reason,
            "voided_by": str(principal.user_id),
            "totals": _totals_dict(_order_totals(order)) if order.total_amount is not None else None,
        },
    )
    await auth_repo.record_audit(
        session,
        action="sales.order_voided",
        entity="order",
        user_id=principal.user_id,
        entity_id=order.id,
        after_data={"previous_status": previous_status, "reason": reason},
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.voided",
        payload={
            "order_id": str(order.id),
            "terminal_id": str(order.terminal_id),
            "table_id": str(order.dining_table_id) if order.dining_table_id else None,
            "reason": reason,
        },
        actor_user_id=principal.user_id,
    )
    # KDS (fase 32): la comanda de cocina se cancela con la venta anulada.
    await kitchen_service.on_sale_order_voided(
        session, order=order, actor_user_id=principal.user_id
    )
    await session.commit()
    return order


def _order_totals(order: Order) -> calc.OrderTotals:
    """Reconstruye ``OrderTotals`` desde el tax_summary persistido."""

    slices = tuple(
        calc.TaxSlice(
            rate_bp=int(s["rate_bp"]),
            base=calc.money_to_cents(Decimal(s["base"])),
            tax=calc.money_to_cents(Decimal(s["tax"])),
            total=calc.money_to_cents(Decimal(s["total"])),
        )
        for s in (order.tax_summary or {}).get("slices", [])
    )
    return calc.OrderTotals(
        base=calc.money_to_cents(order.total_base or Decimal(0)),
        tax=calc.money_to_cents(order.total_tax or Decimal(0)),
        total=calc.money_to_cents(order.total_amount or Decimal(0)),
        slices=slices,
    )


# ---------------------------------------------------------------------------
# Devolución
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RefundLine:
    """Línea a devolver de la venta original (cantidad positiva)."""

    line_id: UUID
    quantity: Decimal  # > 0 y ≤ cantidad vendida de esa línea


async def refund_order(
    session: AsyncSession,
    principal: Principal,
    original_order_id: UUID,
    *,
    cash_session_id: UUID,
    reason: str,
    lines: list[RefundLine],
    payments: list[ClosePayment],
) -> Order:
    """Devuelve una venta cobrada: orden nueva negativa + fila ``refunds``.

    Una venta original admite UNA sola devolución (UNIQUE en ``refunds``);
    dentro de esa devolución se pueden devolver cantidades parciales. Los
    pagos describen cómo sale el dinero (suma exacta del importe devuelto,
    sin «entregado»: el cambio no aplica en una devolución) y quedan como
    filas ``payments`` positivas sobre la orden negativa.
    """

    if not lines:
        raise _validation("La devolución requiere al menos una línea")
    if not payments:
        raise _validation("La devolución requiere al menos un pago")
    if any(item.tendered is not None for item in payments):
        raise _validation("Una devolución no admite importe entregado (no hay cambio)")

    original = await repo.get_order_for_update(session, original_order_id)
    if original is None:
        raise _not_found("Venta no encontrada")
    if original.status == OrderStatus.draft:
        raise _conflict("No se puede devolver una venta sin cobrar")
    if original.status == OrderStatus.voided:
        raise _conflict("No se puede devolver una venta anulada")
    if await repo.get_refund_by_original(session, original.id):
        raise _conflict("La venta ya tiene una devolución")

    await _cash_session_for_sale(session, cash_session_id, original.terminal_id)

    methods = await _load_payment_methods(session, payments)
    original_lines = {
        line.id: line for line in await repo.list_lines(session, original.id)
    }

    refund = Order(
        terminal_id=original.terminal_id,
        user_id=principal.user_id,
        cash_session_id=cash_session_id,
        customer_id=original.customer_id,
        status=OrderStatus.paid,  # la devolución nace cerrada (venta negativa)
        order_type=original.order_type,
        paid_at=repo.utcnow(),
    )

    # Primera pasada: valida y recalcula cada línea SIN tocar la BD, para poder
    # fijar los totales de la orden ANTES de su INSERT: la orden nace cobrada y
    # el CHECK ck_orders_paid_complete no admite importes nulos (un flush previo
    # los enviaba a NULL y tumbaba toda devolución con un 500).
    specs: list[tuple[int, calc.LineAmounts, OrderLine, int, int]] = []
    for position, item in enumerate(lines, start=1):
        source = original_lines.get(item.line_id)
        if source is None:
            raise _validation("La línea a devolver no pertenece a la venta original")
        qty_milli = calc.qty_to_milli(item.quantity)
        if qty_milli <= 0:
            raise _validation("La cantidad a devolver debe ser positiva")
        sold_milli = calc.qty_to_milli(source.quantity)
        if qty_milli > sold_milli:
            raise _validation(
                f"No se puede devolver más de lo vendido en «{source.name}»"
            )
        amounts = calc.line_amounts(
            price_cents=calc.money_to_cents(source.unit_price),
            qty_milli=-qty_milli,
            discount_bp=calc.pct_to_bp(source.discount_pct),
            tax_rate_bp=calc.pct_to_bp(source.tax_rate),
        )
        specs.append(
            (calc.pct_to_bp(source.tax_rate), amounts, source, qty_milli, position)
        )

    totals = calc.order_totals([(bp, am) for bp, am, *_ in specs])
    refund.total_base = calc.cents_to_decimal(totals.base)
    refund.total_tax = calc.cents_to_decimal(totals.tax)
    refund.total_amount = calc.cents_to_decimal(totals.total)
    refund.tax_summary = _totals_dict(totals)
    session.add(refund)
    await session.flush()  # INSERT con totales ya fijos; asigna refund.id

    refund_lines: list[OrderLine] = []
    for _bp, amounts, source, qty_milli, position in specs:
        refund_lines.append(
            OrderLine(
                order_id=refund.id,
                product_id=source.product_id,
                name=source.name,
                unit_price=source.unit_price,
                tax_rate=source.tax_rate,
                quantity=calc.milli_to_qty(-qty_milli),
                discount_pct=source.discount_pct,
                line_base=calc.cents_to_decimal(amounts.base),
                line_total=calc.cents_to_decimal(amounts.total),
                notes=f"Devolución de {original.id}: {reason}"[:200],
                sort_order=position,
            )
        )
    session.add_all(refund_lines)
    await session.flush()

    # El dinero devuelto: suma exacta del importe devuelto (valor absoluto).
    settled, _ = _settle(abs(totals.total), payments, methods)
    for item, s in zip(payments, settled):
        session.add(
            Payment(
                order_id=refund.id,
                payment_method_id=item.payment_method_id,
                terminal_id=refund.terminal_id,
                amount=calc.cents_to_decimal(s.amount_cents),
                status=PaymentStatus.confirmed,
                external_ref=item.external_ref,
                confirmed_at=refund.paid_at,
            )
        )

    row = Refund(
        original_order_id=original.id,
        refund_order_id=refund.id,
        user_id=principal.user_id,
        amount=calc.cents_to_decimal(abs(totals.total)),  # CHECK: amount > 0
        reason=reason,
    )
    session.add(row)
    await repo.record_sale_event(
        session,
        order_id=refund.id,
        event_type=SaleEventType.refund_issued,
        payload={
            "original_order_id": str(original.id),
            "refund_order_id": str(refund.id),
            "reason": reason,
            "cash_session_id": str(cash_session_id),
            "totals": _totals_dict(totals),
            "lines": [_line_dict(line) for line in refund_lines],
            "payments": _payment_dicts(payments, methods, settled),
        },
    )
    await auth_repo.record_audit(
        session,
        action="sales.refund_issued",
        entity="refund",
        user_id=principal.user_id,
        entity_id=row.id,
        after_data={
            "original_order_id": str(original.id),
            "refund_order_id": str(refund.id),
            "amount": format(row.amount, "f"),
            "reason": reason,
            "payments": len(payments),
        },
    )
    # Ticket de la devolución (fase 09): orden negativa → ticket que
    # referencia al original; misma transacción que la devolución.
    await documents_service.issue_ticket_for_order(
        session,
        principal,
        order=refund,
        lines=refund_lines,
        payment_dicts=_payment_dicts(payments, methods, settled),
        change_total=Decimal(0),
        issued_at=refund.paid_at,
    )
    await events_service.record(
        session,
        topic="sales",
        type="sales.refunded",
        payload={
            "order_id": str(refund.id),
            "original_order_id": str(original.id),
            "terminal_id": str(refund.terminal_id),
            "cash_session_id": str(cash_session_id),
            "total": format(refund.total_amount, "f"),
        },
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return refund

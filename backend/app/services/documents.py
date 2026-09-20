"""Servicio de documentos (fase 09 · Tickets y facturas).

- **Ticket** (documento comercial): se emite AUTOMÁTICAMENTE al cobrar cada
  venta —también en las devoluciones, cuya orden es negativa y cuyo ticket
  referencia al original—. Numeración por terminal (``tickets.series``);
  ``issue_ticket_for_order`` NO hace commit: vive en la MISMA transacción del
  cobro (una venta cobrada sin su ticket es un cobro que no existió).
- **Factura** (documento fiscal básico): bajo demanda, agrupa 1..N ventas
  cobradas y positivas del MISMO cliente con NIF. Serie por año
  (``invoices.series``); las ventas ya facturadas no se re-facturan
  (UNIQUE en ``invoice_lines.order_id``). Anulación con motivo.
- **Rectificativa**: serie anual ``invoices.rect_series`` e importes
  negativos. Parcial (importes de la orden de devolución) o total
  (importes invertidos de la factura); enlaza con la original.
- **Cabecera de negocio**: nombre fiscal y logo configurables en
  ``parameters`` (Configuración/Parámetros); el logo vive como FICHERO en el
  volumen ``TPV_DATA_DIR/logos`` (nunca en la BD, ARCHITECTURE.md) y se
  embebe como data URI dentro del payload de cada documento: cambiar el logo
  no altera los documentos ya emitidos. Sin logo configurado, el documento
  se genera sin él.

Nada específico de Veri*Factu/TicketBAI: plugin futuro fuera de esta fase.
Los renders congelados (payload) y la matemática viven en
:mod:`app.domain.documents`. Nunca importa de ``api``.
"""

import base64
import binascii
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.db.enums import InvoiceStatus, OrderStatus, PrintJobKind, SequenceScope
from app.db.models.sales import Invoice, InvoiceLine, Order, OrderLine, Ticket
from app.domain import documents as docs
from app.repos import auth as auth_repo
from app.repos import documents as repo
from app.repos import sales as sales_repo
from app.services import events as events_service
from app.services import printing as printing_service
from app.services.auth import Principal


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Parámetros de negocio y logo (Configuración/Parámetros)
# ---------------------------------------------------------------------------
BUSINESS_FIELDS = ("name", "tax_id", "address", "phone")
_LOGO_PARAM = "business.logo"
_LOGO_DESCRIPTION = "Datos fiscales del negocio: cabecera de tickets y facturas"
LOGO_MAX_BYTES = 512 * 1024  # 512 KiB: sobra para una cabecera impresa


async def _param_str(session: AsyncSession, key: str, default: str) -> str:
    """Valor string de un parámetro (JSONB); si no existe, el defecto."""

    row = await repo.get_parameter(session, key)
    if row is None or not isinstance(row.value, str):
        return default
    return row.value


async def _business_fields(session: AsyncSession) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in BUSINESS_FIELDS:
        values[field] = await _param_str(session, f"business.{field}", "")
    return values


def _logo_dir() -> Path:
    return Path(get_settings().data_dir) / "logos"


def _logo_path(mime: str) -> Path | None:
    extension = docs.logo_extension(mime)
    if extension is None:
        return None
    return _logo_dir() / f"logo.{extension}"


async def _logo_record(session: AsyncSession) -> dict | None:
    """Referencia del logo en ``parameters`` (mime/tamaño/fecha), sin datos."""

    row = await repo.get_parameter(session, _LOGO_PARAM)
    if row is None or not isinstance(row.value, dict):
        return None
    return row.value


async def _logo_data_uri(session: AsyncSession) -> str | None:
    """Logo como data URI para embeber en el render; None si no está o el
    fichero no está accesible (el documento se genera sin él)."""

    record = await _logo_record(session)
    if record is None:
        return None
    mime = record.get("mime")
    path = _logo_path(mime) if isinstance(mime, str) else None
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return docs.build_logo_data_uri(mime, raw)


async def _business_header(session: AsyncSession) -> dict:
    """Cabecera impresa: datos fiscales + logo embebido (si existen)."""

    return docs.build_header(await _business_fields(session), await _logo_data_uri(session))


async def get_business_settings(session: AsyncSession) -> dict:
    """Configuración de documentos (parámetros + referencia del logo)."""

    return {
        "business": await _business_fields(session),
        "logo": await _logo_record(session),
        "series": {
            "tickets": await _param_str(session, "tickets.series", "A"),
            "invoices": await _param_str(session, "invoices.series", "FAC"),
            "rectifications": await _param_str(session, "invoices.rect_series", "R"),
        },
        "currency": {
            "decimals": await _param_str(session, "currency.decimals", "2"),
        },
    }


async def update_business_settings(
    session: AsyncSession, principal: Principal, *, fields: dict[str, str]
) -> dict:
    """Actualiza los datos fiscales de cabecera (solo los campos enviados)."""

    for field, value in fields.items():
        await repo.upsert_parameter(
            session, key=f"business.{field}", value=value, description=_LOGO_DESCRIPTION
        )
    await auth_repo.record_audit(
        session,
        action="documents.business_settings_updated",
        entity="parameter",
        user_id=principal.user_id,
        after_data={"fields": sorted(fields)},
    )
    await events_service.record(
        session,
        topic="system",
        type="system.business_settings_updated",
        payload={"fields": sorted(fields)},
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return await get_business_settings(session)


async def upload_logo(
    session: AsyncSession, principal: Principal, *, mime: str, data_b64: str
) -> dict:
    """Guarda el logo como FICHERO en el volumen y deja la referencia en
    ``parameters``. Solo png/jpeg, base64, máximo 512 KiB."""

    path = _logo_path(mime)
    if path is None:
        raise _validation("El logo debe ser una imagen png o jpeg")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _validation("El logo no es base64 válido") from exc
    if len(raw) == 0:
        raise _validation("El logo está vacío")
    if len(raw) > LOGO_MAX_BYTES:
        raise _validation("El logo supera el máximo de 512 KiB")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    record = {
        "mime": mime,
        "size": len(raw),
        "uploaded_at": _utcnow().isoformat(),
        "uploaded_by": str(principal.user_id),
    }
    await repo.upsert_parameter(
        session, key=_LOGO_PARAM, value=record,
        description="Logo de cabecera de documentos (fichero en volumen)",
    )
    await auth_repo.record_audit(
        session,
        action="documents.logo_updated",
        entity="parameter",
        user_id=principal.user_id,
        after_data={"mime": mime, "size": len(raw)},
    )
    await events_service.record(
        session,
        topic="system",
        type="system.logo_updated",
        payload=None,
        actor_user_id=principal.user_id,
    )
    await session.commit()
    return record


async def delete_logo(session: AsyncSession, principal: Principal) -> None:
    """Elimina el logo (fichero + referencia). Los documentos ya emitidos
    conservan su copia embebida: son snapshots históricos."""

    record = await _logo_record(session)
    if record is None:
        raise _not_found("No hay logo configurado")

    mime = record.get("mime")
    path = _logo_path(mime) if isinstance(mime, str) else None
    if path is not None:
        path.unlink(missing_ok=True)
    await repo.delete_parameter(session, _LOGO_PARAM)
    await auth_repo.record_audit(
        session,
        action="documents.logo_removed",
        entity="parameter",
        user_id=principal.user_id,
    )
    await events_service.record(
        session,
        topic="system",
        type="system.logo_removed",
        payload=None,
        actor_user_id=principal.user_id,
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Renders: snapshots de líneas y pagos (dinero SIEMPRE string, §3)
# ---------------------------------------------------------------------------
def _line_render(line: OrderLine) -> dict:
    return {
        "name": line.name,
        "quantity": format(line.quantity, "f"),
        "unit_price": format(line.unit_price, "f"),
        "tax_rate": format(line.tax_rate, "f"),
        "discount_pct": format(line.discount_pct, "f"),
        "base": format(line.line_base, "f"),
        "total": format(line.line_total, "f"),
    }


def _money(value: Decimal | None) -> str:
    return format(value if value is not None else Decimal(0), "f")


# ---------------------------------------------------------------------------
# Tickets (emisión automática en el cobro)
# ---------------------------------------------------------------------------
async def issue_ticket_for_order(
    session: AsyncSession,
    principal: Principal,
    *,
    order: Order,
    lines: list[OrderLine],
    payment_dicts: list[dict],
    change_total: Decimal,
    issued_at: datetime,
) -> Ticket:
    """Emite el ticket de una venta cobrada, DENTRO de la transacción del
    cobro (sin commit: lo hace el flujo de ventas). También para devoluciones:
    la orden negativa genera un ticket que referencia al original."""

    series = await _param_str(session, "tickets.series", "A")
    number = await repo.next_number(
        session,
        scope=SequenceScope.ticket,
        terminal_id=order.terminal_id,
        year=None,
        series=series,
    )

    is_refund = order.total_amount is not None and order.total_amount < 0
    order_block: dict = {
        "id": str(order.id),
        "type": "refund" if is_refund else "sale",
        "original_order_id": None,
        "original_doc_number": None,
        "customer_id": str(order.customer_id) if order.customer_id else None,
    }
    if is_refund:
        link = await repo.get_refund_by_refund_order(session, order.id)
        if link is not None:
            order_block["original_order_id"] = str(link.original_order_id)
            original = await repo.get_ticket_by_order(session, link.original_order_id)
            if original is not None:
                order_block["original_doc_number"] = docs.format_ticket_number(
                    original.series, original.number
                )

    payload = docs.build_ticket_payload(
        doc_number=docs.format_ticket_number(series, number),
        series=series,
        number=number,
        issued_at=issued_at.isoformat(),
        header=await _business_header(session),
        order=order_block,
        lines=[_line_render(line) for line in lines],
        totals=order.tax_summary or {},
        payments=payment_dicts,
        change_total=_money(change_total),
    )
    ticket = await repo.create_ticket(
        session,
        order_id=order.id,
        terminal_id=order.terminal_id,
        series=series,
        number=number,
        payload=payload,
        printed_at=issued_at,
    )
    await auth_repo.record_audit(
        session,
        action="documents.ticket_issued",
        entity="ticket",
        user_id=principal.user_id,
        entity_id=ticket.id,
        after_data={
            "order_id": str(order.id),
            "doc_number": payload["doc_number"],
            "type": order_block["type"],
        },
    )
    # El job de impresión vive en la MISMA transacción: sin cobro, sin papel.
    # Sin impresora configurada no encola y no falla (payload queda impreso
    # en el historial, reimprimible).
    await printing_service.enqueue_document(
        session, kind=PrintJobKind.ticket, payload=payload, doc_key=str(ticket.id)
    )
    return ticket


async def get_ticket(session: AsyncSession, ticket_id: UUID) -> Ticket:
    ticket = await repo.get_ticket(session, ticket_id)
    if ticket is None:
        raise _not_found("Ticket no encontrado")
    return ticket


async def reprint_ticket(session: AsyncSession, principal: Principal, ticket_id: UUID) -> Ticket:
    """Reimpresión de un ticket histórico: contador y fecha actualizados; el
    payload NO se regenera (snapshot congelado en el momento de emisión)."""

    ticket = await get_ticket(session, ticket_id)
    ticket.printed_at = _utcnow()
    ticket.reprint_count += 1
    await auth_repo.record_audit(
        session,
        action="documents.ticket_reprinted",
        entity="ticket",
        user_id=principal.user_id,
        entity_id=ticket.id,
        after_data={"reprint_count": ticket.reprint_count},
    )
    await session.commit()
    return ticket


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------
def _invoice_doc_number(invoice: Invoice) -> str:
    return docs.format_invoice_number(invoice.series, invoice.year, invoice.number)


def _customer_render(customer) -> dict:
    return {
        "id": str(customer.id),
        "name": customer.name,
        "tax_id": customer.tax_id,
        "address": customer.address or "",
        "city": customer.city or "",
        "postal_code": customer.postal_code or "",
    }


async def _order_block(
    session: AsyncSession, order: Order, *, negate: bool = False
) -> dict:
    """Bloque de una venta dentro del payload de factura: referencia de su
    ticket, líneas y pagos. Con ``negate`` (rectificativa total) se invierten
    cantidad e importes, no el precio unitario ni el tipo de IVA."""

    def _neg(text: str) -> str:
        return format(-Decimal(text), "f") if negate else text

    lines = [
        {
            **_line_render(line),
            "quantity": _neg(format(line.quantity, "f")),
            "base": _neg(format(line.line_base, "f")),
            "total": _neg(format(line.line_total, "f")),
        }
        for line in await sales_repo.list_lines(session, order.id)
    ]
    payments = [
        {"code": method.code, "kind": method.kind.value, "amount": _money(payment.amount)}
        for payment, method in await sales_repo.list_payments_with_method(session, order.id)
    ]
    ticket = await repo.get_ticket_by_order(session, order.id)
    return {
        "order_id": str(order.id),
        "doc_number": docs.format_ticket_number(ticket.series, ticket.number)
        if ticket is not None
        else None,
        "total": _neg(_money(order.total_amount)),
        "lines": lines,
        "payments": payments,
    }


async def issue_invoice(
    session: AsyncSession, principal: Principal, *, order_ids: list[UUID]
) -> Invoice:
    """Emite una factura para 1..N ventas cobradas del mismo cliente.

    Reglas: todas las ventas existen, están cobradas y son positivas (las
    devoluciones no se facturan: se rectifican); todas tienen el mismo
    cliente, con NIF; ninguna está ya facturada. Todo en una transacción con
    las órdenes bloqueadas: dos facturaciones concurrentes del mismo lote,
    un solo ganador.
    """

    if not order_ids:
        raise _validation("La factura requiere al menos una venta")
    if len(set(order_ids)) != len(order_ids):
        raise _validation("La factura no admite ventas duplicadas")

    # El bloqueo ordena por id (canónico, sin deadlocks); el documento embebe
    # las ventas en orden cronológico (el del ticket), no el del UUID.
    locked = await repo.list_orders_for_update(session, order_ids)
    orders = sorted(locked, key=lambda order: (order.created_at, order.id))
    found = {order.id: order for order in orders}
    missing = [str(order_id) for order_id in order_ids if order_id not in found]
    if missing:
        raise _not_found("Venta no encontrada")

    customer_id: UUID | None = None
    for order in orders:
        if order.status != OrderStatus.paid:
            raise _conflict("Solo se pueden facturar ventas cobradas")
        if order.total_amount is None or order.total_amount <= 0:
            raise _conflict(
                "Las ventas a facturar deben ser positivas "
                "(las devoluciones se rectifican, no se facturan)"
            )
        if order.customer_id is None:
            raise _conflict("La venta no tiene cliente asignado")
        if customer_id is None:
            customer_id = order.customer_id
        elif customer_id != order.customer_id:
            raise _conflict("Todas las ventas de la factura deben ser del mismo cliente")

    invoiced = await repo.find_invoiced_order_ids(session, order_ids)
    if invoiced:
        raise _conflict("Alguna de las ventas ya está facturada")

    customer = await repo.get_customer(session, customer_id)
    if customer is None:
        raise _not_found("Cliente no encontrado")
    if not customer.tax_id:
        raise _validation("El cliente no tiene NIF: no se puede emitir factura")

    today = date.today()
    series = await _param_str(session, "invoices.series", "FAC")
    number = await repo.next_number(
        session, scope=SequenceScope.invoice, terminal_id=None, year=today.year, series=series
    )

    totals = docs.merge_tax_summaries([order.tax_summary or {} for order in orders])
    payload = docs.build_invoice_payload(
        doc_number=docs.format_invoice_number(series, today.year, number),
        series=series,
        year=today.year,
        number=number,
        issue_date=today.isoformat(),
        header=await _business_header(session),
        customer=_customer_render(customer),
        orders=[await _order_block(session, order) for order in orders],
        totals=totals,
    )
    invoice = Invoice(
        customer_id=customer_id,
        series=series,
        year=today.year,
        number=number,
        status=InvoiceStatus.issued,
        issue_date=today,
        total_base=Decimal(totals["base"]),
        total_tax=Decimal(totals["tax"]),
        total_amount=Decimal(totals["total"]),
        tax_summary=totals,
        payload=payload,
    )
    await repo.create_invoice(session, invoice=invoice, order_ids=order_ids)
    await auth_repo.record_audit(
        session,
        action="documents.invoice_issued",
        entity="invoice",
        user_id=principal.user_id,
        entity_id=invoice.id,
        after_data={
            "doc_number": payload["doc_number"],
            "customer_id": str(customer_id),
            "orders": len(order_ids),
            "total": payload["totals"]["total"],
        },
    )
    # Encola la impresión en la misma transacción (sin impresora: sin job, sin error).
    await printing_service.enqueue_document(
        session, kind=PrintJobKind.invoice, payload=payload, doc_key=str(invoice.id)
    )
    await session.commit()
    return invoice


async def get_invoice_detail(
    session: AsyncSession, invoice_id: UUID
) -> tuple[Invoice, list[InvoiceLine]]:
    invoice = await repo.get_invoice(session, invoice_id)
    if invoice is None:
        raise _not_found("Factura no encontrada")
    return invoice, await repo.list_invoice_lines(session, invoice_id)


async def void_invoice(
    session: AsyncSession, principal: Principal, invoice_id: UUID, *, reason: str
) -> Invoice:
    """Anula una factura emitida (motivo obligatorio); nunca se borra."""

    invoice = await repo.get_invoice(session, invoice_id)
    if invoice is None:
        raise _not_found("Factura no encontrada")
    if invoice.status == InvoiceStatus.voided:
        raise _conflict("La factura ya está anulada")

    invoice.status = InvoiceStatus.voided
    invoice.voided_at = _utcnow()
    invoice.void_reason = reason
    await auth_repo.record_audit(
        session,
        action="documents.invoice_voided",
        entity="invoice",
        user_id=principal.user_id,
        entity_id=invoice.id,
        after_data={
            "doc_number": _invoice_doc_number(invoice),
            "reason": reason,
        },
    )
    await session.commit()
    return invoice


# ---------------------------------------------------------------------------
# Rectificativas
# ---------------------------------------------------------------------------
async def issue_rectification(
    session: AsyncSession,
    principal: Principal,
    invoice_id: UUID,
    *,
    mode: str,
    refund_order_id: UUID | None,
    reason: str,
) -> Invoice:
    """Emite una factura rectificativa de ``invoice`` (importes negativos).

    - ``partial``: rectifica una devolución — importes de la orden negativa y
      línea ``invoice_lines`` hacia ella (una devolución, una rectificativa).
    - ``total``: anula el importe completo — copia el desglose invertido de la
      original; sin líneas ``invoice_lines`` (esas órdenes ya están en la
      factura original, UNIQUE por venta).
    """

    original = await repo.get_invoice(session, invoice_id)
    if original is None:
        raise _not_found("Factura no encontrada")
    if original.status == InvoiceStatus.voided:
        raise _conflict("No se rectifica una factura anulada")

    today = date.today()
    series = await _param_str(session, "invoices.rect_series", "R")
    number = await repo.next_number(
        session, scope=SequenceScope.invoice, terminal_id=None, year=today.year, series=series
    )
    doc_number = docs.format_invoice_number(series, today.year, number)

    if mode == docs.RECTIFICATION_PARTIAL:
        if refund_order_id is None:
            raise _validation("La rectificativa parcial requiere la orden de devolución")
        refund = await repo.get_refund_by_refund_order(session, refund_order_id)
        if refund is None:
            raise _not_found("Devolución no encontrada")
        original_order_ids = {
            line.order_id for line in await repo.list_invoice_lines(session, original.id)
        }
        if refund.original_order_id not in original_order_ids:
            raise _validation(
                "La devolución indicada no corresponde a una venta de esta factura"
            )
        refund_order = await sales_repo.get_order(session, refund_order_id)
        if refund_order is None:
            raise _not_found("Devolución no encontrada")
        totals = refund_order.tax_summary or {}
        orders_block = [await _order_block(session, refund_order)]
        line_order_ids = [refund_order.id]
    elif mode == docs.RECTIFICATION_TOTAL:
        if refund_order_id is not None:
            raise _validation("La rectificativa total no admite orden de devolución")
        totals = docs.negate_tax_summary(original.tax_summary)
        # Mismo criterio que issue_invoice: cronológico en el documento,
        # id para el bloqueo.
        refund_orders = await repo.list_orders_for_update(
            session, [line.order_id for line in await repo.list_invoice_lines(session, original.id)]
        )
        orders_block = [
            await _order_block(session, order, negate=True)
            for order in sorted(refund_orders, key=lambda order: (order.created_at, order.id))
        ]
        line_order_ids = []
    else:
        raise _validation("Modo de rectificación desconocido")

    payload = docs.build_invoice_payload(
        doc_number=doc_number,
        series=series,
        year=today.year,
        number=number,
        issue_date=today.isoformat(),
        header=await _business_header(session),
        customer=_customer_render(await repo.get_customer(session, original.customer_id)),
        orders=orders_block,
        totals=totals,
        rectifies={
            "invoice_id": str(original.id),
            "doc_number": _invoice_doc_number(original),
            "mode": mode,
            "reason": reason,
        },
    )
    rectification = Invoice(
        customer_id=original.customer_id,
        series=series,
        year=today.year,
        number=number,
        status=InvoiceStatus.issued,
        issue_date=today,
        total_base=Decimal(totals.get("base", "0")),
        total_tax=Decimal(totals.get("tax", "0")),
        total_amount=Decimal(totals.get("total", "0")),
        tax_summary=totals,
        payload=payload,
        rectified_invoice_id=original.id,
    )
    await repo.create_invoice(session, invoice=rectification, order_ids=line_order_ids)
    await auth_repo.record_audit(
        session,
        action="documents.invoice_rectified",
        entity="invoice",
        user_id=principal.user_id,
        entity_id=rectification.id,
        after_data={
            "doc_number": doc_number,
            "rectified": _invoice_doc_number(original),
            "mode": mode,
            "total": payload["totals"]["total"],
            "reason": reason,
        },
    )
    # La rectificativa se imprime como factura (misma tabla, mismo job kind).
    await printing_service.enqueue_document(
        session, kind=PrintJobKind.invoice, payload=payload, doc_key=str(rectification.id)
    )
    await session.commit()
    return rectification

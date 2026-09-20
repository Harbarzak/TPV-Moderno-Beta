"""Documentos (fase 09): ``/api/v1/documents`` + configuración en ``/api/v1/admin``.

- ``GET  /documents/tickets/{id}``                  ticket (payload congelado)
- ``POST /documents/tickets/{id}/reprint``          reimpresión (contador)
- ``POST /documents/invoices``                      factura de 1..N ventas cobradas
- ``GET  /documents/invoices/{id}``                 factura (payload congelado)
- ``POST /documents/invoices/{id}/void``            anulación con motivo
- ``POST /documents/invoices/{id}/rectifications``  rectificativa parcial/total
- ``GET/PATCH /admin/business-settings``            cabecera fiscal (Parámetros)
- ``PUT/DELETE /admin/logo``                        logo de cabecera (fichero en volumen)

El ticket NO se pide aquí: se emite automáticamente en el cobro
(``POST /sales/orders/{id}/close``) y en la devolución, y su respuesta lleva
``ticket_id``. El dinero viaja SIEMPRE como string (nunca float, §3).

Permisos: tickets ``tickets.reprint`` (camarero y jefe: reimprimir históricos),
facturas ``invoices.issue``, anulación ``invoices.void``, configuración
``admin.parameters``. Auditoría: ``documents.*``.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status as http_status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.db.models.sales import Invoice, InvoiceLine, Ticket
from app.services import documents as documents_service

router = APIRouter(prefix="/documents", tags=["documents"])
admin_router = APIRouter(prefix="/admin", tags=["documents"])

_reprint = [Depends(require_permission("tickets.reprint"))]
_issue = [Depends(require_permission("invoices.issue"))]
_void = [Depends(require_permission("invoices.void"))]
_manage = [Depends(require_permission("admin.parameters"))]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class InvoiceCreate(BaseModel):
    order_ids: list[UUID] = Field(min_length=1, max_length=100)


class InvoiceVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class RectificationCreate(BaseModel):
    mode: Literal["partial", "total"]
    refund_order_id: UUID | None = None  # obligatoria en «partial»
    reason: str = Field(min_length=1, max_length=500)


class BusinessSettingsUpdate(BaseModel):
    """Datos fiscales de cabecera (Configuración/Parámetros). Solo los campos
    enviados se actualizan."""

    name: str | None = Field(default=None, min_length=0, max_length=200)
    tax_id: str | None = Field(default=None, min_length=0, max_length=32)
    address: str | None = Field(default=None, min_length=0, max_length=300)
    phone: str | None = Field(default=None, min_length=0, max_length=32)


class LogoUpload(BaseModel):
    mime: Literal["image/png", "image/jpeg"]
    data: str  # base64 (sin el prefijo data:)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
def _money(value) -> str:
    return format(value, "f")


class TicketResponse(BaseModel):
    id: UUID
    order_id: UUID
    terminal_id: UUID
    series: str
    number: int
    doc_number: str
    printed_at: datetime | None
    reprint_count: int
    created_at: datetime
    payload: dict


class InvoiceLineResponse(BaseModel):
    order_id: UUID


class InvoiceResponse(BaseModel):
    id: UUID
    customer_id: UUID
    series: str
    year: int
    number: int
    doc_number: str
    status: str
    issue_date: date
    total_base: str
    total_tax: str
    total_amount: str
    rectified_invoice_id: UUID | None
    voided_at: datetime | None
    void_reason: str | None
    created_at: datetime
    payload: dict
    lines: list[InvoiceLineResponse]


class BusinessSettingsResponse(BaseModel):
    business: dict[str, str]
    logo: dict | None
    series: dict[str, str]
    currency: dict[str, str]


def _ticket_response(ticket: Ticket) -> TicketResponse:
    return TicketResponse(
        id=ticket.id,
        order_id=ticket.order_id,
        terminal_id=ticket.terminal_id,
        series=ticket.series,
        number=ticket.number,
        doc_number=ticket.payload.get("doc_number", ""),
        printed_at=ticket.printed_at,
        reprint_count=ticket.reprint_count,
        created_at=ticket.created_at,
        payload=ticket.payload,
    )


def _invoice_response(
    invoice: Invoice, lines: list[InvoiceLine] | None = None
) -> InvoiceResponse:
    return InvoiceResponse(
        id=invoice.id,
        customer_id=invoice.customer_id,
        series=invoice.series,
        year=invoice.year,
        number=invoice.number,
        doc_number=invoice.payload.get("doc_number", ""),
        status=invoice.status.value,
        issue_date=invoice.issue_date,
        total_base=_money(invoice.total_base),
        total_tax=_money(invoice.total_tax),
        total_amount=_money(invoice.total_amount),
        rectified_invoice_id=invoice.rectified_invoice_id,
        voided_at=invoice.voided_at,
        void_reason=invoice.void_reason,
        created_at=invoice.created_at,
        payload=invoice.payload,
        lines=[InvoiceLineResponse(order_id=line.order_id) for line in (lines or [])],
    )


# ---------------------------------------------------------------------------
# Tickets (la emisión vive en el cobro: POST /sales/orders/{id}/close)
# ---------------------------------------------------------------------------
@router.get("/tickets/{ticket_id}", dependencies=_reprint, response_model=TicketResponse)
async def get_ticket(ticket_id: UUID, session: DbSession, user: CurrentUser):
    ticket = await documents_service.get_ticket(session, ticket_id)
    return _ticket_response(ticket)


@router.post("/tickets/{ticket_id}/reprint", dependencies=_reprint,
             response_model=TicketResponse)
async def reprint_ticket(ticket_id: UUID, session: DbSession, user: CurrentUser):
    ticket = await documents_service.reprint_ticket(session, user, ticket_id)
    return _ticket_response(ticket)


# ---------------------------------------------------------------------------
# Facturas
# ---------------------------------------------------------------------------
@router.post("/invoices", dependencies=_issue, response_model=InvoiceResponse,
             status_code=http_status.HTTP_201_CREATED)
async def issue_invoice(payload: InvoiceCreate, session: DbSession, user: CurrentUser):
    invoice = await documents_service.issue_invoice(session, user, order_ids=payload.order_ids)
    # La respuesta de emisión lleva las líneas: el documento acaba de nacer con ellas.
    _, lines = await documents_service.get_invoice_detail(session, invoice.id)
    return _invoice_response(invoice, lines)


@router.get("/invoices/{invoice_id}", dependencies=_issue, response_model=InvoiceResponse)
async def get_invoice(invoice_id: UUID, session: DbSession, user: CurrentUser):
    invoice, lines = await documents_service.get_invoice_detail(session, invoice_id)
    return _invoice_response(invoice, lines)


@router.post("/invoices/{invoice_id}/void", dependencies=_void,
             response_model=InvoiceResponse)
async def void_invoice(invoice_id: UUID, payload: InvoiceVoid,
                       session: DbSession, user: CurrentUser):
    invoice = await documents_service.void_invoice(
        session, user, invoice_id, reason=payload.reason
    )
    _, lines = await documents_service.get_invoice_detail(session, invoice.id)
    return _invoice_response(invoice, lines)  # anular no toca las líneas


@router.post("/invoices/{invoice_id}/rectifications", dependencies=_issue,
             response_model=InvoiceResponse, status_code=http_status.HTTP_201_CREATED)
async def issue_rectification(invoice_id: UUID, payload: RectificationCreate,
                              session: DbSession, user: CurrentUser):
    rectification = await documents_service.issue_rectification(
        session,
        user,
        invoice_id,
        mode=payload.mode,
        refund_order_id=payload.refund_order_id,
        reason=payload.reason,
    )
    # Parcial: la línea hacia la devolución. Total: vacía por diseño (UNIQUE
    # por venta: esas órdenes ya están en la original).
    _, lines = await documents_service.get_invoice_detail(session, rectification.id)
    return _invoice_response(rectification, lines)


# ---------------------------------------------------------------------------
# Configuración de documentos (Configuración/Parámetros)
# ---------------------------------------------------------------------------
@admin_router.get("/business-settings", dependencies=_manage,
                  response_model=BusinessSettingsResponse)
async def get_business_settings(session: DbSession, user: CurrentUser):
    return await documents_service.get_business_settings(session)


@admin_router.patch("/business-settings", dependencies=_manage,
                    response_model=BusinessSettingsResponse)
async def update_business_settings(payload: BusinessSettingsUpdate,
                                   session: DbSession, user: CurrentUser):
    fields = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    return await documents_service.update_business_settings(session, user, fields=fields)


@admin_router.put("/logo", dependencies=_manage, response_model=BusinessSettingsResponse)
async def upload_logo(payload: LogoUpload, session: DbSession, user: CurrentUser):
    await documents_service.upload_logo(session, user, mime=payload.mime, data_b64=payload.data)
    return await documents_service.get_business_settings(session)


@admin_router.delete("/logo", dependencies=_manage,
                     status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_logo(session: DbSession, user: CurrentUser):
    await documents_service.delete_logo(session, user)

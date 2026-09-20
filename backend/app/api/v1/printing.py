"""Impresión (fase 10): cola en ``/api/v1/printing`` e impresoras en ``/api/v1/admin``.

- CRUD de impresoras + job de prueba (``admin.printers``): de red (ESC/POS
  ``host:port`` 9100) o de agente (tpv-agent); una default activa por tipo
  (tickets/cocina/facturas); ancho térmico 32/42/48 columnas.
- Cola: listar, despachar, confirmar impresión, recuperar y cancelar jobs.
- Copias: ``POST /printing/copies/tickets/{id}`` (``tickets.reprint``) y
  ``POST /printing/copies/invoices/{id}`` (``invoices.issue``) reenvían el
  payload congelado sin regenerar nada.

Los drivers reales (térmica/Windows/agente) llegan en la fase 13: hoy
``app.state.print_adapter`` es un ``NullPrinterAdapter``.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from pydantic import BaseModel, Field

from app.adapters.printing import PrinterAdapter
from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.db.enums import PrinterConn, PrinterKind, PrintJobStatus
from app.db.models.printing import Printer, PrintJob
from app.repos import printing as printing_repo
from app.services import printing as service

router = APIRouter(prefix="/printing", tags=["printing"])
admin_router = APIRouter(prefix="/admin", tags=["printing"])

_manage = [Depends(require_permission("admin.printers"))]
_reprint = [Depends(require_permission("tickets.reprint"))]
_issue = [Depends(require_permission("invoices.issue"))]


# ---------------------------------------------------------------------------
# Peticiones
# ---------------------------------------------------------------------------
class PrinterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: PrinterKind
    connection: PrinterConn
    address: str | None = Field(
        None, min_length=3, max_length=120, description="host:puerto (solo red, ej. 9100)"
    )
    device_id: UUID | None = None  # obligatorio en agente
    width_chars: Literal[32, 42, 48] = 42
    is_default: bool = False


class PrinterUpdate(BaseModel):
    """Solo los campos enviados se actualizan (kind/connection inmutables)."""

    name: str | None = Field(None, min_length=1, max_length=80)
    address: str | None = Field(None, min_length=3, max_length=120)
    width_chars: Literal[32, 42, 48] | None = None
    is_default: bool | None = None
    active: bool | None = None


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class PrinterResponse(BaseModel):
    id: UUID
    name: str
    kind: str
    connection: str
    address: str | None
    device_id: UUID | None
    width_chars: int
    is_default: bool
    active: bool
    created_at: datetime
    updated_at: datetime


class PrintJobResponse(BaseModel):
    id: UUID
    printer_id: UUID
    kind: str
    status: str
    attempts: int
    last_error: str | None
    dedupe_key: str | None
    payload: dict
    created_at: datetime
    sent_at: datetime | None
    printed_at: datetime | None


class PrintJobListResponse(BaseModel):
    items: list[PrintJobResponse]


class PrinterListResponse(BaseModel):
    items: list[PrinterResponse]


class DispatchResponse(BaseModel):
    results: list[dict]


def _printer_response(printer: Printer) -> PrinterResponse:
    return PrinterResponse(
        id=printer.id,
        name=printer.name,
        kind=printer.kind.value,
        connection=printer.connection.value,
        address=printer.address,
        device_id=printer.device_id,
        width_chars=printer.width_chars,
        is_default=printer.is_default,
        active=printer.active,
        created_at=printer.created_at,
        updated_at=printer.updated_at,
    )


def _job_response(job: PrintJob) -> PrintJobResponse:
    return PrintJobResponse(
        id=job.id,
        printer_id=job.printer_id,
        kind=job.kind.value,
        status=job.status.value,
        attempts=job.attempts,
        last_error=job.last_error,
        dedupe_key=job.dedupe_key,
        payload=job.payload,
        created_at=job.created_at,
        sent_at=job.sent_at,
        printed_at=job.printed_at,
    )


def _adapter(request: Request) -> PrinterAdapter:
    """Adaptador registrado por la app (sustituible en tests/fase 13)."""

    return request.app.state.print_adapter


Adapter = Annotated[PrinterAdapter, Depends(_adapter)]

# ---------------------------------------------------------------------------
# Impresoras (administración)
# ---------------------------------------------------------------------------
@admin_router.get("/printers", response_model=PrinterListResponse, dependencies=_manage)
async def list_printers(
    session: DbSession,
    include_inactive: bool = Query(False, description="Incluir dadas de baja"),
) -> PrinterListResponse:
    printers = await printing_repo.list_printers(session, include_inactive=include_inactive)
    return PrinterListResponse(items=[_printer_response(p) for p in printers])


@admin_router.post(
    "/printers",
    response_model=PrinterResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=_manage,
)
async def create_printer(session: DbSession, user: CurrentUser, body: PrinterCreate):
    printer = await service.create_printer(
        session,
        user,
        name=body.name,
        kind=body.kind,
        connection=body.connection,
        address=body.address,
        device_id=body.device_id,
        width_chars=body.width_chars,
        is_default=body.is_default,
    )
    return _printer_response(printer)


@admin_router.patch("/printers/{printer_id}", response_model=PrinterResponse, dependencies=_manage)
async def update_printer(
    session: DbSession, user: CurrentUser, printer_id: UUID, body: PrinterUpdate
):
    fields = body.model_dump(exclude_unset=True)
    printer = await service.update_printer(session, user, printer_id, fields=fields)
    return _printer_response(printer)


@admin_router.delete(
    "/printers/{printer_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_manage
)
async def deactivate_printer(
    session: DbSession, user: CurrentUser, printer_id: UUID
) -> None:
    await service.deactivate_printer(session, user, printer_id)


@admin_router.post(
    "/printers/{printer_id}/test",
    response_model=PrintJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=_manage,
)
async def enqueue_test_job(session: DbSession, user: CurrentUser, printer_id: UUID):
    job = await service.enqueue_test_job(session, user, printer_id)
    return _job_response(job)


# ---------------------------------------------------------------------------
# Cola de trabajos
# ---------------------------------------------------------------------------
@router.get("/jobs", response_model=PrintJobListResponse, dependencies=_manage)
async def list_jobs(
    session: DbSession,
    printer_id: UUID | None = None,
    job_status: PrintJobStatus | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> PrintJobListResponse:
    jobs = await printing_repo.list_jobs(
        session, status=job_status, printer_id=printer_id, limit=limit
    )
    return PrintJobListResponse(items=[_job_response(j) for j in jobs])


@router.get("/jobs/{job_id}", response_model=PrintJobResponse, dependencies=_manage)
async def get_job(session: DbSession, job_id: UUID) -> PrintJobResponse:
    return _job_response(await service.get_job_or_404(session, job_id))


@router.post("/jobs/{job_id}/retry", response_model=PrintJobResponse, dependencies=_manage)
async def retry_job(session: DbSession, user: CurrentUser, job_id: UUID) -> PrintJobResponse:
    queue = service.PrintQueue(session)
    job = await queue.retry_job(user, job_id)
    await session.commit()
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=PrintJobResponse, dependencies=_manage)
async def cancel_job(session: DbSession, user: CurrentUser, job_id: UUID) -> PrintJobResponse:
    queue = service.PrintQueue(session)
    job = await queue.cancel_job(user, job_id)
    await session.commit()
    return _job_response(job)


@router.post("/jobs/{job_id}/confirm", response_model=PrintJobResponse, dependencies=_manage)
async def confirm_printed(session: DbSession, job_id: UUID) -> PrintJobResponse:
    queue = service.PrintQueue(session)
    job = await queue.confirm_printed(job_id)
    await session.commit()
    return _job_response(job)


@router.post("/dispatch", response_model=DispatchResponse, dependencies=_manage)
async def dispatch_pending(
    session: DbSession,
    adapter: Adapter,
    limit: int = Query(20, ge=1, le=100),
) -> DispatchResponse:
    """Mueve la cola: reintentos con backoff vencido + entrega de encolados."""

    results = await service.PrintQueue(session, adapter).dispatch_pending(limit=limit)
    await session.commit()
    return DispatchResponse(results=results)


# ---------------------------------------------------------------------------
# Copias de documentos ya emitidos (payload congelado, sin dedupe)
# ---------------------------------------------------------------------------
@router.post(
    "/copies/tickets/{ticket_id}",
    response_model=PrintJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=_reprint,
)
async def copy_ticket(
    session: DbSession, user: CurrentUser, ticket_id: UUID
) -> PrintJobResponse:
    job = await service.enqueue_ticket_copy(session, user, ticket_id)
    return _job_response(job)


@router.post(
    "/copies/invoices/{invoice_id}",
    response_model=PrintJobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=_issue,
)
async def copy_invoice(
    session: DbSession, user: CurrentUser, invoice_id: UUID
) -> PrintJobResponse:
    job = await service.enqueue_invoice_copy(session, user, invoice_id)
    return _job_response(job)

"""Plugin fiscal (fase 34): ``/api/v1/fiscal``.

- ``GET  /fiscal/status``          proveedor activo + cola + documentos por estado
- ``GET  /fiscal/documents``       listado paginado de documentos fiscales
- ``GET  /fiscal/documents/{id}``  documento con su traza completa (auditoría)
- ``POST /fiscal/dispatch``        una pasada de descarga (consumir + enviar)

El plugin es admin-only: ``fiscal.view`` para consultar, ``fiscal.dispatch``
para descargar (semilla: manager y admin). El adaptador se elige en el
arranque con ``TPV_FISCAL_PROVIDER`` y vive en ``app.state.fiscal_adapter``;
el motor de ventas no pasa por aquí en ningún caso (ADR-010).

El dinero del ``payload`` ya viaja como string «X.XXX» (§3): se pasa tal cual,
el API fiscal nunca recalcula importes.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status as http_status
from pydantic import BaseModel, Field

from app.api.dependencies import CurrentUser, DbSession, require_permission
from app.core.errors import AppError, ErrorCode
from app.db.enums import FiscalDocumentStatus
from app.repos import fiscal as repo
from app.services import fiscal as fiscal_service

router = APIRouter(prefix="/fiscal", tags=["fiscal"])

_view = [Depends(require_permission("fiscal.view"))]
_dispatch = [Depends(require_permission("fiscal.dispatch"))]


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class FiscalEventResponse(BaseModel):
    id: UUID
    kind: str
    detail: dict | None
    actor_user_id: UUID | None
    created_at: datetime


class FiscalDocumentResponse(BaseModel):
    id: UUID
    sale_event_id: UUID
    order_id: UUID
    doc_type: str
    provider: str
    status: str
    payload: dict          # snapshot fiscal íntegro (dinero string, §3)
    external_ref: str | None
    error_code: str | None
    error_message: str | None
    attempts: int
    created_at: datetime


class FiscalDocumentDetailResponse(FiscalDocumentResponse):
    events: list[FiscalEventResponse]


class FiscalDocumentListResponse(BaseModel):
    items: list[FiscalDocumentResponse]
    total: int


class FiscalStatusResponse(BaseModel):
    provider: str
    pending_sale_events: int
    documents: dict[str, int]   # por estado del enum, con ceros


class DispatchResponse(BaseModel):
    provider: str
    consumed: int
    created: int
    submitted: int
    accepted: int
    rejected: int
    details: list[dict] = Field(default_factory=list)


def _document_response(document) -> FiscalDocumentResponse:
    return FiscalDocumentResponse(
        id=document.id,
        sale_event_id=document.sale_event_id,
        order_id=document.order_id,
        doc_type=document.doc_type.value,
        provider=document.provider,
        status=document.status.value,
        payload=document.payload,
        external_ref=document.external_ref,
        error_code=document.error_code,
        error_message=document.error_message,
        attempts=document.attempts,
        created_at=document.created_at,
    )


# ---------------------------------------------------------------------------
# Estado y consulta
# ---------------------------------------------------------------------------
@router.get("/status", response_model=FiscalStatusResponse, dependencies=_view)
async def get_status(request: Request, session: DbSession) -> FiscalStatusResponse:
    """Proveedor activo, cola del outbox y documentos por estado."""
    data = await fiscal_service.fiscal_status(
        session, request.app.state.fiscal_adapter
    )
    return FiscalStatusResponse(**data)


@router.get("/documents", response_model=FiscalDocumentListResponse, dependencies=_view)
async def list_documents(
    session: DbSession,
    status: FiscalDocumentStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FiscalDocumentListResponse:
    """Listado paginado de documentos fiscales (más nuevos primero)."""
    documents, total = await repo.list_documents(
        session, status=status, limit=limit, offset=offset
    )
    return FiscalDocumentListResponse(
        items=[_document_response(document) for document in documents], total=total
    )


@router.get(
    "/documents/{document_id}",
    response_model=FiscalDocumentDetailResponse,
    dependencies=_view,
)
async def get_document(
    document_id: UUID, session: DbSession
) -> FiscalDocumentDetailResponse:
    """Documento con su traza completa (``fiscal_events``, append-only)."""
    document = await repo.get_document_with_events(session, document_id)
    if document is None:
        raise AppError(404, ErrorCode.NOT_FOUND, "Documento fiscal no encontrado")
    return FiscalDocumentDetailResponse(
        **_document_response(document).model_dump(),
        events=[
            FiscalEventResponse(
                id=event.id,
                kind=event.kind.value,
                detail=event.detail,
                actor_user_id=event.actor_user_id,
                created_at=event.created_at,
            )
            for event in document.events
        ],
    )


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
@router.post(
    "/dispatch",
    response_model=DispatchResponse,
    status_code=http_status.HTTP_200_OK,
    dependencies=_dispatch,
)
async def dispatch(
    request: Request, session: DbSession, user: CurrentUser
) -> DispatchResponse:
    """Una pasada: consume ``sale_events`` pendientes y envía los documentos.

    Idempotente y seguro de repetir (outbox + UNIQUE por evento). Idóneo para
    un temporizador externo (cron) con credenciales de servicio.
    """
    report = await fiscal_service.dispatch_pending(
        session,
        request.app.state.fiscal_adapter,
        actor_user_id=user.user_id,
    )
    return DispatchResponse(**report.as_dict())

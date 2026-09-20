"""Servicio del plugin fiscal (fase 34): consume ``sale_events`` y certifica.

Integración (ADR-010) — el motor de ventas NO cambia:

1. **Outbox** (fase 06): cada cierre/anulación/devolución ya dejó su evento
   genérico en ``sale_events`` con ``dispatched_at IS NULL``. Este servicio
   los reclama con ``FOR UPDATE SKIP LOCKED`` (dos descargas simultáneas no
   se pisan) y, salvo que el proveedor sea ``none``, crea UN ``FiscalDocument``
   por evento (UNIQUE en ``sale_event_id``: idempotente aunque el proceso se
   repita) con el snapshot del dominio puro.
2. **Envío**: cada documento ``pending`` se entrega al
   :class:`~app.adapters.fiscal.FiscalAdapter` seleccionado en configuración.
   Aceptado → ``accepted`` + referencia externa; rechazado → ``rejected`` y
   queda reencolable; error del adaptador → sigue ``pending`` con ``attempts``
   incrementado y el diagnóstico en la traza. El envío NUNCA revierte lo ya
   consumido: el reintento vive a nivel de DOCUMENTO, no de evento.
3. **Auditoría total**: cada paso deja su fila append-only en
   ``fiscal_events`` (queued/dispatched/accepted/rejected) y cada descarga un
   resumen en ``audit_log`` (``fiscal.dispatch``).

Disparo: ``POST /api/v1/fiscal/dispatch`` (permiso ``fiscal.dispatch``) desde
el panel de administración o un temporizador externo. Al ser un outbox
transaccional, ejecutarlo tarde o dos veces nunca pierde ni duplica nada.
"""

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.fiscal import (
    FiscalAdapter,
    FiscalError,
    FiscalResult,
    FiscalSubmission,
)
from app.core.logging import get_logger
from app.db.enums import FiscalDocumentStatus, FiscalEventKind
from app.db.models.fiscal import FiscalDocument
from app.domain import fiscal as domain
from app.repos import auth as auth_repo
from app.repos import fiscal as repo

logger = get_logger("tpv.fiscal")


@dataclass(slots=True)
class DispatchReport:
    """Resumen de una descarga: va a la respuesta del API y a ``audit_log``.

    Mutable a propósito: ``dispatch_pending`` lo rellena a medida que avanza
    la pasada y al final viaja congelado en la respuesta y en auditoría.
    """

    provider: str
    consumed: int = 0     # sale_events consumidos (outbox)
    created: int = 0      # documentos fiscales creados
    submitted: int = 0    # documentos entregados al adaptador
    accepted: int = 0
    rejected: int = 0
    details: list[dict] = field(default_factory=list)  # diagnóstico por rechazo

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "consumed": self.consumed,
            "created": self.created,
            "submitted": self.submitted,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "details": self.details,
        }


async def dispatch_pending(
    session: AsyncSession,
    adapter: FiscalAdapter,
    *,
    actor_user_id=None,
    limit: int = 100,
) -> DispatchReport:
    """Una pasada de descarga: consumir outbox y enviar documentos pendientes.

    Todo en UNA transacción: o la descarga completa queda trazada o nada lo
    está. Con un adaptador real (I/O externo) el envío por documento deberá
    confirmarse en su propia transacción (el outbox lo permite: el estado del
    documento ya está persistido antes de llamar al adaptador); los esqueletos
    actuales no hacen I/O, así que la transacción única es correcta hoy.
    """

    report = DispatchReport(provider=adapter.name)

    # --- Paso A: consumir eventos de venta (outbox del motor) ---------------
    for event in await repo.claim_pending_sale_events(session, limit=limit):
        if adapter.name != "none":
            already = await repo.get_document_by_event(session, event.id)
            if already is None:
                snapshot = domain.build_payload(
                    event.event_type.value, event.payload, occurred_at=event.created_at
                )
                document = repo.create_document(
                    session,
                    sale_event_id=event.id,
                    order_id=event.order_id,
                    provider=adapter.name,
                    snapshot=snapshot,
                )
                await session.flush()  # asigna document.id antes de trazarlo
                repo.add_fiscal_event(
                    session,
                    document,
                    kind=FiscalEventKind.queued,
                    detail={"sale_event_id": str(event.id)},
                    actor_user_id=actor_user_id,
                )
                report.created += 1
        repo.mark_sale_event_dispatched(session, event)
        report.consumed += 1

    # --- Paso B: enviar los documentos pendientes ---------------------------
    if adapter.name != "none":
        for document in await repo.claim_retryable_documents(session, limit=limit):
            result = await _submit_document(session, adapter, document, actor_user_id)
            report.submitted += 1
            if result.accepted:
                report.accepted += 1
            else:
                report.rejected += 1
                if result.detail:
                    report.details.append({"document_id": str(document.id), **result.detail})

    await auth_repo.record_audit(
        session,
        action="fiscal.dispatch",
        entity="fiscal_document",
        user_id=actor_user_id,
        after_data=report.as_dict(),
    )
    await session.commit()
    logger.info(
        "fiscal.dispatch",
        provider=report.provider,
        consumed=report.consumed,
        created=report.created,
        submitted=report.submitted,
        accepted=report.accepted,
        rejected=report.rejected,
    )
    return report


async def _submit_document(
    session: AsyncSession,
    adapter: FiscalAdapter,
    document: FiscalDocument,
    actor_user_id,
) -> FiscalResult:
    """Un envío: traza, llama al adaptador y aplica la máquina de estados."""

    repo.add_fiscal_event(
        session, document, kind=FiscalEventKind.dispatched, actor_user_id=actor_user_id
    )
    domain.ensure_transition(document.status, FiscalDocumentStatus.sent)
    document.status = FiscalDocumentStatus.sent
    document.attempts += 1

    submission = FiscalSubmission(
        document_id=document.id,
        order_id=document.order_id,
        doc_type=document.doc_type.value,
        payload=document.payload,
        attempts=document.attempts,
    )
    try:
        result = await adapter.submit(submission)
    except FiscalError as exc:
        # Rechazo del adaptador (incluido «no desarrollado»): el documento
        # vuelve a pending para reencolar cuando el régimen se active.
        document.status = FiscalDocumentStatus.pending
        document.error_code = exc.code
        document.error_message = str(exc)
        repo.add_fiscal_event(
            session,
            document,
            kind=FiscalEventKind.rejected,
            detail={"code": exc.code, "error": str(exc)},
            actor_user_id=actor_user_id,
        )
        return FiscalResult(accepted=False, detail={"code": exc.code, "error": str(exc)})

    if result.accepted:
        domain.ensure_transition(document.status, FiscalDocumentStatus.accepted)
        document.status = FiscalDocumentStatus.accepted
        document.external_ref = result.external_ref
        document.error_code = None
        document.error_message = None
        repo.add_fiscal_event(
            session,
            document,
            kind=FiscalEventKind.accepted,
            detail={"external_ref": result.external_ref} if result.external_ref else None,
            actor_user_id=actor_user_id,
        )
    else:
        domain.ensure_transition(document.status, FiscalDocumentStatus.rejected)
        document.status = FiscalDocumentStatus.rejected
        detail = result.detail or {}
        document.error_code = str(detail.get("code", "FISCAL_REJECTED"))
        document.error_message = str(detail.get("error", "Rechazado por el régimen fiscal"))
        repo.add_fiscal_event(
            session,
            document,
            kind=FiscalEventKind.rejected,
            detail=detail or None,
            actor_user_id=actor_user_id,
        )
    return result


async def fiscal_status(session: AsyncSession, adapter: FiscalAdapter) -> dict:
    """Estado del plugin: proveedor activo, cola pendiente y documentos."""

    return {
        "provider": adapter.name,
        "pending_sale_events": await repo.count_pending_sale_events(session),
        "documents": await repo.count_documents_by_status(session),
    }

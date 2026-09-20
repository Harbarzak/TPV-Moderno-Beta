"""Cola de impresión (fase 10): ``PrintQueue`` sobre un ``PrinterAdapter``.

- ``PrintJob`` encola el payload CONGELADO del documento: la impresión nunca
  regenera datos ni toca ventas. Quien emite entrega el payload y punto
  (``services.documents`` → :func:`enqueue_document`, misma transacción: si
  el cobro revierte, el trabajo de impresión nunca existió).
- Estados (``domain.printing.can_transition``): ``queued → sent → printed |
  failed``. Los ``failed`` se reencolan solos mientras queden intentos
  (MAX_ATTEMPTS) y su backoff esté vencido; agotados, esperan reintento
  manual. ``dedupe_key`` hace idempotente la encolación de documentos.
- Trazabilidad: ``attempts``/``last_error``/``sent_at``/``printed_at`` en el
  job y auditoría ``printing.*`` en las acciones de operador.

El despachador en background (worker/WebSocket) llega con las fases 13-14;
hoy la cola se mueve con ``POST /api/v1/printing/dispatch``.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.printing import PrinterAdapter, PrinterError
from app.core.errors import AppError, ErrorCode
from app.db.enums import InvoiceStatus, PrinterConn, PrinterKind, PrintJobKind, PrintJobStatus
from app.db.models.printing import Printer, PrintJob
from app.db.models.security import Device
from app.domain import printing as domain
from app.repos import auth as auth_repo
from app.repos import documents as documents_repo
from app.repos import printing as repo
from app.services import events as events_service
from app.services.auth import Principal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _not_found(detail: str) -> AppError:
    return AppError(404, ErrorCode.NOT_FOUND, detail)


def _conflict(detail: str) -> AppError:
    return AppError(409, ErrorCode.CONFLICT, detail)


def _validation(detail: str) -> AppError:
    return AppError(422, ErrorCode.VALIDATION_ERROR, detail)


# ---------------------------------------------------------------------------
# Cola
# ---------------------------------------------------------------------------
class PrintQueue:
    """Operaciones de cola sobre una sesión y un adaptador de impresión.

    ``adapter`` solo hace falta para :meth:`dispatch_pending`; la encolación
    (que ocurre dentro de la transacción del documento) no toca hardware.
    """

    def __init__(self, session: AsyncSession, adapter: PrinterAdapter | None = None) -> None:
        self.session = session
        self.adapter = adapter

    # -- encolado ----------------------------------------------------------
    async def enqueue(
        self,
        *,
        printer: Printer,
        kind: PrintJobKind,
        payload: dict,
        dedupe_key: str | None = None,
    ) -> PrintJob:
        """Encola un trabajo en una impresora concreta."""

        if not printer.active:
            raise _conflict(f"La impresora «{printer.name}» no está activa")
        target_kind = domain.printer_kind_for_job(kind)
        if target_kind is not None and printer.kind != target_kind:
            raise _validation(
                f"Un job «{kind.value}» no se puede imprimir en una impresora"
                f" «{printer.kind.value}»"
            )
        job, _created = await repo.enqueue_job(
            self.session,
            printer_id=printer.id,
            kind=kind,
            payload=payload,
            dedupe_key=dedupe_key,
        )
        return job

    async def enqueue_document(
        self, *, kind: PrintJobKind, payload: dict, doc_key: str
    ) -> PrintJob | None:
        """Encola la impresión de un documento emitido.

        Elige la impresora por defecto del tipo asociado al job; si no hay
        ninguna configurada, no encola y no falla: emitir un documento nunca
        puede romperse por no haber impresora (el payload queda impreso en el
        historial y se puede reimprimir). Idempotente por ``dedupe_key``.
        """

        target_kind = domain.printer_kind_for_job(kind)
        if target_kind is None:
            return None
        printer = await repo.get_default_printer(self.session, target_kind)
        if printer is None:
            return None
        return await self.enqueue(
            printer=printer,
            kind=kind,
            payload=payload,
            dedupe_key=f"{kind.value}:{doc_key}",
        )

    async def enqueue_copy(
        self, *, kind: PrintJobKind, payload: dict, printer: Printer
    ) -> PrintJob:
        """Copia explícita (reimpresión): sin ``dedupe_key``, cada copia es
        un trabajo nuevo con el MISMO payload congelado."""

        return await self.enqueue(printer=printer, kind=kind, payload=payload, dedupe_key=None)

    # -- despacho ----------------------------------------------------------
    async def dispatch_pending(
        self, *, limit: int = 20, now: datetime | None = None
    ) -> list[dict]:
        """Reencola fallos con backoff vencido, reclama encolados y los
        entrega al adaptador. Éxito = ``sent`` (a la espera de confirmación
        de impresión); ``PrinterError`` = ``failed`` con ``last_error``."""

        if self.adapter is None:
            raise _validation("Sin adaptador de impresión registrado")
        now = now or _utcnow()

        # Reintentos automáticos: solo si quedan intentos y el backoff venció.
        for job in await repo.failed_jobs(self.session, limit=limit * 3):
            if job.attempts >= domain.MAX_ATTEMPTS or not domain.can_transition(
                job.status, PrintJobStatus.queued
            ):
                continue
            if not domain.retry_due(job.attempts, job.sent_at, now):
                continue
            repo.requeue(job)
        await self.session.flush()

        delivered: list[dict] = []
        for job in await repo.claimable_jobs(self.session, limit=limit):
            printer = await repo.get_printer(self.session, job.printer_id)
            repo.mark_sent(job, now=now)  # claim: cuenta el intento SIEMPRE
            await self.session.flush()
            if printer is None or not printer.active:
                # Falla con consumo de intento: tras MAX_ATTEMPTS el trabajo
                # descansa en 'failed' en vez de reencolar en bucle; un
                # operador lo recupera cuando la impresora vuelva.
                repo.mark_failed(job, error="La impresora no existe o no está activa")
                await self._notify_printer_down(job, printer)
            else:
                try:
                    await self.adapter.send(printer=printer, job=job)
                except PrinterError as exc:
                    repo.mark_failed(job, error=f"{exc.code}: {exc}")
                    await self._notify_printer_down(job, printer)
            delivered.append(self._snapshot(job))
        return delivered

    async def _notify_printer_down(self, job: PrintJob, printer: Printer | None) -> None:
        """Cola atascada (§9.1): un job agotó sus reintentos → notificación.

        Impresora de agente con terminal conocida → tema ``terminal:{id}`` (lo
        ve el TPV afectado); el resto → tema ``system`` (supervisión). Solo
        al agotarse (una vez por job): los reintentos ya están en la cola.
        Best-effort: el fallo ya está persistido; el aviso no rompe el turno.
        """
        if job.attempts < domain.MAX_ATTEMPTS:
            return
        terminal_id = (
            await self.session.scalar(
                select(Device.terminal_id).where(Device.id == printer.device_id)
            )
            if printer is not None and printer.device_id is not None
            else None
        )
        payload = {
            "printer_id": str(job.printer_id),
            "printer_name": printer.name if printer is not None else None,
            "job_id": str(job.id),
            "job_kind": job.kind.value,
            "attempts": job.attempts,
        }
        await events_service.record(
            self.session,
            topic=f"terminal:{terminal_id}" if terminal_id is not None else "system",
            type="printing.printer_down" if terminal_id is not None else "system.printer_down",
            payload=payload,
        )

    # -- transiciones explícitas ------------------------------------------
    async def confirm_printed(self, job_id: UUID, *, now: datetime | None = None) -> PrintJob:
        """Confirmación de impresión (sent→printed); típica del agente."""
        return await self._transition(job_id, PrintJobStatus.printed, now=now)

    async def retry_job(self, principal: Principal, job_id: UUID) -> PrintJob:
        """Recupera un ``failed`` agotado: nuevo ciclo completo de intentos,
        auditado (trazabilidad de quién reimpresiona)."""

        job = await repo.get_job_for_update(self.session, job_id)
        if job is None:
            raise _not_found("Trabajo de impresión no encontrado")
        if job.status != PrintJobStatus.failed:
            raise _conflict("Solo se pueden recuperar trabajos fallidos")
        repo.requeue(job)
        job.attempts = 0  # ciclo nuevo; el fallo previo queda en el histórico
        await auth_repo.record_audit(
            self.session,
            action="printing.job_retried",
            entity="print_job",
            user_id=principal.user_id,
            entity_id=job.id,
            after_data={"printer_id": str(job.printer_id), "kind": job.kind.value},
        )
        return job

    async def cancel_job(self, principal: Principal, job_id: UUID) -> PrintJob:
        """Cancela un trabajo pendiente/entregado/fallido; los terminales
        (printed/cancelled) ya no se tocan."""

        job = await repo.get_job_for_update(self.session, job_id)
        if job is None:
            raise _not_found("Trabajo de impresión no encontrado")
        if not domain.can_transition(job.status, PrintJobStatus.cancelled):
            raise _conflict(f"Un trabajo «{job.status.value}» no se puede cancelar")
        repo.cancel(job)
        await auth_repo.record_audit(
            self.session,
            action="printing.job_cancelled",
            entity="print_job",
            user_id=principal.user_id,
            entity_id=job.id,
            after_data={"printer_id": str(job.printer_id), "kind": job.kind.value},
        )
        return job

    async def _transition(
        self, job_id: UUID, target: PrintJobStatus, *, now: datetime | None = None
    ) -> PrintJob:
        job = await repo.get_job_for_update(self.session, job_id)
        if job is None:
            raise _not_found("Trabajo de impresión no encontrado")
        if not domain.can_transition(job.status, target):
            raise _conflict(
                f"Un trabajo «{job.status.value}» no puede pasar a «{target.value}»"
            )
        if target is PrintJobStatus.printed:
            repo.mark_printed(job, now=now or _utcnow())
        return job

    @staticmethod
    def _snapshot(job: PrintJob) -> dict:
        return {
            "id": str(job.id),
            "printer_id": str(job.printer_id),
            "kind": job.kind.value,
            "status": job.status.value,
            "attempts": job.attempts,
            "error": job.last_error,
        }


# ---------------------------------------------------------------------------
# Encolado desde documentos (hook) y copias
# ---------------------------------------------------------------------------
async def enqueue_document(
    session: AsyncSession, *, kind: PrintJobKind, payload: dict, doc_key: str
) -> PrintJob | None:
    """Punto de enganche para ``services.documents``: sin commit propio, el
    job vive en la transacción del documento que lo emite."""

    return await PrintQueue(session).enqueue_document(
        kind=kind, payload=payload, doc_key=doc_key
    )


async def enqueue_ticket_copy(
    session: AsyncSession, principal: Principal, ticket_id: UUID
) -> PrintJob:
    """Copia de un ticket: reenvía el payload congelado como job nuevo."""

    ticket = await documents_repo.get_ticket(session, ticket_id)
    if ticket is None:
        raise _not_found("Ticket no encontrado")
    printer = await repo.get_default_printer(session, PrinterKind.receipt)
    if printer is None:
        raise _conflict("No hay impresora de tickets configurada")
    job = await PrintQueue(session).enqueue_copy(
        kind=PrintJobKind.ticket, payload=ticket.payload, printer=printer
    )
    await auth_repo.record_audit(
        session,
        action="printing.copy_enqueued",
        entity="print_job",
        user_id=principal.user_id,
        entity_id=job.id,
        after_data={"copy_of": "ticket", "document_id": str(ticket_id)},
    )
    await session.commit()
    return job


async def enqueue_invoice_copy(
    session: AsyncSession, principal: Principal, invoice_id: UUID
) -> PrintJob:
    """Copia de una factura o rectificativa (misma tabla, mismo permiso)."""

    invoice = await documents_repo.get_invoice(session, invoice_id)
    if invoice is None:
        raise _not_found("Factura no encontrada")
    if invoice.status is InvoiceStatus.voided:
        raise _conflict("La factura está anulada: no se puede imprimir una copia")
    printer = await repo.get_default_printer(session, PrinterKind.invoice)
    if printer is None:
        raise _conflict("No hay impresora de facturas configurada")
    job = await PrintQueue(session).enqueue_copy(
        kind=PrintJobKind.invoice, payload=invoice.payload, printer=printer
    )
    await auth_repo.record_audit(
        session,
        action="printing.copy_enqueued",
        entity="print_job",
        user_id=principal.user_id,
        entity_id=job.id,
        after_data={"copy_of": "invoice", "document_id": str(invoice_id)},
    )
    await session.commit()
    return job


async def enqueue_test_job(
    session: AsyncSession, principal: Principal, printer_id: UUID
) -> PrintJob:
    """Job de prueba hacia una impresora concreta (``kind='test'``)."""

    printer = await repo.get_printer(session, printer_id)
    if printer is None:
        raise _not_found("Impresora no encontrada")
    job = await PrintQueue(session).enqueue(
        printer=printer,
        kind=PrintJobKind.test,
        payload={
            "kind": "test",
            "printer": printer.name,
            "requested_by": str(principal.user_id),
            "requested_at": _utcnow().isoformat(),
        },
    )
    await auth_repo.record_audit(
        session,
        action="printing.test_enqueued",
        entity="print_job",
        user_id=principal.user_id,
        entity_id=job.id,
        after_data={"printer_id": str(printer.id)},
    )
    await session.commit()
    return job


# ---------------------------------------------------------------------------
# Impresoras (administración)
# ---------------------------------------------------------------------------
def _printer_snapshot(printer: Printer) -> dict:
    return {
        "name": printer.name,
        "kind": printer.kind.value,
        "connection": printer.connection.value,
        "address": printer.address,
        "device_id": str(printer.device_id) if printer.device_id else None,
        "is_default": printer.is_default,
        "active": printer.active,
    }


async def create_printer(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    kind: PrinterKind,
    connection: PrinterConn,
    address: str | None = None,
    device_id: UUID | None = None,
    width_chars: int = 42,
    is_default: bool = False,
) -> Printer:
    """Alta de impresora: de red (``host:port`` ESC/POS 9100, el servidor
    imprime directo) o colgada de un tpv-agent (periférico local)."""

    if connection is PrinterConn.network:
        if not address:
            raise _validation("Una impresora de red requiere address «host:puerto»")
        if device_id is not None:
            raise _validation("Una impresora de red no lleva dispositivo")
    else:
        if address:
            raise _validation("Una impresora de agente no lleva address: va por device_id")
        if device_id is None:
            raise _validation("Una impresora de agente requiere el dispositivo")
        device = await session.get(Device, device_id)
        if device is None:
            raise _not_found("Dispositivo no encontrado")
        if not device.active:
            raise _validation("El dispositivo no está activo")

    if is_default:
        await repo.clear_default_printer(session, kind)
    printer = repo.add_printer(
        session,
        name=name,
        kind=kind,
        connection=connection,
        address=address,
        device_id=device_id,
        width_chars=width_chars,
        is_default=is_default,
        active=True,
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        # Backstop de los CHECK/UNIQUE (ck_printers_connection, uq_printers_default_per_kind):
        # la validación del servicio ya cubre los casos normales.
        await session.rollback()
        raise _validation(
            "Impresora no válida: revisa conexión, address/dispositivo y unicidad"
        ) from exc
    await auth_repo.record_audit(
        session,
        action="printing.printer_created",
        entity="printer",
        user_id=principal.user_id,
        entity_id=printer.id,
        after_data=_printer_snapshot(printer),
    )
    await session.commit()
    return printer


async def update_printer(
    session: AsyncSession, principal: Principal, printer_id: UUID, *, fields: dict
) -> Printer:
    """Mutables: name, address (solo red), width_chars, is_default, active.
    Inmutables: kind y connection (una cocina no se convierte en factura:
    se da de baja y se crea otra)."""

    printer = await repo.get_printer(session, printer_id)
    if printer is None:
        raise _not_found("Impresora no encontrada")
    if "address" in fields and printer.connection is not PrinterConn.network:
        raise _validation("Solo las impresoras de red tienen address")
    if fields.get("is_default"):
        await repo.clear_default_printer(session, printer.kind)
    for key, value in fields.items():
        setattr(printer, key, value)
    await auth_repo.record_audit(
        session,
        action="printing.printer_updated",
        entity="printer",
        user_id=principal.user_id,
        entity_id=printer.id,
        after_data={"fields": sorted(fields), **_printer_snapshot(printer)},
    )
    await session.commit()
    return printer


async def deactivate_printer(
    session: AsyncSession, principal: Principal, printer_id: UUID
) -> None:
    """Baja lógica: la impresora deja de recibir trabajos y de ser default."""

    printer = await repo.get_printer(session, printer_id)
    if printer is None:
        raise _not_found("Impresora no encontrada")
    printer.active = False
    await auth_repo.record_audit(
        session,
        action="printing.printer_deactivated",
        entity="printer",
        user_id=principal.user_id,
        entity_id=printer.id,
        after_data=_printer_snapshot(printer),
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
async def get_job_or_404(session: AsyncSession, job_id: UUID) -> PrintJob:
    job = await repo.get_job(session, job_id)
    if job is None:
        raise _not_found("Trabajo de impresión no encontrado")
    return job

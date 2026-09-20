"""Acceso a datos de impresión (fase 10): impresoras y cola de ``print_jobs``.

Las tablas ya existían desde la migración inicial (0001): esta fase solo las
puebla. Las mutaciones de estado de un job las guarda el repo; la decisión
(máquina de estados, reintentos) vive en ``domain.printing`` y
``services.printing``.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import PrinterKind, PrintJobKind, PrintJobStatus
from app.db.models.printing import Printer, PrintJob

_MAX_ERROR_LENGTH = 500


# ---------------------------------------------------------------------------
# Impresoras
# ---------------------------------------------------------------------------
async def list_printers(session: AsyncSession, *, include_inactive: bool = False) -> list[Printer]:
    stmt = select(Printer).order_by(Printer.kind, Printer.name)
    if not include_inactive:
        stmt = stmt.where(Printer.active.is_(True))
    return list((await session.scalars(stmt)).all())


async def get_printer(session: AsyncSession, printer_id: UUID) -> Printer | None:
    return await session.get(Printer, printer_id)


async def get_default_printer(session: AsyncSession, kind: PrinterKind) -> Printer | None:
    """La impresora activa marcada por defecto para un tipo (uq_printers_default_per_kind)."""

    stmt = (
        select(Printer)
        .where(Printer.kind == kind, Printer.is_default.is_(True), Printer.active.is_(True))
        .order_by(Printer.name)
        .limit(1)
    )
    return await session.scalar(stmt)


async def clear_default_printer(session: AsyncSession, kind: PrinterKind) -> None:
    """Deja sin por-defecto el tipo indicado (antes de marcar uno nuevo)."""

    await session.execute(
        update(Printer)
        .where(Printer.kind == kind, Printer.is_default.is_(True))
        .values(is_default=False)
    )


def add_printer(session: AsyncSession, **values: object) -> Printer:
    """Crea la fila (flush del servicio; así se mapea IntegrityError a 422)."""

    printer = Printer(**values)
    session.add(printer)
    return printer


# ---------------------------------------------------------------------------
# Cola de trabajos: encolado
# ---------------------------------------------------------------------------
async def enqueue_job(
    session: AsyncSession,
    *,
    printer_id: UUID,
    kind: PrintJobKind,
    payload: dict,
    dedupe_key: str | None = None,
) -> tuple[PrintJob, bool]:
    """Encola un trabajo. Con ``dedupe_key`` es idempotente: si ya existe un
    job con esa clave se devuelve (``created=False``) y no se duplica —
    protege el replay de eventos y los reintentos de red del emisor."""

    if dedupe_key is not None:
        stmt = (
            pg_insert(PrintJob)
            .values(printer_id=printer_id, kind=kind, payload=payload, dedupe_key=dedupe_key)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(PrintJob.id)
        )
        job_id = (await session.execute(stmt)).scalar_one_or_none()
        if job_id is None:  # ya existía: devolver el original
            existing = await session.scalar(
                select(PrintJob).where(PrintJob.dedupe_key == dedupe_key)
            )
            return existing, False
        return await session.get(PrintJob, job_id), True

    job = PrintJob(printer_id=printer_id, kind=kind, payload=payload)
    session.add(job)
    await session.flush()
    return job, True


async def get_job(session: AsyncSession, job_id: UUID) -> PrintJob | None:
    return await session.get(PrintJob, job_id)


async def get_job_for_update(session: AsyncSession, job_id: UUID) -> PrintJob | None:
    """Fila de la cola bloqueada: toda operación de estado pasa por aquí."""

    return await session.scalar(
        select(PrintJob).where(PrintJob.id == job_id).with_for_update()
    )


async def list_jobs(
    session: AsyncSession,
    *,
    status: PrintJobStatus | None = None,
    printer_id: UUID | None = None,
    limit: int = 100,
) -> list[PrintJob]:
    stmt = select(PrintJob).order_by(PrintJob.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(PrintJob.status == status)
    if printer_id is not None:
        stmt = stmt.where(PrintJob.printer_id == printer_id)
    return list((await session.scalars(stmt)).all())


async def claimable_jobs(session: AsyncSession, *, limit: int = 20) -> list[PrintJob]:
    """Encolados en FIFO con ``FOR UPDATE SKIP LOCKED``: dos despachadores
    concurrentes nunca reclaman el mismo trabajo."""

    stmt = (
        select(PrintJob)
        .where(PrintJob.status == PrintJobStatus.queued)
        .order_by(PrintJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def failed_jobs(session: AsyncSession, *, limit: int = 50) -> list[PrintJob]:
    """Fallados candidatos a reintento automático (el servicio filtra por
    ``attempts`` y backoff). Bloqueados: nadie más los toca a la vez."""

    stmt = (
        select(PrintJob)
        .where(PrintJob.status == PrintJobStatus.failed)
        .order_by(PrintJob.sent_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


# ---------------------------------------------------------------------------
# Cola de trabajos: transiciones de estado (persistencia pura)
# ---------------------------------------------------------------------------
def mark_sent(job: PrintJob, *, now: datetime) -> None:
    """Reclama el job (queued→sent) y cuenta el intento."""

    job.status = PrintJobStatus.sent
    job.sent_at = now
    job.attempts += 1
    job.last_error = None


def mark_printed(job: PrintJob, *, now: datetime) -> None:
    job.status = PrintJobStatus.printed
    job.printed_at = now


def mark_failed(job: PrintJob, *, error: str) -> None:
    job.status = PrintJobStatus.failed
    job.last_error = error[:_MAX_ERROR_LENGTH]


def requeue(job: PrintJob) -> None:
    """Vuelve a la cola (reintento automático o manual)."""

    job.status = PrintJobStatus.queued


def cancel(job: PrintJob) -> None:
    job.status = PrintJobStatus.cancelled

"""Impresión: impresoras y cola de trabajos."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, SmallInteger,
    Text, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.db.enums import (
    PrinterConn, PrinterKind, PrintJobKind, PrintJobStatus, pg_enum,
)

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())


class Printer(Base, TimestampMixin):
    __tablename__ = "printers"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[PrinterKind] = mapped_column(pg_enum(PrinterKind), nullable=False)
    connection: Mapped[PrinterConn] = mapped_column(
        pg_enum(PrinterConn), nullable=False
    )
    address: Mapped[str | None] = mapped_column(Text)                # 'host:port' (network)
    device_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("devices.id")
    )                                                                 # impresora en tpv-agent
    width_chars: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="42")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint(
            "(connection = 'network' AND address IS NOT NULL AND device_id IS NULL) OR "
            "(connection = 'agent' AND address IS NULL AND device_id IS NOT NULL)",
            name="ck_printers_connection",
        ),
        CheckConstraint("width_chars IN (32, 42, 48)", name="ck_printers_width"),
        Index("uq_printers_default_per_kind", "kind", unique=True,
              postgresql_where=text("is_default AND active")),
    )


class PrintJob(Base, CreatedAtMixin):
    __tablename__ = "print_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    printer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("printers.id"), nullable=False
    )
    kind: Mapped[PrintJobKind] = mapped_column(pg_enum(PrintJobKind), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[PrintJobStatus] = mapped_column(
        pg_enum(PrintJobStatus), nullable=False, server_default="queued"
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_print_jobs_attempts"),
        Index("ix_print_jobs_queue", "printer_id", "created_at",
              postgresql_where=text("status IN ('queued', 'sent')")),
    )

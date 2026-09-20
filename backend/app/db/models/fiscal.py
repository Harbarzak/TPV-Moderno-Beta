"""Modelos del plugin fiscal (fase 34 · Fiscalidad): documentos y su traza.

Arquitectura desacoplada (ADR-010): el motor de ventas NO sabe nada de
fiscalidad — solo emite ``sale_events`` (fase 06). Este plugin los consume:

- ``FiscalDocument``: un documento fiscal por cada evento de venta consumido
  (UNIQUE en ``sale_event_id``: un evento se transforma una sola vez). El
  ``payload`` es un snapshot propio curado desde el evento (dinero SIEMPRE
  string, §3) para que el régimen fiscal no lea jamás las tablas de venta.
- ``FiscalEvent``: la traza append-only de cada operación sobre el documento
  (queued, dispatched, accepted, rejected, cancelled). Toda operación fiscal
  es auditable: aquí queda el detalle y en ``audit_log`` el resumen de cada
  descarga (``fiscal.dispatch``).

Sin ``updated_at``: el histórico de cambios ES la tabla ``fiscal_events``
(cada cambio lleva su fila con marca de tiempo), no una columna sobrescrita.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin
from app.db.enums import (
    FiscalDocumentStatus,
    FiscalDocumentType,
    FiscalEventKind,
    pg_enum,
)

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())


class FiscalDocument(Base, CreatedAtMixin):
    """Documento fiscal generado desde un evento genérico de venta."""

    __tablename__ = "fiscal_documents"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    sale_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sale_events.id"), nullable=False
    )
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    doc_type: Mapped[FiscalDocumentType] = mapped_column(
        pg_enum(FiscalDocumentType), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[FiscalDocumentStatus] = mapped_column(
        pg_enum(FiscalDocumentStatus), nullable=False,
        server_default=FiscalDocumentStatus.pending.value,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("sale_event_id", name="uq_fiscal_documents_sale_event_id"),
        CheckConstraint("attempts >= 0", name="ck_fiscal_documents_attempts"),
        Index("ix_fiscal_documents_status", "status", "created_at"),
        Index("ix_fiscal_documents_order", "order_id"),
    )

    events: Mapped[list[FiscalEvent]] = relationship(
        back_populates="document", order_by="FiscalEvent.created_at"
    )


class FiscalEvent(Base, CreatedAtMixin):
    """Traza append-only de una operación fiscal sobre un documento."""

    __tablename__ = "fiscal_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("fiscal_documents.id"), nullable=False
    )
    kind: Mapped[FiscalEventKind] = mapped_column(pg_enum(FiscalEventKind), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id")
    )

    __table_args__ = (Index("ix_fiscal_events_document", "document_id", "created_at"),)

    document: Mapped[FiscalDocument] = relationship(back_populates="events")

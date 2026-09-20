"""Ventas: zonas, mesas, órdenes, líneas (snapshot), pagos, tickets, facturas, devoluciones,
numeración y eventos genéricos de venta (para el futuro adaptador fiscal)."""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, Numeric, SmallInteger, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin
from app.db.enums import (
    InvoiceStatus, OrderStatus, OrderType, PaymentStatus, SaleEventType,
    SequenceScope, pg_enum,
)

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())
Money = Numeric(12, 2)
Qty = Numeric(10, 3)
Rate = Numeric(5, 2)


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class DiningTable(Base):
    __tablename__ = "dining_tables"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    zone_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    seats: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Plano 2D (fase 30 · Modo restaurante): posición en el lienzo; NULL =
    # sin colocar (se lista en su zona pero no se dibuja en el mapa).
    pos_x: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    pos_y: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("seats > 0", name="ck_dining_tables_seats"),
        UniqueConstraint("zone_id", "name", name="uq_dining_tables_zone_name"),
        Index("ix_dining_tables_zone", "zone_id"),
    )


class Order(Base, CreatedAtMixin):
    """La venta. draft → paid; voided solo con motivo y autor. Líneas = snapshot inmutable."""

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    terminal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    cash_session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cash_sessions.id")
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id")
    )
    dining_table_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dining_tables.id")
    )
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus), nullable=False, server_default="draft"
    )
    order_type: Mapped[OrderType] = mapped_column(
        pg_enum(OrderType), nullable=False, server_default="bar"
    )
    guest_count: Mapped[int | None] = mapped_column(SmallInteger)
    note: Mapped[str | None] = mapped_column(Text)
    # «Cuenta pedida» (fase 30): instante en que la sala pidió el cobro; el
    # estado del semáforo se deriva en lectura (draft + esto = bill).
    bill_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_base: Mapped[Decimal | None] = mapped_column(Money)
    total_tax: Mapped[Decimal | None] = mapped_column(Money)
    total_amount: Mapped[Decimal | None] = mapped_column(Money)
    tax_summary: Mapped[dict | None] = mapped_column(JSONB)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    void_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status <> 'paid' OR (cash_session_id IS NOT NULL AND paid_at IS NOT NULL "
            "AND total_amount IS NOT NULL)",
            name="ck_orders_paid_complete",
        ),
        CheckConstraint(
            "status <> 'voided' OR (voided_at IS NOT NULL AND voided_by IS NOT NULL "
            "AND void_reason IS NOT NULL)",
            name="ck_orders_void_complete",
        ),
        CheckConstraint(
            "status IN ('paid', 'voided') OR total_amount IS NULL",
            name="ck_orders_draft_no_totals",
        ),
        CheckConstraint("guest_count IS NULL OR guest_count > 0", name="ck_orders_guest_count"),
        # Una sola comanda abierta por mesa:
        Index(
            "uq_orders_open_per_table", "dining_table_id", unique=True,
            postgresql_where=(text("status = 'draft' AND dining_table_id IS NOT NULL")),
        ),
        Index("ix_orders_status_created", "status", "created_at"),
        Index("ix_orders_cash_session", "cash_session_id"),
        Index("ix_orders_terminal_created", "terminal_id", "created_at"),
        Index("ix_orders_user_created", "user_id", "created_at"),
    )


class OrderLine(Base, CreatedAtMixin):
    __tablename__ = "order_lines"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)             # snapshot
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)  # snapshot
    tax_rate: Mapped[Decimal] = mapped_column(Rate, nullable=False)     # snapshot
    quantity: Mapped[Decimal] = mapped_column(Qty, nullable=False)      # negativo en devoluciones
    discount_pct: Mapped[Decimal] = mapped_column(Rate, nullable=False, server_default="0")
    line_base: Mapped[Decimal] = mapped_column(Money, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))

    __table_args__ = (
        CheckConstraint("unit_price >= 0", name="ck_order_lines_price"),
        CheckConstraint("tax_rate BETWEEN 0 AND 100", name="ck_order_lines_tax"),
        CheckConstraint("quantity <> 0", name="ck_order_lines_qty"),
        CheckConstraint("discount_pct BETWEEN 0 AND 100", name="ck_order_lines_discount"),
        CheckConstraint("voided_at IS NULL OR voided_by IS NOT NULL", name="ck_order_lines_void"),
        Index("ix_order_lines_order", "order_id"),
        Index("ix_order_lines_product", "product_id"),
    )


class Payment(Base, CreatedAtMixin):
    __tablename__ = "payments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    payment_method_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("payment_methods.id"), nullable=False
    )
    terminal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("terminals.id")
    )
    device_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("devices.id"))
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus), nullable=False, server_default="confirmed"
    )
    external_ref: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount"),
        Index("ix_payments_order", "order_id"),
        Index("ix_payments_method_created", "payment_method_id", "created_at"),
    )


class DocumentSequence(Base):
    """Series de numeración. Asignación con SELECT … FOR UPDATE (concurrencia)."""

    __tablename__ = "document_sequences"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    scope: Mapped[SequenceScope] = mapped_column(pg_enum(SequenceScope), nullable=False)
    terminal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("terminals.id")
    )
    series: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    current_value: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint("current_value >= 0", name="ck_document_sequences_value"),
        UniqueConstraint("scope", "terminal_id", "year", "series",
                         name="uq_document_sequences",
                         postgresql_nulls_not_distinct=True),
    )


class Ticket(Base, CreatedAtMixin):
    """Documento impreso 1:1 con la orden; payload congelado para reimpresión."""

    __tablename__ = "tickets"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True
    )
    terminal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    series: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reprint_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint("number > 0", name="ck_tickets_number"),
        CheckConstraint("reprint_count >= 0", name="ck_tickets_reprint"),
        UniqueConstraint("terminal_id", "series", "number", name="uq_tickets_terminal_series_number"),
        Index("ix_tickets_created", "created_at"),
    )


class Invoice(Base, CreatedAtMixin):
    __tablename__ = "invoices"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    series: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        pg_enum(InvoiceStatus), nullable=False, server_default="issued"
    )
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_base: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total_tax: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    tax_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    # Factura que esta rectifica (fase 09); nullable: las normales no rectifican nada.
    rectified_invoice_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id")
    )

    __table_args__ = (
        CheckConstraint("number > 0", name="ck_invoices_number"),
        CheckConstraint(
            "status <> 'voided' OR (voided_at IS NOT NULL AND void_reason IS NOT NULL)",
            name="ck_invoices_void",
        ),
        UniqueConstraint("series", "year", "number", name="uq_invoices_series_year_number"),
        Index("ix_invoices_customer", "customer_id"),
        Index("ix_invoices_issue_date", "year", "issue_date"),
        Index("ix_invoices_rectified", "rectified_invoice_id"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), primary_key=True
    )
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), primary_key=True, unique=True
    )


class Refund(Base, CreatedAtMixin):
    """Enlace devolución ↔ venta original (la devolución es una orden negativa)."""

    __tablename__ = "refunds"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    original_order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True
    )
    refund_order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (CheckConstraint("amount > 0", name="ck_refunds_amount"),)


class SaleEvent(Base, CreatedAtMixin):
    """Eventos genéricos de venta; el futuro adaptador fiscal los consume (plugin)."""

    __tablename__ = "sale_events"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False
    )
    event_type: Mapped[SaleEventType] = mapped_column(pg_enum(SaleEventType), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_sale_events_pending", "created_at",
              postgresql_where=text("dispatched_at IS NULL")),
        Index("ix_sale_events_order", "order_id"),
    )

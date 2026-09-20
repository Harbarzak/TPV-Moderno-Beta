"""Caja: sesiones, arqueos y movimientos."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text,
    func, text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import CashMoveKind, pg_enum

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())
Money = Numeric(12, 2)


class CashSession(Base):
    """Sesión de caja. Unicidad de sesión abierta por terminal vía índice parcial."""

    __tablename__ = "cash_sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    terminal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("terminals.id"), nullable=False
    )
    opened_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    closed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    opening_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    expected_amount: Mapped[Decimal | None] = mapped_column(Money)
    counted_amount: Mapped[Decimal | None] = mapped_column(Money)
    difference: Mapped[Decimal | None] = mapped_column(Money)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("opening_amount >= 0", name="ck_cash_sessions_opening"),
        CheckConstraint(
            "closed_at IS NULL OR (expected_amount IS NOT NULL AND counted_amount IS NOT NULL "
            "AND difference IS NOT NULL)",
            name="ck_cash_sessions_close_complete",
        ),
        CheckConstraint("closed_at IS NULL OR closed_at > opened_at",
                        name="ck_cash_sessions_order"),
        Index("uq_cash_sessions_open_per_terminal", "terminal_id", unique=True,
              postgresql_where=text("closed_at IS NULL")),
        Index("ix_cash_sessions_opened", "opened_at"),
    )


class CashCount(Base):
    """Arqueo puntual (puede haber varios por sesión)."""

    __tablename__ = "cash_counts"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    cash_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cash_sessions.id", ondelete="CASCADE"), nullable=False
    )
    counted_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    counted_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("counted_amount >= 0", name="ck_cash_counts_amount"),
        Index("ix_cash_counts_session", "cash_session_id"),
    )


class CashCountLine(Base):
    __tablename__ = "cash_count_lines"

    cash_count_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cash_counts.id", ondelete="CASCADE"), primary_key=True
    )
    denomination: Mapped[Decimal] = mapped_column(Numeric(10, 2), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("denomination > 0", name="ck_cash_count_lines_denom"),
        CheckConstraint("quantity >= 0", name="ck_cash_count_lines_qty"),
    )


class CashMovement(Base):
    __tablename__ = "cash_movements"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    cash_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cash_sessions.id"), nullable=False
    )
    payment_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("payments.id"))
    kind: Mapped[CashMoveKind] = mapped_column(pg_enum(CashMoveKind), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cash_movements_amount"),
        Index("ix_cash_movements_session", "cash_session_id"),
    )

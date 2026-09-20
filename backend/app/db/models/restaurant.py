"""Restaurante/KDS: comandas (pedidos), sus líneas y las estaciones de cocina."""
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin
from app.db.enums import KitchenStatus, pg_enum

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())
Qty = Numeric(10, 3)


class KitchenStation(Base, CreatedAtMixin):
    """Estación de cocina (fase 32 · KDS): «Cocina caliente», «Barra», «Frío»…

    Varias cocinas = varias estaciones activas; el tablero puede filtrar por
    una de ellas. Las líneas de comanda guardan la estación como snapshot
    (la del producto al añadirse), para que recategorizar un producto no
    reescriba el histórico.
    """

    __tablename__ = "kitchen_stations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 120", name="ck_kitchen_stations_name_len"
        ),
    )


class KitchenOrder(Base, CreatedAtMixin):
    """Comanda (1:1 con la orden). Estados KDS en cabecera."""

    __tablename__ = "kitchen_orders"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True
    )
    status: Mapped[KitchenStatus] = mapped_column(
        pg_enum(KitchenStatus), nullable=False, server_default="pending"
    )
    # Prioridad manual de cocina (fase 32): 0 = normal, 1 = urgente.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("priority IN (0, 1)", name="ck_kitchen_orders_priority"),
        CheckConstraint(
            "(ready_at IS NULL OR created_at <= ready_at) AND "
            "(served_at IS NULL OR (ready_at IS NOT NULL AND served_at >= ready_at))",
            name="ck_kitchen_orders_flow",
        ),
    )


class KitchenOrderLine(Base, CreatedAtMixin):
    __tablename__ = "kitchen_order_lines"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    kitchen_order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kitchen_orders.id", ondelete="CASCADE"), nullable=False
    )
    order_line_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("order_lines.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)  # snapshot
    quantity: Mapped[Decimal] = mapped_column(Qty, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[KitchenStatus] = mapped_column(
        pg_enum(KitchenStatus), nullable=False, server_default="pending"
    )
    # Estación de la línea: snapshot de la del producto al añadirse (fase 32).
    station_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kitchen_stations.id")
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_kitchen_lines_qty"),
        Index("ix_kitchen_lines_order", "kitchen_order_id"),
    )

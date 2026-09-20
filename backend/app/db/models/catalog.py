"""Catálogo: departamentos, categorías, IVA, productos, precios, paneles, clientes, formas de pago."""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.db.enums import PaymentKind, pg_enum

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())
Money = Numeric(12, 2)
Qty = Numeric(10, 3)
Rate = Numeric(5, 2)


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    department_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("departments.id")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (Index("ix_categories_department", "department_id"),)


class TaxRate(Base, CreatedAtMixin):
    """Tipos de IVA con vigencia temporal; las líneas vendidas guardan copia del tipo."""

    __tablename__ = "tax_rates"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Rate, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("rate >= 0 AND rate <= 100", name="ck_tax_rates_rate"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_tax_rates_period"),
        UniqueConstraint("code", "valid_from", name="uq_tax_rates_code_valid_from"),
        Index("ix_tax_rates_current", "code",
              postgresql_where=text("valid_to IS NULL")),
    )


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    sku: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categories.id")
    )
    tax_rate_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tax_rates.id"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)  # actual (snapshot en líneas)
    weighable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    kitchen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Estación de cocina destino (fase 32 · KDS); NULL = «todas las estaciones».
    kitchen_station_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("kitchen_stations.id")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_products_name_len"),
        Index("ix_products_category", "category_id"),
        Index("ix_products_name", "name"),
    )


class ProductPrice(Base):
    """Histórico inmutable de precios; un solo precio vigente (índice parcial)."""

    __tablename__ = "product_prices"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_product_prices_price"),
        Index("ix_product_prices_product", "product_id", "valid_from"),
        Index("uq_product_prices_current", "product_id", unique=True,
              postgresql_where=text("valid_to IS NULL")),
    )


class PriceTier(Base, TimestampMixin):
    """Tarifa de precios (p. ej. 'bar', 'terraza', 'hotel'); cada producto puede
    tener un precio propio por tarifa."""

    __tablename__ = "price_tiers"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class ProductTierPrice(Base, CreatedAtMixin):
    """Precio del producto en una tarifa concreta; sin fila aplica el precio general."""

    __tablename__ = "product_tier_prices"

    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tier_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("price_tiers.id"), primary_key=True
    )
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)

    __table_args__ = (CheckConstraint("price >= 0", name="ck_product_tier_prices_price"),)


class ProductBarcode(Base, CreatedAtMixin):
    """Código de barras del producto (varios posibles; único en todo el catálogo)."""

    __tablename__ = "product_barcodes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


class ProductImage(Base, CreatedAtMixin):
    """Imagen del producto (ruta/referencia); sort_order 0 = principal."""

    __tablename__ = "product_images"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_product_images_product", "product_id"),)


class Panel(Base, TimestampMixin):
    __tablename__ = "panels"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class SubPanel(Base, TimestampMixin):
    __tablename__ = "subpanels"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    panel_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("panels.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (Index("ix_subpanels_panel", "panel_id"),)


class PanelItem(Base):
    __tablename__ = "panel_items"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    panel_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("panels.id"))
    subpanel_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subpanels.id")
    )
    product_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    grid_row: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    grid_col: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "(panel_id IS NOT NULL AND subpanel_id IS NULL) OR "
            "(panel_id IS NULL AND subpanel_id IS NOT NULL)",
            name="ck_panel_items_parent",
        ),
        Index("ix_panel_items_panel", "panel_id"),
        Index("ix_panel_items_subpanel", "subpanel_id"),
        Index("ix_panel_items_product", "product_id"),
    )


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    tax_id: Mapped[str | None] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    discount_pct: Mapped[Decimal] = mapped_column(
        Rate, nullable=False, server_default="0"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("discount_pct BETWEEN 0 AND 100", name="ck_customers_discount"),
        Index("ix_customers_name", "name"),
    )


class PaymentMethod(Base, TimestampMixin):
    __tablename__ = "payment_methods"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[PaymentKind] = mapped_column(pg_enum(PaymentKind), nullable=False)
    opens_drawer: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

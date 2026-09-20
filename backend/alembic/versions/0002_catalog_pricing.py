"""Módulo de productos (fase 04): tarifas, precios por tarifa, códigos de barras e imágenes.

Migración aditiva sobre 0001: crea las cuatro tablas nuevas del catálogo
(``price_tiers``, ``product_tier_prices``, ``product_barcodes``, ``product_images``)
y el trigger ``updated_at`` de las tarifas, sin tocar nada de lo existente (las FK
hacia products van con ON DELETE CASCADE, igual que el resto de tablas anexas del
producto). La fuente de verdad del DDL sigue siendo docs/database/schema.sql.
Las seeds van aparte y NO forman parte de la migración.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11
"""
from alembic import op

from app.db.base import Base
import app.db.models  # noqa: F401 — registra todas las tablas en Base.metadata
from app.db.models.catalog import (
    PriceTier,
    ProductBarcode,
    ProductImage,
    ProductTierPrice,
)

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Solo las tablas nuevas: create_all no toca las que ya existen (0001).
_NEW_TABLES = (
    PriceTier.__table__,
    ProductTierPrice.__table__,
    ProductBarcode.__table__,
    ProductImage.__table__,
)


def upgrade() -> None:
    # set_updated_at() ya existe (0001); solo falta el trigger de price_tiers.
    Base.metadata.create_all(bind=op.get_bind(), tables=_NEW_TABLES)
    op.execute(
        "CREATE TRIGGER trg_price_tiers_updated BEFORE UPDATE ON price_tiers "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DROP TRIGGER IF EXISTS trg_price_tiers_updated ON price_tiers")
    for table in reversed(_NEW_TABLES):  # hijas antes que price_tiers
        Base.metadata.drop_all(bind=bind, tables=[table])

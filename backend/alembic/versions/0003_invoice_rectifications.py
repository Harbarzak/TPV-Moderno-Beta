"""Documentos (fase 09): columna de rectificación en facturas.

Migración aditiva sobre 0002: ``invoices.rectified_invoice_id`` enlaza cada
factura rectificativa (serie R, importes negativos) con la factura que
rectifica. Nullable: una factura normal no rectifica nada. Sin UNIQUE a
propósito — una misma factura puede tener varias rectificaciones (parciales).
La fuente de verdad del DDL sigue siendo docs/database/schema.sql.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 construye el esquema desde Base.metadata ACTUAL («esquema inicial
    # completo»): una BD creada de cero hoy ya trae esta columna con su FK y
    # su índice. Este paso solo aporta algo en BDs anteriores a fase 09.
    inspector = sa.inspect(op.get_bind())
    columnas = {col["name"] for col in inspector.get_columns("invoices")}
    if "rectified_invoice_id" in columnas:
        return
    op.add_column(
        "invoices",
        sa.Column("rectified_invoice_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_rectified_invoice",
        "invoices",
        "invoices",
        ["rectified_invoice_id"],
        ["id"],
    )
    op.create_index("ix_invoices_rectified", "invoices", ["rectified_invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_rectified", table_name="invoices")
    op.drop_constraint("fk_invoices_rectified_invoice", "invoices", type_="foreignkey")
    op.drop_column("invoices", "rectified_invoice_id")

"""Plano de mesas (fase 30 · Modo restaurante): posiciones 2D y «cuenta pedida».

Migración aditiva sobre 0004:

- ``dining_tables.pos_x`` / ``pos_y``: coordenadas en el plano (0-100,
  porcentaje del lienzo). NULL = mesa sin colocar: se lista en su zona pero
  no se dibuja en el mapa hasta que alguien la coloque.
- ``orders.bill_requested_at``: «cuenta pedida» — el estado intermedio del
  semáforo (§3.2 del sistema de diseño) entre comanda abierta y cobro. Se
  deriva en lectura: no hay ENUM nuevo ni migración de datos.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 construye el esquema desde Base.metadata ACTUAL («esquema inicial
    # completo»): una BD creada de cero hoy ya trae estas columnas. Este paso
    # solo aporta algo en BDs anteriores a fase 30 (patrón de 0003).
    inspector = sa.inspect(op.get_bind())
    columnas_mesas = {c["name"] for c in inspector.get_columns("dining_tables")}
    if "pos_x" not in columnas_mesas:
        op.add_column(
            "dining_tables", sa.Column("pos_x", sa.Numeric(7, 2), nullable=True)
        )
    if "pos_y" not in columnas_mesas:
        op.add_column(
            "dining_tables", sa.Column("pos_y", sa.Numeric(7, 2), nullable=True)
        )
    columnas_ventas = {c["name"] for c in inspector.get_columns("orders")}
    if "bill_requested_at" not in columnas_ventas:
        op.add_column(
            "orders",
            sa.Column("bill_requested_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("orders", "bill_requested_at")
    op.drop_column("dining_tables", "pos_y")
    op.drop_column("dining_tables", "pos_x")

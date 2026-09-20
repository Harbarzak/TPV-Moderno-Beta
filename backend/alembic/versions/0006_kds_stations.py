"""Estaciones de cocina y prioridad KDS (fase 32 · Kitchen Display System).

Migración aditiva sobre 0005 (mismo patrón inspector-guarded: en una BD creada
de cero hoy el esquema inicial ya trae todo, y estos pasos solo aportan en BDs
anteriores a fase 32):

- ``kitchen_stations``: estaciones de cocina («Cocina caliente», «Barra»…) —
  múltiples cocinas como filas activas, no como instalaciones.
- ``products.kitchen_station_id``: estación destino del producto (NULL =
  sin estación concreta: el tablero la muestra en todas).
- ``kitchen_orders.priority``: prioridad manual de cocina (0 normal / 1 urgente).
- ``kitchen_order_lines.station_id``: snapshot de la estación al añadirse la
  línea (recategorizar un producto después no reescribe el histórico).

El resto del modelo KDS (``kitchen_orders`` / ``kitchen_order_lines`` y el
ENUM ``kitchenstatus``) ya existe desde 0001, dormido a la espera de esta fase.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "kitchen_stations" not in inspector.get_table_names():
        op.create_table(
            "kitchen_stations",
            sa.Column(
                "id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.CheckConstraint(
                "char_length(name) BETWEEN 1 AND 120", name="ck_kitchen_stations_name_len"
            ),
        )

    columnas_productos = {c["name"] for c in inspector.get_columns("products")}
    if "kitchen_station_id" not in columnas_productos:
        op.add_column(
            "products",
            sa.Column(
                "kitchen_station_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("kitchen_stations.id"),
                nullable=True,
            ),
        )

    columnas_comandas = {c["name"] for c in inspector.get_columns("kitchen_orders")}
    if "priority" not in columnas_comandas:
        op.add_column(
            "kitchen_orders",
            sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="0"),
        )
        op.create_check_constraint(
            "ck_kitchen_orders_priority", "kitchen_orders", "priority IN (0, 1)"
        )

    columnas_lineas = {c["name"] for c in inspector.get_columns("kitchen_order_lines")}
    if "station_id" not in columnas_lineas:
        op.add_column(
            "kitchen_order_lines",
            sa.Column(
                "station_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("kitchen_stations.id"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("kitchen_order_lines", "station_id")
    op.drop_constraint("ck_kitchen_orders_priority", "kitchen_orders", type_="check")
    op.drop_column("kitchen_orders", "priority")
    op.drop_column("products", "kitchen_station_id")
    op.drop_table("kitchen_stations")

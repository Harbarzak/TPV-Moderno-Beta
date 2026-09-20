"""Esquema inicial completo (fase 01 · Base de datos).

La fuente de verdad del DDL es docs/database/schema.sql; esta migración construye
exactamente ese esquema a partir de Base.metadata (mismo modelo que usará la app),
incluyendo los tipos ENUM nativos de PostgreSQL y el trigger set_updated_at().
Las seeds van aparte (docs/database/seed.sql) y NO forman parte de la migración.

Revision ID: 0001
Revises:
Create Date: 2026-09-11
"""
from alembic import op

from app.db.base import Base
import app.db.models  # noqa: F401 — registra todas las tablas en Base.metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Tablas con updated_at gestionado por trigger (igual que en schema.sql).
_UPDATED_AT_TABLES = (
    "users", "terminals", "departments", "categories", "products",
    "panels", "subpanels", "customers", "payment_methods", "dining_tables",
    "printers",
)


def upgrade() -> None:
    # Crea enums y tablas en orden de dependencias, con la misma naming convention.
    Base.metadata.create_all(bind=op.get_bind())
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _UPDATED_AT_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    # Enums no gestionados por metadata.drop_all: eliminarlos a mano.
    for enum_name in (
        "order_status", "order_type", "payment_kind", "payment_status",
        "cash_move_kind", "kitchen_status", "device_kind", "printer_kind",
        "printer_conn", "print_job_kind", "print_job_status", "sequence_scope",
        "invoice_status", "sale_event_type",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")

"""Plugin fiscal (fase 34 · Fiscalidad Veri*Factu/TicketBAI), ADR-010.

Migración aditiva sobre 0006 (patrón inspector-guarded: en una BD creada de
cero hoy el esquema inicial ya trae todo, y estos pasos solo aportan en BDs
anteriores a fase 34):

- ENUMs ``fiscal_document_type``, ``fiscal_document_status`` y
  ``fiscal_event_kind``.
- ``fiscal_documents``: un documento por ``sale_event`` consumido (UNIQUE:
  la transformación es idempotente). Sin ``updated_at`` — el histórico ES
  ``fiscal_events``.
- ``fiscal_events``: traza append-only de cada operación (auditoría fiscal).
- Permisos ``fiscal.view`` / ``fiscal.dispatch`` (semilla: admin todo,
  manager consulta y descarga).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-14
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_ENUMS = {
    "fiscal_document_type": ("sale", "void", "refund"),
    "fiscal_document_status": ("pending", "sent", "accepted", "rejected", "cancelled"),
    "fiscal_event_kind": ("queued", "dispatched", "accepted", "rejected", "cancelled"),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_enums = {e["name"] for e in inspector.get_enums()}

    for name, values in _ENUMS.items():
        if name not in existing_enums:
            # create_type=False en las columnas: el tipo ya se crea aquí.
            sa.Enum(*values, name=name).create(bind, checkfirst=True)

    if "fiscal_documents" not in inspector.get_table_names():
        op.create_table(
            "fiscal_documents",
            sa.Column("id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("sale_event_id", UUID(as_uuid=True),
                      sa.ForeignKey("sale_events.id"), nullable=False),
            sa.Column("order_id", UUID(as_uuid=True),
                      sa.ForeignKey("orders.id"), nullable=False),
            sa.Column("doc_type", PGEnum(name="fiscal_document_type", create_type=False),
                      nullable=False),
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("status", PGEnum(name="fiscal_document_status", create_type=False),
                      nullable=False, server_default="pending"),
            sa.Column("payload", JSONB(), nullable=False),
            sa.Column("external_ref", sa.Text(), nullable=True),
            sa.Column("error_code", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.UniqueConstraint("sale_event_id", name="uq_fiscal_documents_sale_event_id"),
            sa.CheckConstraint("attempts >= 0", name="ck_fiscal_documents_attempts"),
        )
        op.create_index(
            "ix_fiscal_documents_status", "fiscal_documents", ["status", "created_at"]
        )
        op.create_index("ix_fiscal_documents_order", "fiscal_documents", ["order_id"])

    if "fiscal_events" not in inspector.get_table_names():
        op.create_table(
            "fiscal_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("document_id", UUID(as_uuid=True),
                      sa.ForeignKey("fiscal_documents.id"), nullable=False),
            sa.Column("kind", PGEnum(name="fiscal_event_kind", create_type=False),
                      nullable=False),
            sa.Column("detail", JSONB(), nullable=True),
            sa.Column("actor_user_id", UUID(as_uuid=True),
                      sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
        )
        op.create_index(
            "ix_fiscal_events_document", "fiscal_events", ["document_id", "created_at"]
        )

    # Permisos del plugin (0004: ON CONFLICT DO NOTHING + admin CROSS JOIN).
    for code, description in (
        ("fiscal.view", "Consultar documentos fiscales y estado del plugin"),
        ("fiscal.dispatch", "Ejecutar la descarga fiscal (consumir y certificar)"),
    ):
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, description) "
                "VALUES (:code, :description) ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, description=description)
        )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
            "WHERE r.code = 'admin' ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r "
            "JOIN permissions p ON p.code IN ('fiscal.view', 'fiscal.dispatch') "
            "WHERE r.code = 'manager' ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_fiscal_events_document", table_name="fiscal_events")
    op.drop_table("fiscal_events")
    op.drop_index("ix_fiscal_documents_order", table_name="fiscal_documents")
    op.drop_index("ix_fiscal_documents_status", table_name="fiscal_documents")
    op.drop_table("fiscal_documents")
    for name in _ENUMS:
        sa.Enum(name=name).drop(bind, checkfirst=True)
    op.execute(
        sa.text("DELETE FROM permissions WHERE code IN ('fiscal.view', 'fiscal.dispatch')")
    )

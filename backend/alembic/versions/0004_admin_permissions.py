"""Permisos (fase 22 · Administración): ventas y módulos nuevos del panel.

Data-only sobre 0003: normaliza los códigos de venta que el código usa
(``sales.sell``/``sales.void``; los ``orders.*`` del seed original no se
usaban en el backend) y añade los permisos de los módulos nuevos del panel
(roles, auditoría, backups). Re-sincroniza la matriz del rol ``admin``
(CROSS JOIN) para que las BDs existentes puedan administrar y vender sin
resiembra. La fuente de verdad del catálogo sigue siendo docs/database/seed.sql.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_NEW_PERMISSIONS = [
    ("sales.sell", "Crear y cobrar ventas"),
    ("sales.void", "Anular ventas (requiere motivo)"),
    ("admin.roles", "Gestionar roles y permisos"),
    ("admin.audit", "Consultar la auditoría del sistema"),
    ("admin.backups", "Ejecutar y consultar backups"),
]


def upgrade() -> None:
    for code, description in _NEW_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, description) "
                "VALUES (:code, :description) ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, description=description)
        )
    # El rol admin siempre lo tiene todo (mismo CROSS JOIN que el seed).
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r CROSS JOIN permissions p "
            "WHERE r.code = 'admin' ON CONFLICT DO NOTHING"
        )
    )
    # El código usa sales.*: manager cobra y anula; waiter cobra.
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r "
            "JOIN permissions p ON p.code IN ('sales.sell', 'sales.void') "
            "WHERE r.code = 'manager' ON CONFLICT DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission_id) "
            "SELECT r.id, p.id FROM roles r "
            "JOIN permissions p ON p.code = 'sales.sell' "
            "WHERE r.code = 'waiter' ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    # role_permissions cae en CASCADE con el permiso. Ojo: la BD queda sin
    # sales.* aunque la aplicación los usa; este downgrade solo tiene sentido
    # para revertir la migración, no para operar.
    op.execute(
        "DELETE FROM permissions WHERE code IN "
        "('sales.sell', 'sales.void', 'admin.roles', 'admin.audit', 'admin.backups')"
    )

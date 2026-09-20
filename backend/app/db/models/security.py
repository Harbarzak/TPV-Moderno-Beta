"""Seguridad y personal: roles, permisos, usuarios, sesiones, terminales, dispositivos."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import INET, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin
from app.db.enums import DeviceKind, pg_enum

_uuid_pk = dict(primary_key=True, server_default=func.gen_random_uuid())


class SystemRole(Base, CreatedAtMixin):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Permission(Base, CreatedAtMixin):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    role_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("roles.id"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Solo ORM (no cambia el DDL); selectin evita lazy-loads fuera del event loop.
    role: Mapped["SystemRole"] = relationship("SystemRole", lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "char_length(username) BETWEEN 2 AND 64", name="ck_users_pin_xor_password_len"
        ),
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_user_sessions_expiry"),
        Index("ix_user_sessions_user", "user_id"),
    )


class Terminal(Base, TimestampMixin):
    __tablename__ = "terminals"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")


class Device(Base, CreatedAtMixin):
    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), **_uuid_pk)
    terminal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("terminals.id"))
    kind: Mapped[DeviceKind] = mapped_column(pg_enum(DeviceKind), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (Index("ix_devices_terminal", "terminal_id"),)

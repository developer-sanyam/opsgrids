"""Declarative base and the mixins every table is built from.

The naming convention is fixed here and must not drift: Alembic's autogenerate compares
constraint names, so changing this silently produces migrations that drop and recreate
constraints across the estate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Everything is stored in UTC and converted at the edge (ADR-0000)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Per-service base. Each service sets ``Base.metadata.schema`` in its own models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def as_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        """Column values only — the shape audit before/after snapshots want."""
        skip = exclude or set()
        return {c.name: getattr(self, c.name) for c in self.__table__.columns if c.name not in skip}


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class OrgScopedMixin:
    """Tenancy. Every business table carries this.

    Deliberately not a foreign key: no constraint may cross a schema boundary, or a later
    database-per-service split stops being a config change. Referential integrity for this
    column is a nightly orphan check, and isolation is RLS plus the query filter.
    """

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_by: Mapped[str | None] = mapped_column(String(64))


class SoftDeleteMixin:
    """Soft delete is **not** filtered automatically — every query adds the predicate.

    An implicit global filter is convenient right up to the first report that has to
    include deleted rows, at which point it is a rewrite.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantBase(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """The standard row shape. Mix in alongside ``Base``::

    class Customer(Base, TenantBase):
        __tablename__ = "customers"
    """

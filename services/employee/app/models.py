"""Tables owned by the employee service — the employment record.

Everything here lives in the `employee` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10). The link to account-service is a plain UUID, never a
constraint that crosses a schema boundary.

**Scope of this first slice: the employment record only, no sensitive PII.** "PII is
encrypted and masked by default" is a load-bearing platform rule, and there is no PII
envelope helper in platform-core yet. So this slice deliberately stores only
low-sensitivity employment and org data — name, designation, department, employment type,
lifecycle status — and *defers* the sensitive fields (date of birth, personal contact,
PAN/Aadhaar/UAN/ESI, bank details, documents) to slice 2, which lands the envelope. None
of those fields exist here on purpose.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "employee"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Employee(Base, TenantBase):
    """One person's employment with one organization — the Employee Master record.

    `id` is the canonical employee id the rest of the platform points at: account-service's
    `membership.employee_id` holds this value, linked by a plain UUID with **no** cross-schema
    foreign key. The same person employed by two customers has two rows here, one per
    organization, exactly as they have two memberships.

    Only low-sensitivity employment data lives here (see the module docstring). `full_name` is
    a directory-grade name — it mirrors what account-service already holds on `users` and
    `memberships` — not a sensitive identifier.
    """

    __tablename__ = "employees"

    #: The account membership this employment corresponds to, when there is one. A plain UUID
    #: into `account.memberships.id`, never a foreign key. Unique per organization where set,
    #: because one membership maps to at most one employment record — the mirror of account's
    #: own `uq_memberships_org_employee`.
    membership_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Human-facing code such as "EMP0001". Unique within an organization.
    employee_no: Mapped[str] = mapped_column(String(40), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    designation: Mapped[str | None] = mapped_column(String(120))
    job_title: Mapped[str | None] = mapped_column(String(120))

    #: The department this person sits in — an `account.org_units.id`. A plain indexed UUID,
    #: never a foreign key across the schema boundary. Org structure lives in account-service
    #: because a scope dimension has to resolve when a token is minted (ADR-0002).
    department_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: An `account.locations.code`. Carried as the code rather than the id because LOCATION
    #: scope compares codes — the token carries codes, not location ids.
    location_code: Mapped[str | None] = mapped_column(String(40))

    #: full_time | part_time | contract | intern.
    employment_type: Mapped[str] = mapped_column(String(20), nullable=False, default="full_time")
    #: office | field | remote | hybrid (plan B.16).
    work_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="office")
    date_of_joining: Mapped[date | None] = mapped_column(Date)

    #: Who this person reports to — another employee in the *same* organization. A plain UUID
    #: into `employees.id`, read directly and never walked transitively, so a cycle is untidy
    #: rather than dangerous and "does X manage Y" stays one comparison. This column also
    #: carries TEAM scope: a manager's reports are the rows whose manager id is the manager's
    #: own employee id.
    reporting_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Lifecycle: probation | active | on_notice | exited. Transitions are governed by the
    #: state machine in `domain.py`; this column only stores the current state.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="probation")
    date_of_exit: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        # Every index leads with `organization_id`: under row-level security the policy qual is
        # ANDed into every query, so an index that did not lead with the tenant column would
        # scan the whole platform's rows and discard almost all of them (ADR-0002).
        Index("ix_employees_org_status", "organization_id", "status"),
        Index("ix_employees_org_manager", "organization_id", "reporting_manager_id"),
        Index("ix_employees_org_department", "organization_id", "department_id"),
        UniqueConstraint("organization_id", "employee_no", name="uq_employees_org_employee_no"),
        # One employment record per membership per organization, enforced in the database.
        # Partial, because `membership_id` is legitimately null for an employee who has no
        # platform login yet.
        Index(
            "uq_employees_org_membership",
            "organization_id",
            "membership_id",
            unique=True,
            postgresql_where=text("membership_id IS NOT NULL"),
        ),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

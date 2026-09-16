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

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
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

    #: A second, informational reporting line — another employee in the *same* organization.
    #: Stored so the approval engine's chain endpoint can show it and a later matrix-approval
    #: rule has the data without a second backfill; nothing in this slice routes on it
    #: (ADR-0006 §2). Composite FK to keep a cross-tenant manager unrepresentable, exactly like
    #: `reporting_manager_id`'s own check does for self-reference — see the constraints below.
    dotted_line_manager_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: Bumped by the database, never by the ORM (`app/models.py` trigger
    #: `trg_employees_hierarchy_version`, data model §4.2). A Python-side `+= 1` would compute
    #: from a stale read — there is no `with_for_update` anywhere in this service — and silently
    #: drop a concurrent edit forever under approval's strictly-greater version gate. The service
    #: only ever reads this column back after a write; it never sets it.
    hierarchy_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

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
        CheckConstraint("hierarchy_version >= 0", name="hierarchy_version_non_negative"),
        CheckConstraint(
            "dotted_line_manager_id IS NULL OR dotted_line_manager_id <> id",
            name="dotted_line_not_self",
        ),
        # Explicit, not generated: the convention keys an unnamed unique on its first column
        # only, and a future composite self-FK on `reporting_manager_id` would collide with this
        # one (data model §1.0.1 rule 4, §4.1). It earns its keep twice over — it is also the FK
        # target below, and the index the backfill's keyset pagination is served by.
        UniqueConstraint("organization_id", "id", name="uq_employees_org_id"),
        # Composite on purpose: a plain `FOREIGN KEY (dotted_line_manager_id) REFERENCES
        # employees (id)` would happily accept a manager from another tenant. Named after the
        # *pointer* column rather than the convention's default (which keys on the first column,
        # `organization_id`, and would collide with any other composite self-FK leading with the
        # tenant column).
        ForeignKeyConstraint(
            ["organization_id", "dotted_line_manager_id"],
            ["employee.employees.organization_id", "employee.employees.id"],
            name="fk_employees_dotted_line_manager_id_employees",
            ondelete="SET NULL (dotted_line_manager_id)",
        ),
    )


class EmployeePii(Base, TenantBase):
    """The sensitive PII for one employee — kept in its own 1:1 table, sealed at rest.

    Separate from `employees` on purpose. The employment record is the hot, widely-read table:
    every list, every scope check, every directory lookup reads it. Sensitive identifiers are
    read rarely and only under a stepped-up `read_sensitive` permission. Splitting them keeps the
    hot table lean and the sensitive data isolated — a plain `SELECT *` on `employees` cannot
    even reach a sealed value, so the default read path has nothing to leak.

    Each sensitive field is a **pair**: `<field>_enc` holds the AES-256-GCM envelope
    (`platform_core.seal`) and `<field>_masked` holds the non-sensitive hint the ordinary read
    path shows. Plaintext is never a column here — it exists only transiently inside `seal` on
    write and `open_sealed` on an audited unmask.

    `bank_ifsc` is the one exception: an IFSC is a public bank branch code, not a secret (it is
    printed on every cheque), so it is stored in plaintext as a single column and classified
    non-sensitive (`platform_core.NON_SENSITIVE_KINDS`). Keeping it here rather than on
    `employees` simply groups it with the bank details it belongs to.

    The link to `employees` **is** a foreign key: it stays inside the `employee` schema, and only
    cross-schema foreign keys are banned (ADR-0000 §10). `unique` makes the relationship 1:1 in
    the database rather than by convention.
    """

    __tablename__ = "employee_pii"

    #: The employee these details belong to. A real foreign key — same schema, so it is allowed —
    #: and unique, so an employee has at most one PII row.
    employee_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("employee.employees.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    date_of_birth_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    date_of_birth_masked: Mapped[str | None] = mapped_column(String(40))

    personal_email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    personal_email_masked: Mapped[str | None] = mapped_column(String(120))

    personal_phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    personal_phone_masked: Mapped[str | None] = mapped_column(String(40))

    pan_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    pan_masked: Mapped[str | None] = mapped_column(String(40))

    aadhaar_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    aadhaar_masked: Mapped[str | None] = mapped_column(String(40))

    uan_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    uan_masked: Mapped[str | None] = mapped_column(String(40))

    esi_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    esi_masked: Mapped[str | None] = mapped_column(String(40))

    bank_account_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    bank_account_masked: Mapped[str | None] = mapped_column(String(40))

    #: Non-sensitive: a public branch code, stored whole in plaintext (see the class docstring).
    bank_ifsc: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (
        # Under row-level security the policy qual is ANDed into every query, so the lookup index
        # leads with the tenant column like every other (ADR-0002).
        Index("ix_employee_pii_org_employee", "organization_id", "employee_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

"""Tables owned by the customer service — the CRM account book.

Everything here lives in the `customer` schema and nothing outside it may hold a foreign
key into it (ADR-0000 §10). The pipeline (deal-service) points at these rows by plain
UUID — never a constraint that crosses a schema boundary. Links **within** this schema
(`contacts.company_id`) are real foreign keys, because only cross-schema keys are banned.

Three concerns, one schema:

* **owners** — the CRM's rep directory: the sales people who own companies, contacts and
  deals. Deal-service carries the same `owner_id` values as plain UUIDs; the SPA resolves
  a display name from this list client-side, so a row here is what makes an `owner_id`
  legible anywhere in the product.
* **companies** — the accounts. `annual_revenue_minor` is integer minor units in an ISO
  currency (ADR-0000 §8); no float ever touches money.
* **contacts** — the people at those accounts. `phone` holds the **masked display form
  only** (`+91 98••• ••210`) — this read-only slice never accepts or stores a raw phone
  number, mirroring employee-service's slice 1, which shipped the employment record and
  deferred sensitive fields rather than store PII outside the sealed envelope. When a
  write surface accepts real numbers it gains `phone_enc` + `phone_masked` via
  `platform_core.pii.seal`, and this column becomes the mask. Work email stays plaintext:
  it is business contact data the book exists to show, not personal PII.

The SPA speaks in opaque ids, so the API's identity is the row UUID; uniqueness for seeds
and humans comes from per-organization natural keys (`companies.name`, `contacts.email`,
`owners.email`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "customer"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Owner(Base, TenantBase):
    """One sales rep in the CRM — the person companies, contacts and deals point at.

    `id` is the value every `owner_id` column in the product carries (including
    deal-service's, as a plain UUID across the schema boundary). Kept per organization:
    the same human selling for two customers is two owners, exactly as they would hold two
    memberships. `email` is the natural key that makes the demo seed idempotent.
    """

    __tablename__ = "owners"

    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    #: Display role, e.g. "Enterprise AE" — a label, not a permission.
    role: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    __table_args__ = (
        # Every index leads with `organization_id`: the RLS policy qual is ANDed into every
        # query, so an index that did not lead with the tenant column would scan the whole
        # platform's rows and discard almost all of them (ADR-0002).
        UniqueConstraint("organization_id", "email", name="uq_owners_org_email"),
    )


class Company(Base, TenantBase):
    """One account in the book.

    `owner_id` is the rep who owns the relationship — the scope column for OWN readings,
    a plain UUID into `owners`. `status` is the account's relationship to the tenant:
    customer | prospect | lead | churned. `annual_revenue_minor` is integer minor units
    of `currency` (ISO-4217); `size_band` is a display band ("201-500"), not a number.
    """

    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    industry: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    size_band: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: customer | prospect | lead | churned.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="prospect")

    #: The rep who owns the account. In-schema reference to `owners.id`, kept a plain
    #: indexed UUID rather than a foreign key so the rep directory can later move to the
    #: account service without a schema rewrite.
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    annual_revenue_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_companies_org_name"),
        Index("ix_companies_org_status", "organization_id", "status"),
        Index("ix_companies_org_owner", "organization_id", "owner_id"),
    )


class Contact(Base, TenantBase):
    """One person at an account.

    `company_id` is a real foreign key — same schema, so allowed — and a contact goes with
    its company. `status` is the funnel position: customer | qualified | lead | dormant.
    `phone` is the masked display form only (see the module docstring); `email` is the work
    address the book displays in full. `last_activity_at` is maintained from the pipeline's
    activity timeline and is NULL for a contact never touched.
    """

    __tablename__ = "contacts"

    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    #: Masked display form only — never a raw number. See the module docstring.
    phone: Mapped[str] = mapped_column(String(40), nullable=False, default="")

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customer.companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: lead | qualified | customer | dormant.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="lead")
    #: The rep who owns the relationship — the OWN-scope column.
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: When this person was last touched (call, email, meeting…). NULL: never contacted.
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_contacts_org_email"),
        Index("ix_contacts_org_company", "organization_id", "company_id"),
        Index("ix_contacts_org_status", "organization_id", "status"),
        Index("ix_contacts_org_owner", "organization_id", "owner_id"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

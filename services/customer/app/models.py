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
  number. `phone_enc` beside it holds the raw number sealed by `platform_core.pii.seal`,
  which is what a messaging channel needs and what a masked string cannot provide. The pair
  is the same shape employee-service uses for `personal_phone_enc`; only the mask column's
  name differs, because `phone` was already this service's published contract. Work email
  stays plaintext: it is business contact data the book exists to show, not personal PII.

The SPA speaks in opaque ids, so the API's identity is the row UUID; uniqueness for seeds
and humans comes from per-organization natural keys (`companies.name`, `contacts.email`,
`owners.email`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
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
    #: False once a rep leaves. `services.deactivate_owner` is the only writer, and it
    #: refuses while the rep still owns a live company or contact — reassign first
    #: (`services.update_company`/`update_contact`'s existing `owner_id` path), deactivate
    #: second. A deactivated owner is kept, never deleted: every row that already carries
    #: their id as `owner_id` must keep resolving to a name.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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
        # Unique among LIVE rows only: a soft-deleted record must not hold its name hostage.
        # Case-*sensitive* on purpose, and deliberately left that way (see
        # `uq_companies_org_name_ci`'s own comment below for why a case-insensitive version of
        # this same constraint was tried and reverted): the original constraint a database
        # already carries, and a migration that replaced it with a unique index on `lower(name)`
        # would fail outright on any organization that already holds two names differing only
        # by case ("Acme Ltd" and "ACME LTD") — a real, silent possibility this constraint's own
        # case-sensitivity has allowed since the day it was created.
        Index(
            "uq_companies_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Non-unique: "Acme Ltd" and "ACME LTD" are the same account to anyone reading the
        # book, and `get_company_by_name`'s lookup needs to find either one written in either
        # case — but this index cannot *enforce* that they are the same row without a data
        # migration nobody has reviewed or consented to (existing organizations may already
        # hold both). New case-insensitive duplicates are refused going forward by
        # `services.create_company`'s own per-name `pg_advisory_xact_lock`, not by a
        # constraint that would break on data that already exists.
        Index(
            "ix_companies_org_name_ci",
            "organization_id",
            text("lower(name)"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
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
    #: The masked display form — never a raw number. This column plays the `<field>_masked`
    #: role of the platform's sealed-envelope pattern; it keeps the name `phone` rather than
    #: becoming `phone_masked` because it is already the contract the CRM's read surface and
    #: its SPA speak, and renaming it would change what every existing reader receives.
    phone: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: The raw number, AES-256-GCM sealed by `platform_core.pii.seal` — the other half of the
    #: pair, and the only place a contact's real phone number exists.
    #:
    #: It exists because a masked number cannot be messaged. WhatsApp, and every channel after
    #: it, needs the actual digits, and the platform's answer to "sensitive data a machine must
    #: read" is already written: seal it, mask it for display, and let exactly one audited path
    #: open it. That path is `services.unmask_contact_phone` for a human who stepped up, and
    #: `api/internal.py` for a service holding a token addressed to customer by name.
    #:
    #: NULL for every contact whose number was never supplied raw — which is all of them until
    #: a write surface passes one, so the ordinary read behaviour is unchanged.
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: A keyed blind index of the same number — `platform_core.pii.blind_index`.
    #:
    #: It exists because `seal` uses a fresh nonce per call, so the sealed column cannot be
    #: searched: the same number encrypts differently every time and there is no equality to
    #: match on. An inbound WhatsApp message arrives carrying a phone number, and *which
    #: contact is this* has to be answerable or every conversation creates a duplicate
    #: customer.
    #:
    #: Deterministic and keyed, so a lookup is one indexed query and nothing is decrypted —
    #: which also means finding a match reads no PII and writes no sensitive-read audit row.
    #: The key is what makes it safe: a phone number is a ten-digit space, so an *unkeyed*
    #: digest of one would be reversible by brute force.
    #:
    #: What it does concede is equality — anyone who can read this column learns which
    #: contacts share a number, which `seal` was arranged not to reveal. For a phone number
    #: that is the deliberate trade, because matching an inbound message *is* an equality
    #: question.
    phone_index: Mapped[str | None] = mapped_column(String(64))

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
        # Unique among LIVE rows only: a soft-deleted record must not hold its name hostage.
        Index(
            "uq_contacts_org_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_contacts_org_company", "organization_id", "company_id"),
        Index("ix_contacts_org_status", "organization_id", "status"),
        Index("ix_contacts_org_owner", "organization_id", "owner_id"),
        # The inbound-message lookup: which contact holds this number. Leads with the tenant
        # column like every index here, because the RLS policy qual is ANDed into the query.
        Index("ix_contacts_org_phone_index", "organization_id", "phone_index"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

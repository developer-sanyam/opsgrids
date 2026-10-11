"""Tables owned by the lead service — the CRM's intake, ahead of the pipeline.

Everything here lives in the `lead` schema and nothing outside it may hold a foreign key into
it (ADR-0000 §10). `owner_id` and the ids a converted lead points at (`converted_company_id`,
`converted_contact_id`, `converted_deal_id`) are plain UUIDs into customer-service's and
deal-service's rows — never a constraint that crosses a schema boundary.

**leads** — one prospect, captured from a web form, an import or a rep typing one in by hand
(`source`). `status` moves new -> working -> qualified | disqualified, and qualified -> converted
(`app/domain.py` owns the transition rule; nothing here enforces it at the row level). A lead is
not a deal: it carries no amount and no close date, and most of them are discarded — modelling
that as a deal would pollute every pipeline metric the CRM sells (ecosystem-gaps.md §2).

**Email and phone are sealed, not stored plain (ADR-0017 / CLAUDE.md's "PII is encrypted and
masked by default").** A lead is an unvetted, self-reported contact — closer to a webhook payload
than to a book of established business contacts — so unlike `customer.contacts.email` (kept
plaintext there as business contact data the account book exists to show) a lead's email and
phone both get the full envelope: `<field>_masked` for the ordinary read, `<field>_enc` sealed by
`platform_core.pii.seal`, and `<field>_index` — a keyed blind index — so a second web-form
submission from the same address dedupes without ever decrypting anything to check.

**assignment_rules** — one row per organization, holding the round-robin (or fixed owner-list)
policy `services.assign_owner` reads. Not a table the SPA calls a "rule engine": it is exactly
the ordered list of reps to spread new leads across, plus a cursor that advances one slot per
assignment. Kept in its own table rather than a JSON blob on the organization because ORG-scoped
config that survives independently of any one lead is exactly what a table is for.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "lead"


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA

#: The lifecycle, in order. `domain.can_transition` is the only enforcement point — this
#: constant lives here because the model docstring above is where a reader looks first.
STATUSES: tuple[str, ...] = ("new", "working", "qualified", "disqualified", "converted")


class Lead(Base, TenantBase):
    """One prospect, from capture to conversion (or disqualification).

    `owner_id` is the rep working the lead — the OWN-scope column — a plain UUID, assigned
    either by the caller or by `services.assign_owner` reading this organization's
    `AssignmentRule`. `converted_*` are NULL until `:convert` succeeds and then never change
    again: the ids a second call to `:convert` returns, so converting twice is one company and
    one deal (ADR-0017).
    """

    __tablename__ = "leads"

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))

    #: The masked display form only — never the raw address. See the module docstring.
    email_masked: Mapped[str | None] = mapped_column(String(320))
    #: The raw email, AES-256-GCM sealed by `platform_core.pii.seal`.
    email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    #: A keyed blind index of the same email — `platform_core.pii.blind_index` — so a second
    #: submission from the same address is found without decrypting anything.
    email_index: Mapped[str | None] = mapped_column(String(64))

    phone_masked: Mapped[str | None] = mapped_column(String(40))
    #: The raw phone, sealed the same way as `email_enc`.
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_index: Mapped[str | None] = mapped_column(String(64))

    #: form | import | manual.
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    #: new | working | qualified | disqualified | converted. See `STATUSES`.
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="new")
    #: The rep working the lead. Plain UUID into customer-service's rep directory
    #: (`customer.owners`) — never validated against it here, the same trust boundary
    #: `deal.company_id` already crosses without a lookup.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Set once, by `:convert`, and never again — the idempotency record. A lead's status is
    #: "converted" iff these are set; the reverse never happens, so the two never disagree.
    converted_company_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    converted_contact_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    converted_deal_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: The claim-lease pair `services.claim_conversion` uses to serialize concurrent
    #: `:convert` calls on the *same* lead without holding a row lock across the network calls
    #: that follow. `conversion_claim_id` is a fresh id per attempt (never the lead's own id,
    #: so it cannot be guessed and replayed); `conversion_claimed_at` is when that attempt
    #: took the claim. A second call while the lease is still live (`domain
    #: .CONVERSION_LEASE_SECONDS`) is refused with a 409; once the lease expires, a new
    #: attempt takes over. Cleared (`NULL`) by `services.finalize_conversion` and
    #: `services.fail_conversion` alike — a cleared claim is what lets the *next* legitimate
    #: attempt start with no lease to wait out.
    conversion_claim_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    conversion_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Folded into the input `domain.conversion_ids` derives this lead's company, contact and
    #: deal ids from — `0` for every lead that has never aborted a conversion, so every id
    #: already handed out stays exactly what it was. `services.finalize_conversion` bumps it on
    #: every abort, and `services.fail_conversion` bumps it when customer reports the record an
    #: *earlier* attempt created has since been deleted: the ids are deterministic on purpose
    #: (that is the whole idempotency mechanism), which means an aborted attempt's ids, or a
    #: deleted record at a lead's deterministic id, would otherwise be handed to the next
    #: attempt again. A new generation is a new set of ids — fresh records, same lead.
    conversion_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        # Every index leads with `organization_id`: the RLS policy qual is ANDed into every
        # query, so an index that did not lead with the tenant column would scan the whole
        # platform's rows and discard almost all of them (ADR-0002).
        Index("ix_leads_org_status", "organization_id", "status"),
        Index("ix_leads_org_owner", "organization_id", "owner_id"),
        # The dedup lookup for a repeat web-form submission. A blind index, not the sealed
        # column itself — `seal`'s fresh nonce per call makes the ciphertext unsearchable.
        # Unique among LIVE rows with a real email only: NULL is not "no other lead shares
        # this NULL" and Postgres already treats multiple NULLs in a unique index that way,
        # but the `deleted_at` half still needs stating — a soft-deleted lead must not hold
        # a real customer's email hostage against ever submitting the form again.
        Index(
            "uq_leads_org_email_index",
            "organization_id",
            "email_index",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND email_index IS NOT NULL"),
        ),
    )


class AssignmentRule(Base, TenantBase):
    """One organization's lead-assignment policy — round-robin over an ordered owner list.

    `cursor` is the index of the *next* owner to receive a lead (mod `len(owner_ids)`), and
    `domain.next_owner` is the pure function that reads it; `services.assign_owner` is the only
    writer, and it advances the cursor in the same transaction as the lead it assigns, so a
    crash between the two cannot skip or repeat a rep. `mode` is `round_robin` today; `manual`
    disables auto-assignment entirely (a created lead keeps whatever `owner_id` the caller gave
    it, or none).
    """

    __tablename__ = "assignment_rules"

    #: round_robin | manual.
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="round_robin")
    #: The rep rotation, in order. Plain UUIDs into customer-service's rep directory, same
    #: trust boundary as `Lead.owner_id`.
    owner_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list
    )
    cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        # One rule per organization — `services.get_or_create_rule` is the only writer of a
        # new row, and this is what makes a race between two concurrent first-writes a
        # constraint violation rather than two silently coexisting policies.
        UniqueConstraint("organization_id", name="uq_assignment_rules_org"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

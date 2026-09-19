"""Tables owned by the storefront service (ADR-0010 §10).

Everything here lives in the `storefront` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links **within** the schema — `zone_postal_codes.zone_id` — are real
foreign keys, because only *cross-schema* ones are banned.

Four tables, and what each is for:

* **storefronts** — the product's identity row, and the only thing a stranger's request can
  start from. This ADR finishes four of its columns (`slug`, `status`, `currency`, `timezone`);
  the store document, theme, pages and catalogue are a later ADR's and are deliberately not
  stubbed here.
* **delivery_zones** — a named bag of postal codes plus the behaviour that applies to all of
  them: the mode, the promise, the (display-only) fee and minimum order value.
* **zone_postal_codes** — one row per postal code. **One postal code maps to exactly one zone
  per organization**, so the hot lookup is a single-row index probe and there is no precedence
  rule for a merchant to learn.
* **serviceability_demand** — a daily counter, not a log. The public endpoint is an
  unauthenticated write, so a row per check would be a free way for a stranger to grow a
  merchant's table without bound. A counter is bounded by the number of days, distinct postal
  codes and outcomes rather than by traffic (ADR-0010 §7).

**Money is integer minor units** against `storefronts.currency` (ADR-0000 §8) — and in slice 1
every amount here is **display only**. The pricing engine recomputes the total from its own
state when the cart lands, and these are never the numbers charged (ADR-0010 §8).

**Nothing in this module soft-deletes, and that is a decision.** `TenantBase` supplies
`deleted_at`, but `zone_postal_codes` carries `UNIQUE (organization_id, postal_code)` over live
and dead rows alike — a soft-deleted mapping would keep occupying its postal code and the next
import would conflict with a row nobody can see. So a zone and a mapping are removed for real,
a zone with mappings refuses to be removed at all, and `deleted_at` stays null. Selectors filter
on it anyway, because the day something does set it the filter has to already be there.

**Every index leads with `organization_id`** — except one, and the exception is the whole
reason this service needs a bypass reader. See `Storefront.__table_args__`.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "storefront"

#: A store is invisible to the internet until it is `published`, so a merchant maps zones and
#: uses the tester before any buyer can ask. `suspended` exists for the entitlement-lapse
#: consumer ADR-0010 §4 names, describes, and explicitly does **not** build or own.
STORE_STATUSES = ("draft", "published", "suspended")

#: Named for what the **buyer does**, not for merchant marketing words like "quick" and
#: "standard", which change and would then be either wrong in the enum or migrated in every
#: tenant's data (ADR-0010 §3).
#:
#: `SLOTTED` is in the enum from day one and **refused by the writer until slice 2**: shipping a
#: half-rendered slot picker in front of a real buyer is worse than a feature that is honestly
#: absent, and hiding the value would make slice 2 a schema change instead of a validation one.
ZONE_MODES = ("SLOTTED", "PROMISED", "UNSERVED")

#: `DURATION` renders "in about 30 minutes"; `DATE_OFFSET` renders "by Thursday 25 September".
#: The server resolves both from the **store's** timezone and returns a minute count or an ISO
#: date — never prose, and never the browser's clock (ADR-0000 "Also settled", ADR-0010 §3).
PROMISE_KINDS = ("DURATION", "DATE_OFFSET")

#: What a check is counted as. `UNSERVED` is split into its three reasons because "we decided
#: not to" and "we have not got round to it" are different facts, and the demand report is worth
#: far more when it can tell them apart (ADR-0010 §3).
DEMAND_OUTCOMES = ("SERVED", "NO_MAPPING", "EXCLUDED", "ZONE_INACTIVE")

#: Wide enough for a UK or Canadian postal code, so a second country is a validator change
#: rather than a migration (ADR-0010 §2). The validator itself is **not** generalised today.
POSTAL_CODE_LENGTH = 16


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Storefront(Base, TenantBase):
    """One merchant's published store identity.

    Four finished columns and one placeholder. The store document, theme, pages and catalogue
    are the storefront-document ADR's, which **extends** this table rather than replacing it.
    """

    __tablename__ = "storefronts"

    #: The store's published identity, known to the SPA at build or runtime config. It is what
    #: an anonymous check names in its **request body** — never the Host header, because
    #: `_slug_from_host` is a subdomain heuristic that returns `None` for a two-label domain and
    #: `shop` for `shop.merchant.com`, and every storefront's end state is a custom domain
    #: (ADR-0010 §4).
    #:
    #: Slugs are enumerable by anyone, deliberately and irreversibly: a merchant publishes both
    #: the slug and the delivery map on their own website to every visitor. It is a shopfront
    #: sign.
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    #: draft | published | suspended. The anonymous route 404s unless this is `published`.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    #: ISO-4217. Every `*_minor` amount on a zone is in this currency (ADR-0000 §8).
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: IANA, e.g. `Asia/Kolkata`. **This is what a promised date is computed from** — a buyer in
    #: Singapore looking at a Mumbai store must see the Mumbai answer, so the browser's timezone
    #: is never an input (ADR-0000 "Also settled").
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    #: Slice 2. Nullable, unindexed, and unread by anything in slice 1 — its unique partial
    #: index lands with custom-domain resolution, per ADR-0010 §10's index-timing register.
    custom_domain: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        # **The one index in this design that does not lead with the tenant column**, and the
        # reason this service needs its own bypass reader. The public slug lookup runs before
        # any tenant is known — that is what an anonymous request has instead of a token — so
        # there is no `organization_id` to lead with. Global uniqueness is also what makes a
        # slug an identity rather than a per-tenant label (ADR-0010 §10).
        UniqueConstraint("slug", name="uq_storefronts_slug"),
        # One storefront per organization in slice 1, because `GET/PUT /storefront/store` is
        # singular. **Not in ADR-0010 §10's index-timing register**, which enumerates access
        # paths; this is a correctness constraint, and without it a second row is invisible to
        # the singular route until a merchant is shown settings that are not the ones they
        # edited. A multi-store ADR drops it; nothing else about this schema changes when it
        # does.
        UniqueConstraint("organization_id", name="uq_storefronts_organization_id"),
        CheckConstraint(
            "status IN ('draft', 'published', 'suspended')",
            name="status",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'", name="slug_shape"),
    )


class DeliveryZone(Base, TenantBase):
    """A named set of postal codes, plus the behaviour that applies to all of them.

    The zone carries the behaviour rather than the postal code, so a merchant edits one row to
    change what happens to two hundred areas, and the hot lookup stays a single-row probe.
    """

    __tablename__ = "delivery_zones"

    #: The merchant's handle for the zone, and what the CSV import's `zone_code` column resolves
    #: against. Unique per organization.
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: SLOTTED | PROMISED | UNSERVED. `SLOTTED` is refused by the writer until slice 2.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Null unless `mode = 'PROMISED'`, and then one of DURATION | DATE_OFFSET.
    promise_kind: Mapped[str | None] = mapped_column(String(16))
    #: DURATION: "in about 30 minutes".
    promise_minutes: Mapped[int | None] = mapped_column(Integer)
    #: DATE_OFFSET: today + this many days, or one more if the local cutoff has passed.
    promise_offset_days: Mapped[int | None] = mapped_column(Integer)
    #: Local to `storefronts.timezone`, never to the buyer's browser.
    promise_cutoff_time: Mapped[dt.time | None] = mapped_column(Time)

    #: **Display only in slice 1.** A card saying "Yes — ₹29 delivery, free over ₹499" is a
    #: materially better answer than "Yes", and it is three columns. The rule that must not be
    #: broken: the pricing engine recomputes from its own state at checkout and *this service's
    #: numbers are never the ones charged*. Two sources of truth for money is how a customer is
    #: billed something the page did not say, so when the cart lands one of the two stops being
    #: authoritative — and it is this one (ADR-0010 §8).
    delivery_fee_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Null means "no free-delivery threshold".
    free_delivery_above_minor: Mapped[int | None] = mapped_column(BigInteger)
    min_order_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    #: A zone switched off answers `UNSERVED` with reason `zone_inactive` — distinct in the
    #: response and in the demand report from "mapped and excluded" and from "never mapped".
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Free text shown to the buyer alongside the answer, e.g. "Orders after 6pm ship next day".
    buyer_note: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_delivery_zones_org_code"),
        CheckConstraint(
            "mode IN ('SLOTTED', 'PROMISED', 'UNSERVED')",
            name="mode",
        ),
        CheckConstraint(
            "promise_kind IS NULL OR promise_kind IN ('DURATION', 'DATE_OFFSET')",
            name="promise_kind",
        ),
        # The promise has to be coherent *in the database*, not only in the writer. A
        # `PROMISED` zone with no promise renders as a blank answer to a real buyer, and the
        # one path that could produce one — a later admin script, a backfill, a fix-it-by-hand
        # UPDATE — is exactly the path that skips the service layer.
        CheckConstraint(
            "(mode <> 'PROMISED' AND promise_kind IS NULL)"
            " OR (mode = 'PROMISED' AND promise_kind = 'DURATION'"
            "     AND promise_minutes IS NOT NULL AND promise_minutes > 0)"
            " OR (mode = 'PROMISED' AND promise_kind = 'DATE_OFFSET'"
            "     AND promise_offset_days IS NOT NULL AND promise_offset_days >= 0"
            "     AND promise_cutoff_time IS NOT NULL)",
            name="promise_is_complete",
        ),
        CheckConstraint(
            "delivery_fee_minor >= 0 AND min_order_minor >= 0"
            " AND (free_delivery_above_minor IS NULL OR free_delivery_above_minor >= 0)",
            name="money_is_not_negative",
        ),
    )


class ZonePostalCode(Base, TenantBase):
    """One postal code, in exactly one zone, for one organization.

    Stored normalised — upper-cased with non-alphanumerics stripped — by `domain.normalise`.
    """

    __tablename__ = "zone_postal_codes"

    #: An in-schema foreign key, which is allowed; only cross-schema ones are banned.
    #: `RESTRICT` rather than `CASCADE`: deleting a zone with postal codes still mapped to it
    #: would silently un-serve every one of them, and a merchant discovering that from a buyer
    #: is the failure this table exists to prevent. The service layer refuses first, with a
    #: message naming the count; this is the net under it.
    zone_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.delivery_zones.id", ondelete="RESTRICT"),
        nullable=False,
    )
    postal_code: Mapped[str] = mapped_column(String(POSTAL_CODE_LENGTH), nullable=False)

    __table_args__ = (
        # **The hot path.** Every serviceability check is this one probe, and it is also the
        # `merge` import's conflict target. Leading with `organization_id` is not optional: the
        # row policy ANDs `organization_id = storefront.current_org()` into every query, so an
        # index that did not lead with the tenant column would scan the whole platform's rows
        # and discard almost all of them.
        UniqueConstraint(
            "organization_id", "postal_code", name="uq_zone_postal_codes_org_postal_code"
        ),
        # The admin's "show me this zone's postal codes", and the `replace` import's delete.
        Index("ix_zone_postal_codes_org_zone", "organization_id", "zone_id"),
    )


class ServiceabilityDemand(Base, TenantBase):
    """How many times each postal code was asked about, per day, per outcome.

    **The only demand signal a merchant has before they have a single order**, and the strongest
    argument for shipping slice 1 on its own: "these postal codes were asked for and we said no,
    this many times".

    Written in the **same transaction as the read**, on purpose: the row returned and the row
    counted are then the same decision by construction, and there is no "recorded served,
    returned unserved" drift to debug. If the upsert fails the whole request fails with a 503,
    which the client renders as *could not check* — never as *not served* (ADR-0010 §7, §13).

    Retention is 400 days rather than 365, so a merchant can compare this September with last
    September. The sweep is named in ADR-0010 §7 and is **not built in slice 1**.
    """

    __tablename__ = "serviceability_demand"

    #: The date in the **store's** timezone, so a merchant's day boundary is their own.
    check_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    postal_code: Mapped[str] = mapped_column(String(POSTAL_CODE_LENGTH), nullable=False)
    #: SERVED | NO_MAPPING | EXCLUDED | ZONE_INACTIVE.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    checks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)

    __table_args__ = (
        # The upsert's conflict target. A second index on `(organization_id, check_date DESC)`
        # for the report is **rejected**, not deferred: this unique index's
        # `(organization_id, check_date)` prefix already serves the report's range scan, and a
        # second index would be write amplification on the hottest write in the service
        # (ADR-0010 §10).
        UniqueConstraint(
            "organization_id",
            "check_date",
            "postal_code",
            "outcome",
            name="uq_serviceability_demand_org_date_postal_outcome",
        ),
        CheckConstraint(
            "outcome IN ('SERVED', 'NO_MAPPING', 'EXCLUDED', 'ZONE_INACTIVE')",
            name="outcome",
        ),
        CheckConstraint("checks > 0", name="checks_positive"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    **Empty in slice 1, deliberately.** ADR-0010 §14 publishes nothing: an event type with no
    consumer is a contract kept compatible forever in exchange for nothing (ADR-0008 §3). The
    table stays until the order engine needs something from it.
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it.

    **No row per serviceability check.** It is an anonymous read of published data at page-view
    rates, and auditing it would bury every real entry (ADR-0010 §14).
    """

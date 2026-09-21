"""Tables owned by the storefront service (ADR-0010 §10).

Everything here lives in the `storefront` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). Links **within** the schema — `zone_postal_codes.zone_id` — are real
foreign keys, because only *cross-schema* ones are banned.

Four tables, and what each is for:

* **storefronts** — the product's identity row, and the only thing a stranger's request can
  start from. ADR-0010 finished `slug`, `status`, `currency` and `timezone`; ADR-0011 adds the
  **brand** (`name`, `tagline`, `logo_text`) and makes `custom_domain` writable as a *stated
  intention*. The store document, theme, pages and catalogue are still a later ADR's and are
  deliberately not stubbed here.
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
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
#:
#: **These are states, not a field a merchant types** (ADR-0011 §4). `draft ⇄ published` are the
#: two a merchant may reach, and only through the named `publish` / `unpublish` transitions —
#: never as a value on the settings form, because a settings save that can silently put a store
#: in front of the internet is the failure a dropdown produces by default. `suspended` belongs
#: to the entitlement-lapse consumer; no merchant-facing route can set it or leave it.
STORE_STATUSES = ("draft", "published", "suspended")

#: How far a claimed custom domain has got. **Only `unverified` is reachable today**, because
#: nothing in this platform serves a custom domain: there is no ingress, no TLS termination and
#: no host-based routing anywhere in the repository, and this column is read by nothing. The
#: value is stored so a merchant reading their own settings cannot conclude their site is live
#: (ADR-0011 §5).
#:
#: `verified` is in the enum from day one and refused by the writer, exactly as `SLOTTED` is in
#: `ZONE_MODES`: the hosting slice that adds DNS verification is then a validation change and a
#: new endpoint, not a migration against a table with live tenant rows.
CUSTOM_DOMAIN_STATUSES = ("unverified", "verified")

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

#: Brand text lengths. All three are **merchant-authored strings rendered into a public page**,
#: so each is bounded here, in the column, and in the schema — a limit that exists only in
#: pydantic is a limit a backfill walks past (ADR-0011 §6).
#:
#: The numbers are the renderer's, not round numbers: the header wordmark takes
#: `logoText.slice(0, 3)` and the favicon takes two characters, so twelve is already generous
#: for a wordmark and small enough that it cannot become a sentence. A store name is a shop
#: sign, and a tagline is one line of copy under it.
STORE_NAME_LENGTH = 80
TAGLINE_LENGTH = 160
LOGO_TEXT_LENGTH = 12

#: 253 is the longest a fully-qualified domain name can be. The column is wider than the
#: validator so a later internationalised-domain decision (store the punycode form, or store
#: both) is a validator change rather than an ALTER on a live table.
CUSTOM_DOMAIN_LENGTH = 255

#: The theme document (ADR-0011 §3): a `StoreDoc` minus the brand, which ADR-0011 moved onto
#: `storefronts`. One row per organization, keyed the same way `storefronts` is.
THEME_MODES = ("trade", "retail")
THEME_KEY_LENGTH = 40
#: Bounds `tokens` + `pages`, serialised, so a merchant's editor cannot grow one document
#: without limit. `domain.validate_theme_document` is what checks it before a write.
MAX_THEME_DOCUMENT_BYTES = 750_000
MAX_THEME_PAGES = 60

#: Catalogue lengths — column limits mirrored in `schemas.py`, same discipline as the brand
#: text above: a limit that exists only in pydantic is one a backfill can walk past.
CATEGORY_KEY_LENGTH = 40
CATEGORY_LABEL_LENGTH = 80
CATEGORY_BLURB_LENGTH = 200
IMAGE_PATH_LENGTH = 255
PRODUCT_CODE_LENGTH = 40
PRODUCT_NAME_LENGTH = 160
PRODUCT_BRAND_LENGTH = 80
PRODUCT_SPEC_LENGTH = 200
PRODUCT_UNIT_LENGTH = 40
PRODUCT_MOQ_UNIT_LENGTH = 24
PRODUCT_STATUSES = ("draft", "active", "archived")
VARIANT_LABEL_LENGTH = 40

#: Enquiries — this product's order-like flow (ADR-0000 "Also settled": no cart, no checkout).
ENQUIRY_STATUSES = ("new", "contacted", "won", "lost")
ENQUIRY_NAME_LENGTH = 160
ENQUIRY_COMPANY_LENGTH = 160
ENQUIRY_GSTIN_LENGTH = 15
ENQUIRY_QUANTITY_LENGTH = 40


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Storefront(Base, TenantBase):
    """One merchant's store identity — **who the store is**, never how it looks.

    ADR-0011 moves the brand here. `name`, `tagline` and `logo_text` lived in `StoreDoc.brand`
    on the front end, inside the *theme document* — which `adoptTheme` forks. A store's name is
    its identity, not its styling, and a property of the theme a merchant happened to pick is a
    property they can lose by picking another one. What is left in the theme document, once the
    brand has gone, is genuinely a theme; it moves to `/api/v1/storefront/theme`, which is
    reserved by ADR-0011 §3 and **owned and built by the storefront-document ADR, not here**.

    The store document, pages and catalogue are still that ADR's, and it **extends** this table
    rather than replacing it.
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
    #:
    #: **The slug is immutable once the row exists** (ADR-0011 §7). It is in every published
    #: link, it is what an anonymous check names in its request body, it keys the buyer's
    #: `localStorage`, and it is cached for five minutes — so a rename 404s every buyer for the
    #: length of that TTL and breaks every link that was ever shared, permanently. The writer
    #: refuses a change; this column stays plain because a later multi-store or
    #: rename-with-redirect ADR is what should be allowed to relax it.
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    #: The store's name — **the identity, not the theme's**. Required: a store with no name is a
    #: page with a blank header, and every surface that lists stores has to invent a label.
    name: Mapped[str] = mapped_column(String(STORE_NAME_LENGTH), nullable=False)
    #: One line under the name. Null means the merchant has not written one; the renderer
    #: decides what to show instead, and it is never this service's business.
    tagline: Mapped[str | None] = mapped_column(String(TAGLINE_LENGTH))
    #: The wordmark in the header. Null falls back to `name` **in the renderer** — stored null
    #: rather than copied from the name, so a later name change does not leave a stale wordmark
    #: nobody remembers typing.
    logo_text: Mapped[str | None] = mapped_column(String(LOGO_TEXT_LENGTH))
    #: draft | published | suspended. The anonymous route 404s unless this is `published`.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    #: ISO-4217. Every `*_minor` amount on a zone is in this currency (ADR-0000 §8).
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    #: IANA, e.g. `Asia/Kolkata`. **This is what a promised date is computed from** — a buyer in
    #: Singapore looking at a Mumbai store must see the Mumbai answer, so the browser's timezone
    #: is never an input (ADR-0000 "Also settled").
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    #: **A stated intention, not configuration that takes effect** (ADR-0011 §5).
    #:
    #: Nothing in this repository serves a custom domain: there is no ingress, no TLS
    #: termination and no host-based routing, and no deployment at all — a merchant's store
    #: exists at `localhost:4400/live` and nowhere else. Setting this records the domain the
    #: merchant intends to use. It changes nothing about reachability, and `custom_domain_status`
    #: is what stops a merchant concluding otherwise from reading their own settings back.
    #:
    #: Globally unique when present, across every tenant — see `__table_args__`.
    custom_domain: Mapped[str | None] = mapped_column(String(CUSTOM_DOMAIN_LENGTH))
    #: `unverified` whenever `custom_domain` is set, null whenever it is not. Nothing can move
    #: it off `unverified` today, and that honest dead end is the point: **serving a domain
    #: because somebody typed it into a form is how one merchant takes another's traffic**, so
    #: verification is a precondition of ever serving one, and it does not exist yet.
    custom_domain_status: Mapped[str | None] = mapped_column(String(16))

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
        # **A custom domain is claimed once, platform-wide.** Two tenants naming the same domain
        # is the one collision this table can have across organizations, and it is settled here
        # rather than by whoever writes the hosting slice: the loser gets a named 409
        # (`custom_domain_taken`), not a 500 and not a second ambiguous row.
        #
        # Partial, because null is not a claim: every storefront that has named no domain would
        # otherwise collide with every other one. Deliberately brought forward from ADR-0010
        # §10's register, which put it in slice 2 — the register's condition was "with custom
        # domains", and the field becomes merchant-writable here (ADR-0011 §5).
        Index(
            "uq_storefronts_custom_domain",
            "custom_domain",
            unique=True,
            postgresql_where=text("custom_domain IS NOT NULL"),
        ),
        # Brand text is stored trimmed and non-blank, or not at all. `'   '` is not a tagline,
        # and a value that differs from its own trimmed form is a value that renders with a gap
        # nobody can see in the form they typed it into. The writer trims first; this is what
        # makes it true for a backfill and a hand-written UPDATE as well.
        CheckConstraint(
            "length(name) > 0 AND name = btrim(name)",
            name="name_is_trimmed",
        ),
        CheckConstraint(
            "tagline IS NULL OR (length(tagline) > 0 AND tagline = btrim(tagline))",
            name="tagline_is_trimmed",
        ),
        CheckConstraint(
            "logo_text IS NULL OR (length(logo_text) > 0 AND logo_text = btrim(logo_text))",
            name="logo_text_is_trimmed",
        ),
        # A domain is stored lower-cased, dot-separated, at least two labels, no scheme, no port
        # and no path — the parser's rule written again where a backfill cannot skip it.
        CheckConstraint(
            "custom_domain IS NULL OR custom_domain ~ "
            "'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'",
            name="custom_domain_shape",
        ),
        # The status exists **because** there is a domain, and only then. A domain with no
        # status would read as verified to anyone writing the hosting slice; a status with no
        # domain is a claim about nothing.
        CheckConstraint(
            "(custom_domain IS NULL AND custom_domain_status IS NULL)"
            " OR (custom_domain IS NOT NULL"
            " AND custom_domain_status IN ('unverified', 'verified'))",
            name="custom_domain_status",
        ),
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


class ThemeDocument(Base, TenantBase):
    """One merchant's `StoreDoc`, minus the brand (ADR-0011 §2 moved that onto `storefronts`).

    Reserved at `/api/v1/storefront/theme` by ADR-0011 §3. `tokens` and `pages` are the
    front end's `ThemeTokens` and `PageDoc[]` — opaque JSON to this service, validated only at
    the envelope (`theme_key`, `mode`, size) by `domain.validate_theme_document`; a section's
    internal shape is the editor's business, not the database's.

    `version` is optimistic concurrency: `PUT` fails with a named 409 when the caller's
    `version` does not match the stored one, so a second tab saving over a stale copy is a
    message rather than a silent overwrite.
    """

    __tablename__ = "theme_documents"

    theme_key: Mapped[str] = mapped_column(String(THEME_KEY_LENGTH), nullable=False)
    #: trade | retail — copied from the theme at adopt time; the front end forks it and this
    #: service never resolves it back to a theme.
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    tokens: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    pages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        # One **live** theme document per organization — the singular `GET/PUT
        # /storefront/theme`. Partial, the same shape `categories.key` and `products.code`
        # use: a soft-deleted document ("Remove this store" in Gallery) must not block a fresh
        # adopt, or a merchant who removes their theme could never adopt another one.
        Index(
            "uq_theme_documents_organization_id_live",
            "organization_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("mode IN ('trade', 'retail')", name="mode"),
        CheckConstraint("length(theme_key) > 0", name="theme_key_not_blank"),
        CheckConstraint("version > 0", name="version_positive"),
    )


class Category(Base, TenantBase):
    """One catalogue category. Merchant-owned vocabulary — the front end's `CategoryKey` was a
    compile-time constant; this table is what makes it data (ADR-0000 §10, gap 3).

    `key` is the slug a product's `category_id` resolves to and the client renders as
    `Category.key` / `Product.category`. Unique per organization **among live rows only**, so a
    merchant can delete "seasonal" and create a new category with the same key later.
    """

    __tablename__ = "categories"

    key: Mapped[str] = mapped_column(String(CATEGORY_KEY_LENGTH), nullable=False)
    label: Mapped[str] = mapped_column(String(CATEGORY_LABEL_LENGTH), nullable=False)
    blurb: Mapped[str | None] = mapped_column(String(CATEGORY_BLURB_LENGTH))
    #: Root-relative, `/a/...` (ADR-0009 §6) — resolved to an absolute address at render time,
    #: never stored as one.
    image_path: Mapped[str | None] = mapped_column(String(IMAGE_PATH_LENGTH))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Partial: reuse of a key is allowed once the row that held it is gone, so a merchant
        # is never permanently blocked from a slug they deleted.
        Index(
            "uq_categories_org_key_live",
            "organization_id",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("key ~ '^[a-z0-9][a-z0-9-]*$'", name="key_shape"),
        Index("ix_categories_org_active", "organization_id", "is_active"),
    )


class Product(Base, TenantBase):
    """One catalogue line. Field-for-field against `frontend/product-storefront/src/api/types.ts`
    `Product`, minus what the client derives (`variants[0]` pricing) or resolves (`category`,
    `currency`) at read time (gap 3).

    Money is `price_min_minor` / `price_max_minor` — a band, matching the front end's "from"
    pricing — plus an optional `mrp_minor`, checked never to sit below the band's floor.
    `currency` is not a column here: it is `storefronts.currency`, read once per store rather
    than repeated on every line.
    """

    __tablename__ = "products"

    code: Mapped[str] = mapped_column(String(PRODUCT_CODE_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(PRODUCT_NAME_LENGTH), nullable=False)
    brand: Mapped[str] = mapped_column(String(PRODUCT_BRAND_LENGTH), nullable=False, default="")
    #: In-schema FK — allowed; only cross-schema FKs are banned. `RESTRICT`: a category with
    #: live products refuses to delete rather than silently orphaning them (services.py enforces
    #: the friendlier 409 first; this is the net under it).
    category_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    spec: Mapped[str] = mapped_column(String(PRODUCT_SPEC_LENGTH), nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(PRODUCT_UNIT_LENGTH), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text)
    price_min_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_max_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Printed MRP. **Never a stored discount** — the percentage is derived at render time so it
    #: cannot disagree with the price it was computed from (ADR-0000 §8; `types.ts` says the same).
    mrp_minor: Mapped[int | None] = mapped_column(BigInteger)
    gst_inclusive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    moq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    moq_unit: Mapped[str] = mapped_column(
        String(PRODUCT_MOQ_UNIT_LENGTH), nullable=False, default=""
    )
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Door-to-door minutes — the quick-commerce promise. Null: no delivery promise for this line.
    delivery_eta_minutes: Mapped[int | None] = mapped_column(Integer)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_path: Mapped[str | None] = mapped_column(String(IMAGE_PATH_LENGTH))
    #: draft | active | archived. Only `active` lines of a `published` store are ever shown to
    #: an anonymous buyer (`app/api/public.py`).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_products_org_code_live",
            "organization_id",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "price_min_minor >= 0 AND price_max_minor >= price_min_minor",
            name="price_band_is_coherent",
        ),
        CheckConstraint(
            "mrp_minor IS NULL OR mrp_minor >= price_min_minor", name="mrp_not_below_price"
        ),
        CheckConstraint("moq >= 1", name="moq_positive"),
        CheckConstraint("lead_time_days >= 0", name="lead_time_not_negative"),
        CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name="status",
        ),
        Index("ix_products_org_category", "organization_id", "category_id"),
        Index("ix_products_org_status", "organization_id", "status"),
    )


class ProductVariant(Base, TenantBase):
    """One pack size of a product — "500 g", "1 kg" — matching `types.ts`' `ProductVariant`
    field for field. `product_id` is `CASCADE`: a variant has no identity a buyer can address
    independently of its product (unlike `zone_postal_codes`, which is `RESTRICT`), so removing
    the product removes its variants with it.
    """

    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.products.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(VARIANT_LABEL_LENGTH), nullable=False)
    price_min_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_max_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mrp_minor: Mapped[int | None] = mapped_column(BigInteger)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Display order. `position = 0` is the default pack — `services.py`'s invariant is that the
    #: product's own price band and MRP equal this row's (`types.ts:100-104`).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "price_min_minor >= 0 AND price_max_minor >= price_min_minor",
            name="price_band_is_coherent",
        ),
        CheckConstraint(
            "mrp_minor IS NULL OR mrp_minor >= price_min_minor", name="mrp_not_below_price"
        ),
        Index("ix_product_variants_org_product", "organization_id", "product_id"),
    )


class Enquiry(Base, TenantBase):
    """A buyer's line list, sent as an enquiry — this product's "order" (ADR-0000 "Also
    settled": no cart, no checkout, no pricing engine; gap 6).

    **Append-only content.** Only `status` ever changes after creation — the enquiry itself is
    what the buyer sent, and correcting it is a new row, not an edit, exactly as ADR-0000
    requires of an append-only ledger.

    Buyer contact fields are PII, sealed the way `services/customer` seals a contact's phone
    (`models.py:157`): `<field>_masked` is what an ordinary list or detail read returns,
    `<field>_enc` is the AES-256-GCM envelope `platform_core.pii.seal` produces, and nothing in
    this table stores a raw value in the clear. `email` and `phone` are both PII on a b2b
    enquiry — a buyer's business email is still personally identifying — so both are sealed;
    `name` and `company` are not classified sensitive and are stored in the clear, matching the
    front end's own form (`EnquiryForm.tsx`), which treats them as the business's own words
    about itself rather than a contact channel.
    """

    __tablename__ = "enquiries"

    name: Mapped[str] = mapped_column(String(ENQUIRY_NAME_LENGTH), nullable=False)
    company: Mapped[str] = mapped_column(String(ENQUIRY_COMPANY_LENGTH), nullable=False)
    email_masked: Mapped[str] = mapped_column(String(254), nullable=False, default="")
    email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    phone_masked: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    gstin: Mapped[str | None] = mapped_column(String(ENQUIRY_GSTIN_LENGTH))
    requirement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity: Mapped[str] = mapped_column(
        String(ENQUIRY_QUANTITY_LENGTH), nullable=False, default=""
    )
    #: The line list, as the buyer built it: `[{"product_id": ..., "name": ..., "quantity": ...}]`.
    #: Opaque JSON — a product referenced here may since have been deleted, and that is fine: an
    #: enquiry is a record of what was asked for, not a live join.
    lines: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    #: new | contacted | won | lost. `domain.assert_enquiry_transition` is the state machine.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")

    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'contacted', 'won', 'lost')",
            name="status",
        ),
        Index("ix_enquiries_org_status", "organization_id", "status"),
        Index("ix_enquiries_org_created", "organization_id", "created_at"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe.

    **Non-empty as of gap 6**: `enquiry.created` is published after commit so a notification
    consumer can pick it up. Nothing else in this service publishes yet — ADR-0010 §14's
    reasoning still holds for the rest: an event type with no consumer is a contract kept
    compatible forever in exchange for nothing (ADR-0008 §3).
    """


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it.

    **No row per serviceability check.** It is an anonymous read of published data at page-view
    rates, and auditing it would bury every real entry (ADR-0010 §14).
    """

"""Tables owned by the payment service — mandates, charges, refunds, and the webhook inbox.

Everything here lives in the `payment` schema and nothing outside it may hold a foreign key
into it (ADR-0000 §10). A charge points at a billing invoice and a subscription by plain UUID,
never a constraint that crosses a schema boundary.

`payment` is deliberately the most isolated commerce service (ADR-0004 §1, §6): it carries the
provider-specific truth — *what was actually collected*, and the messy reality behind it — so a
provider's constraints never leak into the rest of the platform. The seam is built against the
**more constrained** provider (Razorpay): mandate caps, multi-day eMandate activation, and no
native idempotency key, so a later Stripe adapter fits inside the same shape (see
`app/providers/`).

Four business tables, and the tenancy/immutability discipline on each is a security decision
written out per table (ADR-0002 §5):

* **`mandates`** — a customer's standing authorization to be debited, tenant-scoped. Carries the
  Razorpay cap (`max_amount_minor`) and the eMandate activation moment (`activated_at`), which is
  days after creation for a multi-day mandate.
* **`charges`** — one collection attempt against an invoice, tenant-scoped. `idempotency_key` is
  **platform-supplied** (Razorpay has no native one, ADR-0004 §6): unique per organization, so
  raising the same charge twice is one row. Mutable through its small state machine
  (`created → authorized → captured | failed`).
* **`refunds`** — **append-only** (ADR-0000 §9). A correction is a new row; a refund is never
  edited or deleted. Enforced below the application by a trigger (see the append-only migration).
* **`provider_events`** — the **append-only** webhook inbox and the reconciliation source of
  truth. Idempotent on `provider_event_id` (unique per organization), so a provider re-delivering
  a webhook lands exactly one row and reconciles exactly once.

**Two payees (ADR-0024 §8, A4).** Everything above was built for OpsGrids collecting its own
subscription charges from tenants — `payee = 'platform'`. Tenant payment collection (`app/
collections/`) is the opposite direction: a tenant's own customer paying the tenant, into the
tenant's own Razorpay account — `payee = 'merchant'`. The same `charges`, `refunds` and
`provider_events` tables carry both, told apart by `payee`, and two more tables exist for the
merchant side only:

* **`merchant_accounts`** — one per organization: the tenant's own provider key id and its key
  secret and webhook secret, **sealed** (`platform_core.pii.seal`); only masked hints are ever
  read back.
* **`status_history`** — **append-only**: every state a merchant charge or refund has been in,
  and what moved it there (the API, a verified webhook, a server-to-server status check, the
  expiry sweep). The charge's own `state` column is the current truth; this is how it got there.

A merchant charge never emits `commerce.payment.*` and is never picked up by the platform's
stuck-charge sweep: a tenant's customer's failed UPI payment must never suspend the tenant's
own subscription (ADR-0024 §8).

The platform tables (`outbox`, `audit_logs`, `processed_events`) come from the scaffold.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from platform_core.audit import AuditLogMixin
from platform_core.events import OutboxMixin, ProcessedEventMixin
from platform_core.models import Base as _Base
from platform_core.models import TenantBase

SCHEMA = "payment"

# --- charge states -------------------------------------------------------------------
#: A charge is born `created`, moves to `authorized` when the provider accepts it, and reaches a
#: terminal `captured` or `failed` when the money moves (or does not). Kept as data here and
#: asserted by the pure state machine in `domain.py`; the closed set is pinned by a CHECK too.
CHARGE_CREATED = "created"
CHARGE_AUTHORIZED = "authorized"
CHARGE_CAPTURED = "captured"
CHARGE_FAILED = "failed"
#: Merchant (collection) charges only: the operator abandoned a checkout nobody paid, or the
#: expiry sweep closed one that stayed unpaid past `collection_checkout_ttl_seconds`. Both are
#: this platform's own decision, so a provider capture that lands later is still recorded — the
#: money moved (`app.collections.domain`). A CHECK keeps them off platform charges.
CHARGE_CANCELLED = "cancelled"
CHARGE_EXPIRED = "expired"
CHARGE_STATES = (
    CHARGE_CREATED,
    CHARGE_AUTHORIZED,
    CHARGE_CAPTURED,
    CHARGE_FAILED,
    CHARGE_CANCELLED,
    CHARGE_EXPIRED,
)

# --- charge flows (ADR-0029) ----------------------------------------------------------------
#: How a platform charge is collected. `debit`: OpsGrids debits the customer's saved mandate (the
#: billing run, the dunning ladder) — the only flow before Phase A, and the stuck-charge sweep's.
#: `checkout`: the customer pays an invoice themselves in the provider's hosted checkout. A
#: checkout charge is `created` with **no provider payment** until the customer pays, exactly
#: like a merchant collection — so the stuck-charge sweep must never resume it (it would "retry"
#: a payment nobody has made, fail it as `attempts_exhausted` and suspend a customer who has not
#: yet paid). A column with a server default rather than a backfill.
FLOW_DEBIT = "debit"
FLOW_CHECKOUT = "checkout"
FLOWS = (FLOW_DEBIT, FLOW_CHECKOUT)

# --- payees (ADR-0024 §8) --------------------------------------------------------------
#: Who the money is for. `platform`: OpsGrids collecting its own subscription charge from a
#: tenant (everything that existed before collections). `merchant`: a tenant's customer paying the
#: tenant, into the tenant's own provider account (`app/collections/`). A column with a server
#: default rather than a backfill, because `refunds` and `provider_events` refuse any UPDATE.
PAYEE_PLATFORM = "platform"
PAYEE_MERCHANT = "merchant"
PAYEES = (PAYEE_PLATFORM, PAYEE_MERCHANT)

# --- merchant account states -----------------------------------------------------------
MERCHANT_ACCOUNT_ACTIVE = "active"
#: Soft-removed: the sealed secrets are wiped, `deleted_at` is set, and nothing can verify a
#: webhook or call the provider with it any more. A new account is a new row (and a new webhook
#: URL), so a removed one is never silently revived.
MERCHANT_ACCOUNT_REMOVED = "removed"
MERCHANT_ACCOUNT_STATES = (MERCHANT_ACCOUNT_ACTIVE, MERCHANT_ACCOUNT_REMOVED)
#: `test` keys (`rzp_test_…`) move no money; `live` keys (`rzp_live_…`) do, and are refused unless
#: the deployment says otherwise (`Settings.collections_allow_live_keys`).
MERCHANT_MODE_TEST = "test"
MERCHANT_MODE_LIVE = "live"

# --- mandate states ------------------------------------------------------------------
#: A Razorpay eMandate is `created`, `pending` while the bank confirms (multi-day), `active` once
#: it can be debited, and `revoked` when the customer or bank withdraws it.
MANDATE_CREATED = "created"
MANDATE_PENDING = "pending"
MANDATE_ACTIVE = "active"
MANDATE_REVOKED = "revoked"
MANDATE_STATES = (MANDATE_CREATED, MANDATE_PENDING, MANDATE_ACTIVE, MANDATE_REVOKED)

# --- refund states -------------------------------------------------------------------
REFUND_CREATED = "created"
REFUND_PROCESSED = "processed"
REFUND_FAILED = "failed"
REFUND_STATES = (REFUND_CREATED, REFUND_PROCESSED, REFUND_FAILED)

# --- inbox dispositions --------------------------------------------------------------
#: What the platform did with a verified webhook, decided **before** the inbox row is written — the
#: table is insert-only, so a disposition can never be back-filled.
#:
#: `processed`  reconciliation ran without meeting a permanent condition (it may still have been a
#:              no-op: an intermediate event, a redelivered outcome). The default.
#: `ignored`    authentic, and there is nothing here for this platform to reconcile — the charge it
#:              names is not one of this organization's. Nothing for an operator to do.
#: `conflict`   authentic, and it contradicts what this platform holds — an outcome the charge's
#:              state machine forbids, or an amount or currency that is not the charge's. No state
#:              was changed; an operator should look, because money may have moved.
#:
#: Every non-`processed` row carries a stable `disposition_reason` code (`domain.REASON_*`) — never
#: free text, never any part of the body.
INBOX_PROCESSED = "processed"
INBOX_IGNORED = "ignored"
INBOX_CONFLICT = "conflict"
INBOX_DISPOSITIONS = (INBOX_PROCESSED, INBOX_IGNORED, INBOX_CONFLICT)


class Base(_Base):
    """Schema-scoped declarative base for this service."""

    __abstract__ = True


Base.metadata.schema = SCHEMA


class Mandate(Base, TenantBase):
    """A customer's standing authorization to be debited by a provider.

    Shaped to Razorpay's constraints (ADR-0004 §6): a mandate carries a **cap**
    (`max_amount_minor`) no single debit may exceed, and an **activation moment**
    (`activated_at`) that is days after creation for a multi-day eMandate — the mandate exists but
    cannot be debited until the bank confirms it. Both are first-class here because an interface
    that ignored them would be one Razorpay cannot satisfy.
    """

    __tablename__ = "mandates"

    #: Which processor holds the mandate — `razorpay` today. A string, never an enum in the DB, so
    #: adding a provider is not a migration.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's id for the mandate. Null until the provider assigns one (the stub returns a
    #: deterministic fake); unique per organization once set.
    provider_mandate_id: Mapped[str | None] = mapped_column(String(120))

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=MANDATE_CREATED)

    #: The Razorpay cap: no debit under this mandate may exceed it. Integer minor units, BIGINT
    #: because a mandate cap can be large (ADR-0000 §8 — no floats, ever).
    max_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ISO-4217, three chars. One currency per mandate.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    #: When the eMandate became debitable — days after creation for a multi-day mandate, NULL
    #: while it is still `created`/`pending`.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("max_amount_minor >= 0", name="max_amount_non_negative"),
        CheckConstraint("state in ('created', 'pending', 'active', 'revoked')", name="state"),
        # Unique per organization when present, so a provider id cannot collide across a tenant's
        # mandates. Partial (WHERE NOT NULL) because many mandates share NULL before assignment.
        Index(
            "uq_mandates_org_provider_mandate_id",
            "organization_id",
            "provider",
            "provider_mandate_id",
            unique=True,
            postgresql_where=text("provider_mandate_id IS NOT NULL"),
        ),
        # Leads with the tenant: the RLS qual is ANDed into every query.
        Index("ix_mandates_org_state", "organization_id", "state"),
    )


class MerchantAccount(Base, TenantBase):
    """A tenant's own provider account — where *its* customers' payments go (ADR-0024 §8, A4).

    One live row per organization. The provider's key id is public by design (Razorpay Checkout
    is opened in the customer's browser with it), so it is plaintext. The key secret (which can
    create orders and refunds as the tenant) and the webhook secret (which authenticates every
    callback about the tenant's money) are **sealed** with `platform_core.pii.seal`, bound by AAD
    to this row's organization and id and to the field, so a ciphertext copied onto another row
    does not open. Only `*_masked` hints — a fixed run of bullets and the last four characters,
    never the length — are ever returned by an API or written to an audit row.

    The webhook delivery URL names this row's `id` (`?account=<id>`): with one secret per merchant
    the secret has to be found *before* the body is trusted, so the account is the one thing a
    callback is resolved by before any tenant is bound. That lookup reads `id`,
    `organization_id` and `deleted_at` only, on a column-scoped BYPASSRLS grant
    (`migrations/env.py`), and never these secrets.
    """

    __tablename__ = "merchant_accounts"

    #: `razorpay` — the only provider this platform has an adapter for.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's public key id (`rzp_test_…` / `rzp_live_…`). What the browser is given.
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `test` or `live`, derived from `key_id` and never set independently of it.
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    #: Sealed. NULL only once the account is removed (the secrets are wiped, not kept).
    key_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_secret_masked: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    webhook_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    webhook_secret_masked: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=MERCHANT_ACCOUNT_ACTIVE)
    #: When the credentials were last replaced in place (the row, and so the webhook URL, kept).
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status in ('active', 'removed')", name="status"),
        CheckConstraint("mode in ('test', 'live')", name="mode"),
        CheckConstraint(
            "(status = 'active') = (key_secret_enc IS NOT NULL AND webhook_secret_enc IS NOT NULL)",
            name="secrets_present_iff_active",
        ),
        # One live account per organization. Partial, so a removed row stays as history and a
        # new one can be connected beside it.
        Index(
            "uq_merchant_accounts_org_live",
            "organization_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Charge(Base, TenantBase):
    """One collection attempt against a billing invoice. Tenant-scoped, mutable through its states.

    The `idempotency_key` is **platform-supplied** because Razorpay has no native one (ADR-0004
    §6): it is unique per organization, and the charge-create path inserts `ON CONFLICT DO
    NOTHING` against it, so raising the same charge twice — a retried billing run, a redelivered
    `commerce.invoice.finalized` — yields exactly one row and one collection attempt.

    `invoice_id`, `subscription_id` and `product_key` are how a *failure* finds what to suspend
    without payment reaching into another schema: they travel out on `commerce.payment.failed`,
    and the subscription service turns that into a suspension (ADR-0004 §5). They are plain
    references — no cross-schema foreign key (ADR-0000 §10).
    """

    __tablename__ = "charges"

    #: The billing invoice this charge collects, by plain UUID (billing owns `invoices`; no FK
    #: crosses the schema boundary). Nullable for a charge not tied to a specific invoice.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The subscription whose entitlement rides on this charge, by plain UUID. A failed charge
    #: suspends exactly this subscription (the suspend loop, ADR-0004 §5). Nullable: not every
    #: charge is for a subscription.
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    #: The product the subscription entitles — the same string the gateway routes. Carried so the
    #: failure event is self-describing without a lookup. A plain string, no FK.
    product_key: Mapped[str | None] = mapped_column(String(40))

    #: The mandate this charge debits, when one was named. `create_charge` refuses the debit
    #: unless the mandate exists in this organization, is `active`, matches the currency, and
    #: the amount is within its cap — and records which mandate authorized the debit here, so
    #: the authority a debit ran under is never a matter of reconstruction. Intra-schema FK,
    #: which the boundary rule allows (ADR-0000 §10 forbids only cross-schema keys).
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.mandates.id", name="fk_charges_mandate_id"),
    )

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's payment id. Null until the provider accepts the charge (the stub returns a
    #: deterministic fake); unique per organization once set. The webhook reconciles against it.
    provider_payment_id: Mapped[str | None] = mapped_column(String(120))

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    state: Mapped[str] = mapped_column(String(20), nullable=False, default=CHARGE_CREATED)
    #: Why a charge failed, straight from the provider — for support and the audit trail.
    failure_reason: Mapped[str | None] = mapped_column(String(200))

    #: Platform-supplied idempotency key, unique within the organization. Two creates with the
    #: same key are one charge — the property the create path is built around.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    #: The provider Order this charge's payment is created against. Razorpay has no idempotency
    #: key of its own (ADR-0004 §6): the Order, created with `receipt` = `idempotency_key` and
    #: looked up by it before any create (`services.call_provider_charge`), is what lets a resumed
    #: call find the payment a crashed one already made. Recorded at finalize.
    provider_order_id: Mapped[str | None] = mapped_column(String(120))

    # --- who may call the provider, and when (D3) -------------------------------------
    #: Calls claimed so far, the stager's own included. The stuck-charge sweep stops at
    #: `Settings.charge_max_attempts` and fails the charge, so dunning proceeds.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Earliest moment the sweep may claim another call — the bounded backoff between attempts.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: While in the future, a call is in flight and nobody else may make one: a caller's HTTP
    #: retry of the same key, or the sweep. Claimed by a conditional `UPDATE … RETURNING`.
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- tenant collections (ADR-0024 §8) ----------------------------------------------
    #: `platform` (every charge above) or `merchant` (a tenant's customer paying the tenant).
    #: What keeps a merchant charge out of the subscription dunning loop entirely.
    payee: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PAYEE_PLATFORM, server_default=PAYEE_PLATFORM
    )
    #: The tenant account a merchant charge is collected into. Set exactly when `payee` is
    #: `merchant`. Intra-schema FK (ADR-0000 §10 forbids only cross-schema keys).
    merchant_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.merchant_accounts.id", name="fk_charges_merchant_account_id"),
    )
    #: What a merchant charge is *for*, by plain reference — `operations.order` and the order's
    #: id today. Never a foreign key: the order lives in another schema (ADR-0000 §10).
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    # --- how a platform charge is collected (ADR-0029) ---------------------------------------
    #: `debit` or `checkout` — see `FLOW_CHECKOUT`. Merchant charges are always `debit`-flow rows
    #: with their own lifecycle; the column is meaningful for platform charges.
    flow: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FLOW_DEBIT, server_default=FLOW_DEBIT
    )
    #: When an unpaid checkout stops being payable. Set exactly for `checkout` charges (it is also
    #: the Order's own expiry at Cashfree). The poller closes a checkout past it that nothing paid.
    checkout_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    refunds: Mapped[list[Refund]] = relationship(back_populates="charge")

    __table_args__ = (
        # Idempotency enforced in the database, relied on via ON CONFLICT rather than a
        # read-then-write race — exactly as metering does for usage ingest.
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_charges_org_idempotency_key"
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        CheckConstraint("amount_minor >= 0", name="amount_non_negative"),
        CheckConstraint(
            "state in ('created', 'authorized', 'captured', 'failed', 'cancelled', 'expired')",
            name="state",
        ),
        CheckConstraint("payee in ('platform', 'merchant')", name="payee"),
        CheckConstraint("flow in ('debit', 'checkout')", name="flow"),
        # A checkout is a platform charge with an expiry; nothing else is a checkout.
        CheckConstraint(
            "flow = 'debit' OR (payee = 'platform' AND checkout_expires_at IS NOT NULL)",
            name="checkout_flow_shape",
        ),
        # A merchant charge always names its account and what it is for; a platform charge never
        # does — so neither can be mistaken for the other by a query that forgets `payee`.
        CheckConstraint(
            "(payee = 'merchant') = (merchant_account_id IS NOT NULL)",
            name="merchant_account_iff_merchant",
        ),
        CheckConstraint(
            "payee = 'platform' OR (reference_type IS NOT NULL AND reference_id IS NOT NULL)",
            name="merchant_charge_has_reference",
        ),
        # `cancelled` / `expired` are collection states; the platform's charge machine never
        # reaches them, and its dunning consumers must never have to reason about them.
        CheckConstraint(
            "payee = 'merchant' OR state in ('created', 'authorized', 'captured', 'failed')",
            name="platform_charge_states",
        ),
        Index(
            "ix_charges_org_reference",
            "organization_id",
            "reference_type",
            "reference_id",
            postgresql_where=text("reference_id IS NOT NULL"),
        ),
        # At most one *open* collection per thing being paid for: a second "collect" for an
        # order reuses the open one (the same provider Order, which the customer may retry
        # against) instead of opening a second checkout that could be paid twice. `failed` is
        # open on purpose — a failed attempt can be retried on the same provider Order.
        Index(
            "uq_charges_open_collection",
            "organization_id",
            "reference_type",
            "reference_id",
            unique=True,
            postgresql_where=text(
                "payee = 'merchant' AND state IN ('created', 'authorized', 'failed')"
            ),
        ),
        # The webhook reconciles a charge by its provider payment id, under a bound tenant. Scoped
        # to the provider (ADR-0029): two providers' id spaces are unrelated, and a Cashfree
        # `cf_payment_id` that happens to equal a Razorpay one must not be a conflict.
        Index(
            "uq_charges_org_provider_payment_id",
            "organization_id",
            "provider",
            "provider_payment_id",
            unique=True,
            postgresql_where=text("provider_payment_id IS NOT NULL"),
        ),
        Index("ix_charges_org_state", "organization_id", "state"),
        Index("ix_charges_org_invoice", "organization_id", "invoice_id"),
        Index(
            "uq_charges_org_provider_order_id",
            "organization_id",
            "provider",
            "provider_order_id",
            unique=True,
            postgresql_where=text("provider_order_id IS NOT NULL"),
        ),
        # The stuck-charge sweep's discovery: only unresolved charges, oldest first.
        Index(
            "ix_charges_unresolved",
            "created_at",
            postgresql_where=text("state = 'created' AND provider_payment_id IS NULL"),
        ),
        # The poller's discovery (ADR-0029): charges that have a provider Order and no terminal
        # state, by provider and flow.
        Index(
            "ix_charges_poll",
            "state",
            "provider",
            "flow",
            postgresql_where=text(
                "provider_order_id IS NOT NULL AND state IN ('created', 'authorized')"
            ),
        ),
        # At most one *open* checkout per invoice: a second "pay now" returns the first, never a
        # second Order the customer could pay as well.
        Index(
            "uq_charges_open_checkout",
            "organization_id",
            "invoice_id",
            unique=True,
            postgresql_where=text(
                "flow = 'checkout' AND state IN ('created', 'authorized')"
                " AND invoice_id IS NOT NULL"
            ),
        ),
    )


class Refund(Base, TenantBase):
    """A refund against a charge. **Append-only** (ADR-0000 §9).

    A refund is never edited or deleted: a correction is a new row, and the database enforces that
    with a `BEFORE UPDATE OR DELETE` trigger (see the append-only migration), the same discipline
    the credit ledger and audit trail use. It references its charge by an intra-schema foreign key
    (allowed — the boundary rule forbids only *cross-schema* keys, ADR-0000 §10).
    """

    __tablename__ = "refunds"

    charge_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.charges.id", name="fk_refunds_charge_id"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(120))

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: The state the refund was **recorded** in. The table is append-only, so a later provider
    #: outcome for a merchant refund (`refund.processed` / `refund.failed`) is a `status_history`
    #: row, never an edit here — `app.collections.selectors.effective_refund_states` reads both.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default=REFUND_CREATED)
    reason: Mapped[str | None] = mapped_column(String(200))
    #: See `Charge.payee`. Server default, never backfilled: this table refuses every UPDATE.
    payee: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PAYEE_PLATFORM, server_default=PAYEE_PLATFORM
    )
    #: The caller's key for a merchant refund — a retried refund request with the same key is the
    #: same refund here and, through the provider's own refund idempotency header, at the
    #: provider. NULL for a platform refund and for one the provider reported that this platform
    #: never asked for (a refund made in the merchant's own provider dashboard).
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    charge: Mapped[Charge] = relationship(back_populates="refunds")

    __table_args__ = (
        CheckConstraint("payee in ('platform', 'merchant')", name="payee"),
        Index(
            "uq_refunds_org_idempotency_key",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        # Zero refunds nothing and negative would mint money; both are refused in the service
        # too, but a CHECK is the layer a stray script cannot argue with.
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("state in ('created', 'processed', 'failed')", name="state"),
        Index("ix_refunds_org_charge", "organization_id", "charge_id"),
        # Mirrors charges/mandates: one local row per provider refund. Without it a replayed
        # provider response could be recorded twice, and the append-only trigger would make
        # the duplicate uncorrectable in place.
        Index(
            "uq_refunds_org_provider_refund_id",
            "organization_id",
            "provider",
            "provider_refund_id",
            unique=True,
            postgresql_where=text("provider_refund_id IS NOT NULL"),
        ),
    )


class ProviderEvent(Base, TenantBase):
    """The webhook inbox and the reconciliation source of truth. **Append-only**.

    Every provider callback that verifies its signature is recorded here *before* it is acted on,
    idempotently: the unique `(organization_id, provider_event_id)` means a provider re-delivering
    the same webhook — which they do, freely — lands exactly one row and reconciles exactly once.
    The row is never mutated except to stamp `processed_at` once… which is itself a mutation, so
    instead reconciliation is recorded by the charge it updates and by the outbox event it emits,
    and this table stays strictly insert-only under the append-only trigger.

    For the same reason the row states its own outcome at insert: `disposition` is `processed`, or
    — for an authentic webhook that names a charge this organization does not have, or contradicts
    the one it does (an illegal transition, a different amount or currency) — `ignored` /
    `conflict` with a stable `disposition_reason`. The webhook is answered 200 either way: the
    condition is permanent, so asking the provider to redeliver it for days would change nothing
    and record nothing. The evidence is kept here, and the conflict is audited and logged for an
    operator.

    Storing the `payload` and `signature` makes reconciliation replayable and auditable. The
    payload is the provider's body **after redaction** (ADR-0029): the table is append-only, so a
    payer's name, email, phone, UPI id or card detail must never be written to it. What is kept is
    an allow-list that still reads back as the provider's envelope (`_meta.webhook_version` records
    the shape), enough to replay a reconciliation, never enough to identify who paid.
    """

    __tablename__ = "provider_events"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The provider's unique per-delivery event id (Razorpay's `X-Razorpay-Event-Id` **header** —
    #: its body carries none), or, when the delivery has none, `derived:` + a digest of the body's
    #: own event name, entity id and timestamp. Idempotency rests on it, so it is never empty: a
    #: CHECK refuses the empty string, which as a key collapses every event an organization ever
    #: receives onto one row.
    provider_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The provider's event name, e.g. `payment.captured`, `payment.failed`.
    event_name: Mapped[str] = mapped_column(String(80), nullable=False)

    #: The charge this event reconciled against, resolved from the payload. Null if none matched.
    charge_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    #: The raw provider body, kept verbatim — the replayable, auditable truth.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The HMAC signature the provider sent, kept for audit.
    signature: Mapped[str | None] = mapped_column(String(256))

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: What was done with the event — one of `INBOX_DISPOSITIONS`; see there.
    disposition: Mapped[str] = mapped_column(
        String(16), nullable=False, default=INBOX_PROCESSED, server_default=INBOX_PROCESSED
    )
    #: Why it was not reconciled — a stable code, set exactly when `disposition` is not `processed`.
    disposition_reason: Mapped[str | None] = mapped_column(String(40))
    #: See `Charge.payee`. A merchant webhook's `payload` is **not** the provider's verbatim body:
    #: it names the tenant's own customer (email, phone, card and UPI details), and this table is
    #: append-only, so PII written here could never be erased. It is stored reduced to the fields
    #: reconciliation reads (`app.collections.domain.redact_webhook_body`); the provider keeps the
    #: full record, fetchable by the ids that are kept.
    payee: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PAYEE_PLATFORM, server_default=PAYEE_PLATFORM
    )

    __table_args__ = (
        CheckConstraint("payee in ('platform', 'merchant')", name="payee"),
        # Scoped to the provider (ADR-0029): an event id is only unique within the provider that
        # issued it, and one platform now has two of them.
        UniqueConstraint(
            "organization_id",
            "provider",
            "provider_event_id",
            name="uq_provider_events_org_event_id",
        ),
        Index("ix_provider_events_org_charge", "organization_id", "charge_id"),
        # Created NOT VALID by the migration, so a database that already holds a row keyed '' (the
        # collision this constraint exists to prevent) still upgrades; every new row is checked.
        CheckConstraint("length(provider_event_id) > 0", name="provider_event_id_not_empty"),
        CheckConstraint("disposition in ('processed', 'ignored', 'conflict')", name="disposition"),
        CheckConstraint(
            "(disposition = 'processed') = (disposition_reason IS NULL)",
            name="disposition_reason_matches",
        ),
    )


#: What moved a merchant charge or refund (`StatusHistory.source`).
HISTORY_SOURCE_API = "api"  # an operator's request, through operations
HISTORY_SOURCE_WEBHOOK = "webhook"  # a signature-verified provider callback
HISTORY_SOURCE_SYNC = "sync"  # a server-to-server status check against the provider's API
HISTORY_SOURCE_SWEEP = "sweep"  # the collection expiry sweep
HISTORY_SOURCES = (
    HISTORY_SOURCE_API,
    HISTORY_SOURCE_WEBHOOK,
    HISTORY_SOURCE_SYNC,
    HISTORY_SOURCE_SWEEP,
)


class StatusHistory(Base, TenantBase):
    """Every state a merchant charge or refund has been in, and what put it there. **Append-only**.

    `charges.state` is mutable — it is the current truth a read needs — so the path it took is
    kept here, one row per move, the same discipline `order_transitions` keeps for orders. For a
    refund it is also where its *current* state lives after it was recorded: `refunds` is
    append-only, so a later `refund.processed` / `refund.failed` from the provider is a row here
    (`app.collections.selectors.effective_refund_states`).

    Never carries a body or a person: states, a source, the provider's event id and a stable
    reason code only. Written only by `app/collections/`; platform charges keep their audit rows.
    """

    __tablename__ = "status_history"

    #: `charge` | `refund`.
    entity: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    #: NULL for the row that records the entity coming into existence.
    from_state: Mapped[str | None] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The inbox key of the webhook that moved it, when a webhook did.
    provider_event_id: Mapped[str | None] = mapped_column(String(120))
    #: A stable code (`cancelled_by_operator`, `checkout_expired`, the provider's `error_code`),
    #: never free text.
    reason: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("entity in ('charge', 'refund')", name="entity"),
        CheckConstraint("source in ('api', 'webhook', 'sync', 'sweep')", name="source"),
        Index("ix_status_history_org_entity", "organization_id", "entity", "entity_id", "at"),
    )


class OutboxEvent(Base, OutboxMixin):
    """Domain events staged in the same transaction as the change they describe."""


class ProcessedEvent(Base, ProcessedEventMixin):
    """Idempotency ledger — delivery is at-least-once."""


class AuditLog(Base, AuditLogMixin):
    """Who changed what, written inside the transaction that changed it."""

"""Request and response contracts for subscription.

This module is the OpenAPI surface: a change here is a change to the published contract, and the
frontend generates its client from it. `CatalogPlanVersion`/`CatalogPrice` are the shape of
catalog's internal read, parsed when a subscribe or change-plan resolves its pin.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


#: The longest operator note: a reason, not an essay. The transition's own `reason` column is 60
#: characters of machine code; the words live in `audit_logs.reason` (text) only — never on the
#: event, which a customer org's notification templates render whole.
NOTE_MAX_LENGTH = 500


def _strip_note(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValueError("note must not be blank")
    return value.strip()


# --------------------------------------------------------------------- catalog internal read


class CatalogPrice(BaseModel):
    """One priced component of a catalog plan version, as its internal read returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    amount_minor: int | None = None
    currency: str
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class CatalogFeature(BaseModel):
    """One row of a catalog version's feature sheet. `value` is whatever JSON catalog stored;
    this service reads exactly one key of it (`bundled_products`) and ignores the rest."""

    model_config = ConfigDict(from_attributes=True)

    feature_key: str
    value: Any = None


class CatalogPlanVersion(BaseModel):
    """A catalog plan version as the pin lookup sees it — unfiltered, including the audience
    pointer, so this service can apply the published-and-visible rule itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    product_key: str | None = None
    #: The plan's display name, which catalog's pin lookup carries beside the version. Read only to
    #: label a subscription's own plan on the customer's Subscriptions page.
    plan_name: str | None = None
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    prices: list[CatalogPrice] = Field(default_factory=list)
    #: The feature sheet, parsed so a bundle's members can be read off the version being pinned.
    features: list[CatalogFeature] = Field(default_factory=list)

    def feature_pairs(self) -> list[tuple[str, Any]]:
        return [(f.feature_key, f.value) for f in self.features]


class CatalogOfferableVersion(BaseModel):
    """One row of catalog's offerable-versions read: a plan version an organization may be moved
    onto right now — an active plan's published version, public or negotiated for exactly that
    organization, and the newest of its plan for its audience, cadence and currency (catalog's
    `selectors.offerable_versions`). Carries amounts: this service decides what a tenant sees."""

    model_config = ConfigDict(from_attributes=True)

    plan_id: uuid.UUID
    plan_code: str
    plan_name: str
    product_key: str
    plan_version_id: uuid.UUID
    version_no: int
    state: str
    billing_interval: str
    currency: str
    restricted_to_org_id: uuid.UUID | None = None
    published_at: datetime | None = None
    prices: list[CatalogPrice] = Field(default_factory=list)
    features: list[CatalogFeature] = Field(default_factory=list)


# --------------------------------------------------------------------- customer input


class ExpectedState(BaseModel):
    """What the caller believes the subscription is, compared under the row lock.

    A preview and the confirm that follows it are two requests; a customer's own change, a period
    roll or dunning can all move the subscription in between. A mismatch is a 409 `state_changed`
    — the caller re-reads and decides again, rather than confirming something they never saw.
    """

    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1, max_length=20)
    plan_version_id: uuid.UUID
    pending_plan_version_id: uuid.UUID | None = None


class CustomerExpectedState(ExpectedState):
    """The customer's picture of the subscription: `ExpectedState` plus the two facts that decide
    *when* a change lands and *whether money moves*, neither of which state, pin or pending
    captures.

    * `current_period_end` — a scheduled change lands at the end of the period (or of the claimed
      one). A renewal the billing run records moves this and nothing else, so without it a preview
      that said "moves on 5 Nov" would be confirmed after the roll and scheduled for 5 Dec.
    * `lifecycle_claim_id` — the renewal or trial conversion the billing run has claimed and not yet
      recorded, or null. A claim prices the change against the claimed period, and a claimed trial
      conversion turns a free trial switch into a paid, prorated one while `state` still says
      `trial`.

    Both are required, null included: the customer confirm sends back exactly what its preview
    returned, so a missing field is a client that never saw a preview, not an old caller to excuse.
    The staff path keeps `ExpectedState` — console builds it from the three fields it reads.
    """

    current_period_end: datetime | None
    lifecycle_claim_id: uuid.UUID | None


class SubscribeIn(BaseModel):
    """Start a subscription for a product, pinned to a catalog plan version.

    `trial_days` set asks for a trial; left null it starts `active`. **The length is the pinned
    version's, not the caller's**: the request is capped at the version's `trial_days` feature
    (14 days when the version names none), and refused if the version sells no trial. A trial is
    a *state of the subscription*, never a separate object (ADR-0004 §4).

    A bundle cannot be bought here — it is sold by the platform team through the operator path
    (docs/commerce-packaging.md).

    The version must be **on offer** to the organization now — catalog's offerable set, the same
    one change-plan enforces (ADR-0034 §3): a version a newer one of its plan has superseded is
    refused with 422 `not_offerable` even though it is still published (its existing subscribers
    stay pinned to it), so a stale page or a direct call cannot start anyone on a price the
    catalogue has moved past (ADR-0035).
    """

    product_key: str = Field(min_length=1, max_length=40)
    plan_version_id: uuid.UUID
    trial_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Ask for a trial. Capped at the plan version's own trial length.",
    )


class ChangePlanIn(BaseModel):
    """Re-pin a subscription to a different published version. The old pin is preserved on the
    transition row — never overwritten in history (ADR-0004 §2).

    The version must be one catalog offers this organization now (422 `not_offerable` otherwise),
    or the pin the subscription is already on — which withdraws a change that is waiting.
    `expected` is the state the customer's preview was computed from (`CustomerChangePlanPreviewOut.
    expected`, sent back whole); when sent, it is compared under the row lock — state, pin, pending
    change, period end and the billing run's pending claim — and a mismatch is a 409
    `state_changed`, so a confirm never applies a change, a date or a charge the customer did not
    see. Optional, so a caller that predates the preview keeps working.

    A change the OpsGrids team scheduled (a negotiated price from the next period) is not the
    customer's to withdraw or replace: while one waits, every customer change is refused 422
    `scheduled_by_opsgrids`.
    """

    plan_version_id: uuid.UUID
    expected: CustomerExpectedState | None = None


class ChangePlanPreviewIn(BaseModel):
    """The customer's question behind the Change plan confirm: *what would this change do?*
    Read-only, and answered by the same evaluation the write runs."""

    model_config = ConfigDict(extra="forbid")

    plan_version_id: uuid.UUID


class OperatorSubscribeIn(BaseModel):
    """A subscription opened by the platform team for a customer — the only way a bundle is sold,
    and the console's "Add product" (ADR-0031).

    Delegated over `/internal` by an operator surface, like a suspension. `organization_id` binds
    the transaction; `operator_id` is the accountable human on the audit row. `trial` asks for the
    version's own trial length; there is no length to type. Left false, the subscription starts
    `active` and its first period is claimed for billing in the same transaction — console asks the
    operator to acknowledge that before it sends `trial: false`.

    Refused, with a stable code, when the organization already has a row for the product in any
    state (409 `already_subscribed`), when the version is `one_time` (422 `one_time`), and when its
    currency differs from the organization's other live subscriptions (422
    `org_currency_mismatch`).
    """

    organization_id: uuid.UUID
    product_key: str = Field(min_length=1, max_length=40)
    plan_version_id: uuid.UUID
    trial: bool = False
    operator_id: str = Field(min_length=1, max_length=64)
    #: The operator's reason, in words (1-500). Stored as the audit row's `reason` only — never in
    #: the transition's 60-character machine `reason`, and never on the event, which a customer
    #: org's own notification templates can render.
    note: str | None = Field(default=None, min_length=1, max_length=NOTE_MAX_LENGTH)

    @field_validator("note")
    @classmethod
    def _note_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_note(value)


class OperatorChangePlanIn(BaseModel):
    """Re-pin a subscription on the platform team's behalf — how a bundle moves between rungs,
    and (fix round 1, PAID-UPGRADE TIMING) how a staff-set price change on the admin console
    is applied.

    `timing` is `None` for every existing caller (a bundle rung change): `_change_plan`'s own
    direction-based default decides — immediate for an upgrade, deferred to the next period for
    a downgrade, exactly as it always has. `"next_period"` forces the deferred path regardless of
    direction, which console's own pricing write asks for on every *active, paying* subscription
    (never on a trial, which has no money to defer) — an operator negotiating a new deal is not
    the same act as a customer choosing to pay more right now, and this is what stops that
    upgrade from prorating and charging a card immediately for a price nobody chose at checkout.
    """

    organization_id: uuid.UUID
    plan_version_id: uuid.UUID
    operator_id: str = Field(min_length=1, max_length=64)
    timing: Literal["next_period"] | None = None
    #: The operator's reason, in words (1-500) — see `OperatorSubscribeIn.note`. Optional here so
    #: the callers that predate it (a bundle rung change, console's price change) keep working;
    #: console's plan-switch always sends one.
    note: str | None = Field(default=None, min_length=1, max_length=NOTE_MAX_LENGTH)
    #: Optional expected-state guard (`ExpectedState`): compared under the row lock, before
    #: anything else is decided. A mismatch is a 409 `state_changed`.
    expected: ExpectedState | None = None

    @field_validator("note")
    @classmethod
    def _note_is_not_blank(cls, value: str | None) -> str | None:
        return _strip_note(value)


class OperatorExtendTrialIn(BaseModel):
    """Move a trialling subscription's end later, on the platform team's behalf (ADR-0031).

    **Absolute, never relative.** `new_trial_ends_at` is the instant the trial will end, and
    `expected_trial_ends_at` the end the operator saw — compared under the row lock (409
    `state_changed` when it moved). A retried request therefore cannot add the time twice: the
    second attempt finds the end already moved and its `expected` no longer true. Both must carry
    a timezone; a naive time would be read in whatever zone the server runs in.

    `note` is required: an extension is a concession, and its why belongs on the audit trail (and
    only there — never on the customer org's event). `extra="forbid"` — a write accepts exactly
    this shape.
    """

    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    operator_id: str = Field(min_length=1, max_length=64)
    new_trial_ends_at: AwareDatetime
    expected_trial_ends_at: AwareDatetime
    note: str = Field(min_length=1, max_length=NOTE_MAX_LENGTH)

    @field_validator("note")
    @classmethod
    def _note_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("note must not be blank")
        return value.strip()


class OperatorChangePlanPreviewIn(BaseModel):
    """The question behind the plan-switch confirm screen: *what would this change do?*

    Read-only — nothing is written, nothing is locked — and answered by the same evaluation the
    write runs, so the preview cannot say something the write then does differently. There is no
    `note` and no `expected`: it is the thing that *produces* the `expected` the write carries.
    """

    organization_id: uuid.UUID
    plan_version_id: uuid.UUID


class ChangePlanPreviewOut(BaseModel):
    """What a change-plan would do right now, without an amount.

    **No amount, deliberately.** A paid change is priced by the billing run from catalog and the
    subscription's items when it claims the period; there is no quote to read until then, and
    inventing one here would be a second pricing implementation that could disagree with the first
    invoice. What *is* knowable, and what an operator needs to decide, is the direction, when it
    lands, what it replaces, and which products it adds or removes.
    """

    #: The live state this answer was computed from — pass it back as `expected` on the write.
    expected: ExpectedState
    #: `switch` moves to a different version; `withdraw_scheduled` is a change back to the pin the
    #: subscription is on, which cancels the change that is waiting (then `direction` is null).
    action: Literal["switch", "withdraw_scheduled"] = "switch"
    #: `upgrade`, `downgrade` or `equal`, by the same list-price rule the write uses
    #: (recurring flat and per-seat components at quantity one; usage ignored — see
    #: `usage_priced`). `None` when the change is refused before a direction exists.
    direction: Literal["upgrade", "downgrade", "equal"] | None = None
    #: `now`: the pin moves in this request (a trial; an equal-price change on a paid
    #: subscription; withdrawing a scheduled change). `next_period`: it is scheduled for
    #: `effective_at` and nothing about entitlement or money changes until the billing run
    #: applies it there.
    effective: Literal["now", "next_period"] | None = None
    effective_at: datetime | None = None
    #: Applying this replaces a change that is already scheduled.
    would_clear_pending: bool = False
    entitled_added: list[str] = Field(default_factory=list)
    entitled_removed: list[str] = Field(default_factory=list)
    #: Either version carries a usage-priced component. Direction compares the recurring part
    #: only, so an operator is told when that is not the whole story.
    usage_priced: bool = False
    #: Applying now closes the usage metered so far **on the current pin, at its usage rates**, as
    #: its own invoice the billing run raises (the pin moves, so usage from here on is priced on the
    #: new one). True only when the change applies now, the time since the period began is paid
    #: time (an active subscription, or a trial whose conversion is already claimed), and the
    #: current version has a usage price. An equal-price switch is "no proration", not "nothing
    #: charged", whenever this is set.
    usage_closed_now: bool = False
    #: The time since this period began is paid time — an active subscription, or a trial whose
    #: conversion the billing run has already claimed. False for a trial with nothing claimed,
    #: where any change applies now and moves no money.
    paid: bool = False
    #: Applying this raises a **prorated charge now** for the rest of the current period: a paid
    #: upgrade on the customer's own change-plan (the difference between the new and the old
    #: recurring price, priced and invoiced by the billing run; no amount is known until then).
    #: Never true on the staff path, which defers every paid change to the next period.
    prorated_charge_now: bool = False
    #: Set (with `refusal_detail`) when the write would be refused. The preview still answers 200:
    #: a refusal is an answer to "what would happen", not a failure of the question.
    refusal_code: str | None = None
    refusal_detail: str | None = None


class CustomerChangePlanPreviewOut(ChangePlanPreviewOut):
    """The customer's preview: the same answer, with the customer's fuller `expected` to send back.

    `direction` is null whenever `paid` is false (a trial with nothing claimed): such a change is
    always now and free, so the comparison is never needed, and answering it would let a trialist
    compare any public plan's list price against a negotiated amount they can see.
    """

    expected: CustomerExpectedState


# --------------------------------------------------------------------- customer plan reads
#
# What the customer's Subscriptions page shows about plans (ADR-0034). **Amounts are the
# organization's own agreed prices only**: a version negotiated for exactly this organization
# (`restricted_to_org_id` = the caller's organization), and only to a caller who may read this
# organization's invoices. A public (list) price is never sent to a tenant here, whether or not
# the organization is on it — the owner's rule since 2026-09-24 (pricing is agreed per client;
# catalog's tenant reads carry no amounts). `amount_visible` says which case a row is, so a screen
# never has to guess why a price is missing.


class PlanPriceOut(BaseModel):
    """One priced component of the organization's own agreed price, integer minor units."""

    kind: str
    amount_minor: int | None = None
    unit: str | None = None
    unit_price_micros: int | None = None
    meter_key: str | None = None


class PlanRefOut(BaseModel):
    """A plan version, as the customer may see it."""

    plan_version_id: uuid.UUID
    plan_id: uuid.UUID | None = None
    plan_name: str | None = None
    version_no: int | None = None
    billing_interval: str | None = None
    #: Negotiated for this organization alone (an audience-of-one version, ADR-0004 §2).
    negotiated: bool = False
    #: True when `currency`, `recurring_minor` and `prices` are filled in: the version is this
    #: organization's own negotiated price and the caller may read its invoices. False for a
    #: public version — whose amounts are never sent to a tenant — or a caller without that right.
    amount_visible: bool = False
    currency: str | None = None
    #: Flat plus per-seat at quantity one, usage excluded — for display only. The preview decides
    #: direction; the billing run prices.
    recurring_minor: int | None = None
    prices: list[PlanPriceOut] | None = None


class PlanOptionOut(PlanRefOut):
    """One plan the customer may pick in Change plan, and whether this subscription can take it."""

    is_current: bool = False
    is_pending: bool = False
    #: Why this subscription cannot take it: `cadence` or `currency` (one cadence and one currency
    #: per subscription), `bundle`, `current` (it is the plan already in force) or `pending` (it is
    #: the change already scheduled). Null when it can.
    blocked_code: str | None = None
    blocked_reason: str | None = None


class PlanOptionsOut(BaseModel):
    """Everything the Change plan dialog's first step needs, from live reads.

    `options` is catalog's offerable set for this organization (active plans, published versions,
    public or negotiated for it, the newest per plan, audience, cadence and currency) — never a
    superseded, withdrawn or retired-plan version — with the current pin marked even when it is
    no longer on offer itself.
    """

    subscription_id: uuid.UUID
    product_key: str
    state: str
    #: `bundle` (a bundle's plan is changed by the OpsGrids team), `state` (only a trial or an
    #: active subscription changes plan) or `scheduled_by_opsgrids` (a change the OpsGrids team
    #: scheduled is waiting, and only they change it). Null when a change may be asked for.
    change_blocked_code: str | None = None
    current: PlanRefOut | None = None
    pending: PlanRefOut | None = None
    #: When the waiting change applies: the end of the period it waits for.
    pending_effective_at: datetime | None = None
    #: Who scheduled the waiting change: `opsgrids` (staff — a negotiated price from the next
    #: period, which the customer cannot withdraw or replace) or `customer`. Null when none waits.
    pending_scheduled_by: Literal["opsgrids", "customer"] | None = None
    options: list[PlanOptionOut] = Field(default_factory=list)


class SubscriptionPlanSummaryOut(BaseModel):
    """The plan line of one row on the Subscriptions page: what it is on, and what it moves to.

    A cancelled subscription has no scheduled change: it never reaches another period. Cancelling
    now clears one; a row cancelled before that still carries it, and it is not reported."""

    subscription_id: uuid.UUID
    current: PlanRefOut | None = None
    pending: PlanRefOut | None = None
    pending_effective_at: datetime | None = None
    #: As on `PlanOptionsOut`: `opsgrids`, `customer`, or null when nothing waits.
    pending_scheduled_by: Literal["opsgrids", "customer"] | None = None


class CancelIn(BaseModel):
    """Cancel a subscription. Immediate and terminal: access ends now, and a cancelled
    subscription cannot be reopened or replaced (one row per product per organization). `reason`
    is the customer's own words, recorded as the audit row's reason."""

    reason: str | None = Field(default=None, max_length=200)


class PaymentOutcome(BaseModel):
    """What a `commerce.payment.*` event tells this service about one collection attempt.

    Parsed from the event payload at the edge of the consumer rather than passed around as a raw
    dict, so a payload that changes shape fails at one named place. Only the fields dunning needs
    are here: enough to open a cycle and to ask for the same debit again later, and nothing about
    the provider — which processor took the money is payment's business alone (ADR-0004 §6).
    """

    invoice_id: uuid.UUID | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    #: Why a failed charge failed, as payment recorded it. Kept on a trial conversion that failed
    #: before the billing run recorded it (ADR-0005 addendum, review fix 1).
    failure_reason: str | None = Field(default=None, max_length=200)


class SuspendIn(BaseModel):
    """Operator-driven suspension, delegated from the console over /internal (a later phase).

    `organization_id` names the owning tenant so the internal handler can bind the transaction to
    it; RLS then confirms the subscription really belongs there. `operator_id` is the accountable
    human the console passes through for the audit row.
    """

    organization_id: uuid.UUID
    operator_id: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=200)


class ReactivateIn(BaseModel):
    """Operator-driven reactivation of a suspended subscription — `SuspendIn`'s inverse,
    delegated from the console over /internal.

    Same tenant-binding contract as `SuspendIn` (`organization_id` names the owning tenant, RLS
    then confirms the subscription really belongs there). Unlike `SuspendIn`, `reason` is
    **required**: lifting a hold is exactly the act whose why an audit trail must carry, and
    `extra="forbid"` because a write accepts exactly this shape and nothing smuggled in beside it.
    """

    model_config = ConfigDict(extra="forbid")

    organization_id: uuid.UUID
    operator_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


# --------------------------------------------------------------------- output


class SubscriptionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    price_id: uuid.UUID
    quantity: int


class DunningOut(BaseModel):
    """The live grace window, as the customer's own app should render it (ADR-0005).

    Present only while a cycle is open, which is exactly while the subscription is `past_due` or
    `suspended` for non-payment. It carries the two dates a banner has to be able to say out loud
    — when the next debit will be attempted, and when access stops — read from the stored
    schedule rather than recomputed, so the screen and the sweep can never disagree.
    """

    model_config = ConfigDict(from_attributes=True)

    cycle_id: uuid.UUID
    opened_at: datetime
    grace_ends_at: datetime
    next_attempt_at: datetime | None = None
    attempts_made: int
    max_attempts: int
    amount_minor: int | None = None
    currency: str | None = None


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_key: str
    plan_version_id: uuid.UUID
    state: str
    trial_ends_at: datetime | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at: datetime | None = None
    #: A downgrade waiting for the next period (ADR-0005 addendum §4): the version the
    #: subscription moves to at `current_period_end`. Null when nothing is waiting.
    pending_plan_version_id: uuid.UUID | None = None
    currency: str
    #: The products this subscription switches on. One key for an ordinary subscription; a
    #: bundle's members for a bundle — snapshotted from the pinned catalog version at subscribe
    #: and change-plan, never taken from the client.
    entitled_products: list[str] = Field(default_factory=list)
    items: list[SubscriptionItemOut] = Field(default_factory=list)
    #: Null unless a dunning cycle is open against this subscription.
    dunning: DunningOut | None = None


class SubscriptionOperatorOut(SubscriptionOut):
    """`SubscriptionOut` plus this row's own last-write instant — console's cross-tenant
    backfill discovery read (`GET /internal/v1/organizations/{id}/subscriptions`) only, never the
    customer-facing routes above.

    Deliberately not added to the base `SubscriptionOut`: those routes serialise the response
    inside the same transaction as a write that can touch `updated_at` (cancel, suspend, a
    change-plan) — `TimestampMixin.updated_at` is `onupdate=func.now()`, a server-generated value
    SQLAlchemy marks expired after such an UPDATE, and reading an expired attribute triggers an
    implicit refresh that is not awaitable in an async context (`MissingGreenlet`). The internal
    listing route this schema is for does no write in its own request — a plain
    `selectors.list_by_organization` read — so the column is never expired when this model reads
    it.

    Console's backfill (`services/console/app/backfill.py`) stamps its snapshot fold with this
    value rather than "now", so the same ordering guard a live transition event gets applies to a
    discovered snapshot too — a later real event always wins, and re-running the backfill against
    an unchanged subscription is a no-op.
    """

    updated_at: datetime
    #: While trialling: the latest instant staff may move the trial's end to
    #: (`current_period_start` + `domain.MAX_TOTAL_TRIAL_DAYS`). Null for any other state. Stated
    #: here, from the one constant, so an operator surface never restates the cap and drifts.
    trial_max_ends_at: datetime | None = None

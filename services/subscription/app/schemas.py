"""Request and response contracts for subscription.

This module is the OpenAPI surface: a change here is a change to the published contract, and the
frontend generates its client from it. `CatalogPlanVersion`/`CatalogPrice` are the shape of
catalog's internal read, parsed when a subscribe or change-plan resolves its pin.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


#: The longest operator note: a reason, not an essay. The transition's own `reason` column is 60
#: characters of machine code; the words live in `audit_logs.reason` (text) and on the event.
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


# --------------------------------------------------------------------- customer input


class SubscribeIn(BaseModel):
    """Start a subscription for a product, pinned to a catalog plan version.

    `trial_days` set asks for a trial; left null it starts `active`. **The length is the pinned
    version's, not the caller's**: the request is capped at the version's `trial_days` feature
    (14 days when the version names none), and refused if the version sells no trial. A trial is
    a *state of the subscription*, never a separate object (ADR-0004 §4).

    A bundle cannot be bought here — it is sold by the platform team through the operator path
    (docs/commerce-packaging.md).
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
    transition row — never overwritten in history (ADR-0004 §2)."""

    plan_version_id: uuid.UUID


class OperatorSubscribeIn(BaseModel):
    """A subscription opened by the platform team for a customer — the only way a bundle is sold.

    Delegated over `/internal` by an operator surface, like a suspension. `organization_id` binds
    the transaction; `operator_id` is the accountable human on the audit row. `trial` asks for the
    version's own trial length; there is no length to type.
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


class ExpectedState(BaseModel):
    """What the caller believes the subscription is, compared under the row lock.

    A preview and the confirm that follows it are two requests; a customer's own change, a period
    roll or dunning can all move the subscription in between. A mismatch is a 409 `state_changed`
    — the operator re-reads and decides again, rather than confirming something they never saw.
    """

    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1, max_length=20)
    plan_version_id: uuid.UUID
    pending_plan_version_id: uuid.UUID | None = None


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
    #: Set (with `refusal_detail`) when the write would be refused. The preview still answers 200:
    #: a refusal is an answer to "what would happen", not a failure of the question.
    refusal_code: str | None = None
    refusal_detail: str | None = None


class CancelIn(BaseModel):
    """Cancel a subscription. Immediate for the MVP; `reason` is recorded on the transition."""

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


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    from_state: str | None = None
    to_state: str
    from_plan_version_id: uuid.UUID | None = None
    to_plan_version_id: uuid.UUID | None = None
    reason: str | None = None
    occurred_at: datetime


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

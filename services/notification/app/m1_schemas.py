"""Response and request contracts for messaging analytics and cost (slice M1).

Published API surface: a change here is a contract change. Times are ISO-8601 UTC. Every money
value is `{amount_minor: int, currency: str}` — integer minor units, never a float — and every
cost is an **estimate**: `estimated` is always `true`. A message is priced by the category and
billable flag Meta reported on its delivery receipt (or its template's category until one lands),
at the rate for its recipient's market: the organization's own override or, failing that, the
platform card seeded from Meta's published INR rates (`meta-inr-2026-10-01`).

Buckets are **UTC calendar days** (`series[].date`). A range is two inclusive UTC dates.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MessageCategory = Literal["marketing", "utility", "authentication", "service"]
ChannelKey = Literal["whatsapp", "email", "sms", "push"]


class Money(BaseModel):
    amount_minor: int
    currency: str


# --------------------------------------------------------------------- WhatsApp overview


class SeriesPoint(BaseModel):
    #: The UTC calendar day, `YYYY-MM-DD`.
    date: str
    sent: int
    delivered: int
    read: int
    failed: int


class CategoryCount(BaseModel):
    category: MessageCategory
    messages: int


class StatusCount(BaseModel):
    status: Literal["read", "delivered", "sent", "failed"]
    count: int


class FailureReason(BaseModel):
    #: A Meta error code where the receipt or the refusal carried one, `http_<status>` for a
    #: refusal without one, `dispatch_failed` otherwise.
    code: str
    label: str
    count: int


class ActivityItem(BaseModel):
    kind: str
    title: str
    text: str
    at: datetime


class WhatsAppTotals(BaseModel):
    #: Active WhatsApp automation rules (`rules.active` on the whatsapp channel).
    active_automations: int
    approved_templates: int
    pending_templates: int
    #: Open conversations.
    active_conversations: int
    #: Open conversations nobody is assigned to.
    unassigned_conversations: int


class CostCategory(BaseModel):
    category: MessageCategory
    messages: int
    amount: Money


class CostSource(BaseModel):
    """What one automation or inbox conversation cost in the window (billable messages only)."""

    #: `automation` (the rule that sent it) or `conversation` (a reply in an inbox thread).
    kind: Literal["automation", "conversation"]
    #: The rule code, or the conversation id.
    key: str
    #: The rule code, or the conversation's **masked** number.
    label: str
    messages: int
    amount: Money


class RateCardRef(BaseModel):
    """Which rates produced the cost, so a reader can tell a placeholder from a real figure."""

    #: `organization` when every category is priced by the organization's own override,
    #: `platform_default` when none is, `mixed` otherwise (cannot happen through the API, which
    #: overrides all four categories together, but is stated rather than assumed).
    source: Literal["platform_default", "organization", "mixed"]
    #: True when any rate in use is a shipped placeholder.
    placeholder: bool
    #: The platform card's label (`meta-inr-2026-10-01`), `organization`, or a placeholder label.
    version: str


class Cost(BaseModel):
    total: Money
    #: Always true. Meta's list rates per market and category, without volume tiers or tax.
    estimated: Literal[True] = True
    by_category: list[CostCategory]
    rate_card: RateCardRef
    #: How many of the sent messages Meta itself priced (a receipt carried a `pricing` object);
    #: the rest are estimated from their template's category and assumed billable.
    priced_by_meta: int = 0
    #: The most expensive automations and inbox conversations, most expensive first (at most 10).
    by_source: list[CostSource] = Field(default_factory=list)


class WhatsAppOverview(BaseModel):
    #: Handed to WhatsApp and not (yet) failed: current state `sent`, `delivered` or `read`.
    sent: int
    #: Current state `delivered` or `read`.
    delivered: int
    #: Current state `read`.
    read: int
    #: Current state `failed`, at any stage (refused at dispatch or reported by a receipt).
    failed: int
    #: Inbound customer messages, in the window, on a conversation that had an outbound reply in
    #: the window. Outbound sends outside a conversation carry no conversation id, so this is a
    #: lower bound for template-driven sends.
    replies: int
    #: Added: every inbound customer message in the window, whatever it answered.
    inbound_messages: int
    #: Added: staged but not yet handed over (`pending`, `sending`), and blocked by consent.
    queued: int
    suppressed: int
    series: list[SeriesPoint]
    by_category: list[CategoryCount]
    status_breakdown: list[StatusCount]
    failures_by_reason: list[FailureReason]
    recent_activity: list[ActivityItem]
    totals: WhatsAppTotals
    cost: Cost


# --------------------------------------------------------------------- hub overview


class ChannelStatus(BaseModel):
    key: ChannelKey
    connected: bool
    detail: str | None


class ChannelCost(BaseModel):
    channel: ChannelKey
    amount: Money


class HubCosts(BaseModel):
    total: Money
    estimated: Literal[True] = True
    by_channel: list[ChannelCost]
    rate_card: RateCardRef


class HubOverview(BaseModel):
    channels: list[ChannelStatus]
    whatsapp: WhatsAppOverview
    #: E1's `email_overview` result, passed through. `null` when the email analytics module is
    #: not available in this deployment.
    email: dict[str, Any] | None
    costs: HubCosts


# --------------------------------------------------------------------- rate card


class RateOut(BaseModel):
    #: The market's calling-code prefix (`91`); `""` is every market no other row claims.
    calling_code: str = ""
    #: Meta's name for the market ("India", "North America", "Other").
    market: str = ""
    category: MessageCategory
    amount_minor_per_1000_messages: int
    currency: str
    effective_from: date
    #: `platform_default` (the platform's card) or `organization`.
    source: Literal["platform_default", "organization"]
    placeholder: bool


class RateCardOut(BaseModel):
    channel: Literal["whatsapp"]
    #: Always true: these rates produce estimates.
    estimated: Literal[True] = True
    #: True when any rate in force is a shipped placeholder.
    placeholder: bool
    version: str
    #: A human sentence for the UI; says plainly when the figures are placeholders.
    note: str
    rates: list[RateOut]


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MessageCategory
    amount_minor_per_1000_messages: int = Field(ge=0, le=10_000_000_000)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class RateCardIn(BaseModel):
    """Override the rate card: exactly one rate per category, one currency across them.

    All four categories together, because a partial override would price some categories in the
    organization's currency and the rest in the placeholder's, and one total cannot span two.
    """

    model_config = ConfigDict(extra="forbid")

    #: The first UTC day the new rates apply to. Defaults to today (UTC). Re-saving the same day
    #: replaces that day's rates; a later day adds a new price point and keeps the old one.
    effective_from: date | None = None
    rates: list[RateIn] = Field(min_length=4, max_length=4)

    @field_validator("rates")
    @classmethod
    def _one_per_category_one_currency(cls, rates: list[RateIn]) -> list[RateIn]:
        if {r.category for r in rates} != {"marketing", "utility", "authentication", "service"}:
            raise ValueError("provide exactly one rate for each of the four categories")
        if len({r.currency for r in rates}) != 1:
            raise ValueError("every rate must use the same currency")
        return rates


class PlatformRateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calling_code: str = Field(pattern=r"^[0-9]{0,4}$")
    market: str = Field(min_length=1, max_length=60)
    category: MessageCategory
    amount_minor_per_1000_messages: int = Field(ge=0, le=10_000_000_000)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")


class PlatformRateCardIn(BaseModel):
    """Staff: add or replace price points on the platform rate table (every tenant's default).

    Rows not named are left alone, so publishing a changed market is one row, not the whole card.
    """

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    #: Where the figures came from, e.g. `meta-inr-2027-01-01`. Shown to tenants as the version.
    label: str = Field(min_length=1, max_length=60)
    rates: list[PlatformRateIn] = Field(min_length=1, max_length=1000)


__all__ = [
    "ActivityItem",
    "CategoryCount",
    "ChannelCost",
    "ChannelStatus",
    "Cost",
    "CostCategory",
    "CostSource",
    "FailureReason",
    "HubCosts",
    "HubOverview",
    "Money",
    "PlatformRateCardIn",
    "PlatformRateIn",
    "RateCardIn",
    "RateCardOut",
    "RateCardRef",
    "RateIn",
    "RateOut",
    "SeriesPoint",
    "StatusCount",
    "WhatsAppOverview",
    "WhatsAppTotals",
]

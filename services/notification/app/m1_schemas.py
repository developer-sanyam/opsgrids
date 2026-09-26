"""Response and request contracts for messaging analytics and cost (slice M1).

Published API surface: a change here is a contract change. Times are ISO-8601 UTC. Every money
value is `{amount_minor: int, currency: str}` — integer minor units, never a float — and every
cost is an **estimate**: `estimated` is always `true`, and the rates behind it are the
organization's own override or, failing that, the platform's *placeholder* defaults
(`placeholder-2026-09`), which are not Meta's real rates for any country.

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


class RateCardRef(BaseModel):
    """Which rates produced the cost, so a reader can tell a placeholder from a real figure."""

    #: `organization` when every category is priced by the organization's own override,
    #: `platform_default` when none is, `mixed` otherwise (cannot happen through the API, which
    #: overrides all four categories together, but is stated rather than assumed).
    source: Literal["platform_default", "organization", "mixed"]
    #: True when any rate in use is a shipped placeholder.
    placeholder: bool
    #: `placeholder-2026-09` or `organization`.
    version: str


class Cost(BaseModel):
    total: Money
    #: Always true. Meta bills on its own schedule and country; this is messages x a rate card.
    estimated: Literal[True] = True
    by_category: list[CostCategory]
    rate_card: RateCardRef


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
    category: MessageCategory
    amount_minor_per_1000_messages: int
    currency: str
    effective_from: date
    #: `platform_default` (a shipped placeholder) or `organization`.
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


__all__ = [
    "ActivityItem",
    "CategoryCount",
    "ChannelCost",
    "ChannelStatus",
    "Cost",
    "CostCategory",
    "FailureReason",
    "HubCosts",
    "HubOverview",
    "Money",
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

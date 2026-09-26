"""Response contracts for the email analytics reads (slice E1). The OpenAPI surface of
`GET /engage/campaigns/{id}/analytics` and `GET /engage/analytics/email/overview`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeviceOpens(BaseModel):
    device: str
    opens: int


class LinkClicks(BaseModel):
    url: str
    clicks: int


class CampaignSeriesPoint(BaseModel):
    date: str
    opens: int
    clicks: int


class TrackingFlags(BaseModel):
    opens: bool
    clicks: bool


class CampaignAnalyticsOut(BaseModel):
    recipients: int
    #: Accepted by the provider (the recipient reached `sent`). Not proof of inbox placement.
    delivered: int
    #: Distinct recipients with a human open. Machine opens are stored but excluded.
    opened: int
    #: Distinct recipients with a human click.
    clicked: int
    unsubscribed: int
    #: Recipients the provider refused at send time. Asynchronous bounces are not processed.
    bounced: int
    #: No complaint feedback loop is processed: 0 means "not measured".
    complaints: int
    #: Fractions between 0 and 1 (`opened / delivered`), not percentages.
    open_rate: float
    click_rate: float
    by_device: list[DeviceOpens]
    by_link: list[LinkClicks]
    series: list[CampaignSeriesPoint]
    #: Off means "not measured", not zero.
    tracking: TrackingFlags


class EmailSeriesPoint(BaseModel):
    date: str
    sent: int
    delivered: int
    opened: int
    clicked: int
    failed: int


class EmailActivity(BaseModel):
    kind: Literal["campaign_sent", "high_open_rate", "new_subscriber", "automation_executed"]
    title: str
    text: str
    at: str


class EmailTotals(BaseModel):
    connected_accounts: int
    active_automations: int
    templates: int
    contacts: int
    subscribed_contacts: int


class EmailOverviewOut(BaseModel):
    sent: int
    #: Accepted by the provider, not proof the message reached an inbox.
    delivered: int
    opened: int
    clicked: int
    failed: int
    series: list[EmailSeriesPoint] = Field(default_factory=list)
    recent_activity: list[EmailActivity] = Field(default_factory=list)
    totals: EmailTotals

"""Wire shapes for `GET /customers/{id}/360` (slice C1).

A section is `null` when it could not be produced — the upstream failed, timed out, or the
organization does not have that product — and its name is then in `unavailable`. `null` never
means "zero": an empty-but-known section is an object with zeroes in it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: The section names that can appear in `unavailable`. Documented here so the frontend can
#: switch on them.
SectionName = Literal["crm", "orders", "finance", "support", "channels"]


class Money(BaseModel):
    amount_minor: int
    currency: str


class OwnerRef(BaseModel):
    id: str
    name: str


class Customer360Customer(BaseModel):
    id: str
    name: str
    company: str | None
    since: datetime
    status: Literal["active", "inactive"]
    #: The book has no tags column today, so this is always empty rather than invented.
    tags: list[str] = Field(default_factory=list)
    owner: OwnerRef | None
    #: Masked, exactly as `ContactOut.phone`. The real number needs the unmask route.
    phone: str
    #: Masked. `ContactOut.email` is the full work address; this view masks it.
    email: str
    city: str | None


class CrmSection(BaseModel):
    stage: str | None
    open_opportunities: int
    hot: bool


class LatestOrder(BaseModel):
    number: str
    status: str
    total: Money
    at: datetime


class OrdersSection(BaseModel):
    count_90d: int
    total_90d: Money
    latest: LatestOrder | None


class LatestInvoice(BaseModel):
    number: str
    status: str
    due_on: str


class FinanceSection(BaseModel):
    outstanding: Money
    overdue_count: int
    latest_invoice: LatestInvoice | None


class SupportSection(BaseModel):
    open_tickets: int
    total_tickets: int


class WhatsAppChannel(BaseModel):
    opted_in: bool
    window_open_until: datetime | None
    last_message_at: datetime | None
    conversations: int


class EmailChannel(BaseModel):
    subscribed: bool
    last_email_at: datetime | None


class ChannelsSection(BaseModel):
    whatsapp: WhatsAppChannel | None = None
    email: EmailChannel | None = None


class TimelineEntry(BaseModel):
    kind: str
    title: str
    at: datetime


class Customer360(BaseModel):
    customer: Customer360Customer
    crm: CrmSection | None
    orders: OrdersSection | None
    finance: FinanceSection | None
    support: SupportSection | None
    channels: ChannelsSection
    timeline: list[TimelineEntry]
    unavailable: list[SectionName]

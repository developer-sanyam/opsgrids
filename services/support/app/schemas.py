"""Request and response contracts for the support service.

This module is the published API surface — a change here is a contract change, and the product's
SPA generates its client from it. The response shapes mirror
`frontend/product-support/src/api/types.ts` field-for-field so wiring the live gateway is a flag
flip rather than a reshaping: `id` is the human code (`TK-4001`, `ag-01`), times are ISO-8601
UTC, and `assignee_id` is the agent's public ref.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Priority = Literal["urgent", "high", "normal", "low"]
TicketStatusT = Literal["new", "open", "pending", "on_hold", "solved"]
Channel = Literal["email", "chat", "web", "phone", "api"]
Satisfaction = Literal["good", "bad"]
AuthorType = Literal["customer", "agent", "system"]
RequesterKind = Literal["customer", "staff"]
AgentRole = Literal["agent", "senior_agent", "team_lead"]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class Requester(BaseModel):
    name: str
    email: str
    company: str
    kind: RequesterKind


class MessageOut(BaseModel):
    id: str
    author_type: AuthorType
    author_name: str
    at: datetime
    body: str
    internal: bool = False


class TicketOut(BaseModel):
    id: str
    subject: str
    requester: Requester
    priority: Priority
    status: TicketStatusT
    channel: Channel
    assignee_id: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    first_response_at: datetime | None
    sla_due: datetime | None
    sla_breached: bool
    satisfaction: Satisfaction | None
    messages: list[MessageOut]


class AgentOut(BaseModel):
    id: str
    name: str
    email: str
    role: AgentRole
    team: str
    open_load: int
    avg_first_response_min: int
    csat_pct: int
    solved_7d: int
    online: bool


class SlaTargetOut(BaseModel):
    priority: Priority
    first_response_min: int
    resolution_min: int


class SlaPolicyOut(BaseModel):
    name: str
    business_hours: str
    coverage: str
    targets: list[SlaTargetOut]


# --------------------------------------------------------------------- overview rollups


class VolumePointOut(BaseModel):
    label: str
    date: str
    opened: int
    solved: int


class BreakdownSliceOut(BaseModel):
    key: str
    label: str
    count: int
    pct: int


class OverviewOut(BaseModel):
    open_tickets: int
    breaching_sla: int
    first_response_median_min: int
    csat_pct: int | None
    volume: list[VolumePointOut]
    priority_breakdown: list[BreakdownSliceOut]
    status_breakdown: list[BreakdownSliceOut]


# --------------------------------------------------------------------- write contracts


class ReplyIn(BaseModel):
    """Append one message to a ticket's thread.

    `author_type` defaults to `agent` — a reply from the desk is the common case; a `system`
    note or a logged `customer` message names itself.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)
    author_type: AuthorType = "agent"
    author_name: str = Field(min_length=1, max_length=160)
    internal: bool = False


class AssignIn(BaseModel):
    """Assign a ticket to an agent, or unassign it with `assignee_id: null`."""

    model_config = ConfigDict(extra="forbid")

    assignee_id: str | None = Field(default=None, max_length=40)

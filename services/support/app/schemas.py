"""Request and response contracts for the support service.

This module is the published API surface — a change here is a contract change, and the product's
SPA generates its client from it. The response shapes mirror
`frontend/product-support/src/api/types.ts` field-for-field so wiring the live gateway is a flag
flip rather than a reshaping: `id` is the human code (`TK-4001`, `ag-01`), times are ISO-8601
UTC, and `assignee_id` is the agent's public ref.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

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


class RequesterIn(BaseModel):
    """Who a ticket is being opened for.

    A contact snapshot denormalised onto the ticket, not a link to a customer record — the same
    shape the queue reads back as `requester`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=1, max_length=254)
    company: str = Field(min_length=1, max_length=200)
    kind: RequesterKind = "customer"


class OpeningMessageIn(BaseModel):
    """One message of a new ticket's opening thread.

    The per-ticket code (`m1`, `m2`) and the timestamp are stamped by the server — a client that
    could choose either could rewrite the order of a conversation that is meant to be append-only.
    """

    model_config = ConfigDict(extra="forbid")

    author_type: AuthorType = "customer"
    author_name: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1)
    internal: bool = False


class TicketIn(BaseModel):
    """Open a ticket, with its opening thread.

    What is deliberately **not** on this payload:

    * `status` — a new ticket always starts `new`; it moves through the lifecycle from there.
    * `assignee_id` — assignment goes through `POST /support/tickets/{number}/assign`, which
      refuses an agent who is not part of the organization. Accepting it here would be the one
      way to assign a ticket to a ghost.
    * `created_at`, `updated_at`, `first_response_at`, `sla_due`, `sla_breached`, `satisfaction`
      — lifecycle state the desk stamps as it happens, never something a caller declares.

    `number` *is* accepted: it is the organization's own ticket code (`TK-4001`) and nothing here
    allocates one. A repeat is a 409, not a second ticket.
    """

    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=300)
    requester: RequesterIn
    priority: Priority = "normal"
    channel: Channel = "email"
    tags: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=list)
    messages: list[OpeningMessageIn] = Field(default_factory=list)


class AgentIn(BaseModel):
    """Create or replace one agent's record. The agent's public ref (`ag-01`) is the path segment,
    because it is the natural key the upsert is idempotent on.

    The performance figures are a stored projection the desk maintains, so they are writable here
    rather than derived — see `models.Agent`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=1, max_length=254)
    role: AgentRole = "agent"
    team: str = Field(default="Tier 1", min_length=1, max_length=80)
    open_load: int = Field(default=0, ge=0)
    avg_first_response_min: int = Field(default=0, ge=0)
    csat_pct: int = Field(default=0, ge=0, le=100)
    solved_7d: int = Field(default=0, ge=0)
    online: bool = False


class SlaTargetIn(BaseModel):
    """One priority's first-response and resolution windows, in minutes."""

    model_config = ConfigDict(extra="forbid")

    priority: Priority
    first_response_min: int = Field(ge=0)
    resolution_min: int = Field(ge=0)


class SlaPolicyIn(BaseModel):
    """Set the organization's one SLA policy.

    A **whole replacement**, not a patch: an organization has exactly one policy and `targets`
    replaces the stored list outright, so the field is required rather than defaulted — a payload
    that silently wiped the targets by omitting them is worse than one that refuses.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    business_hours: str = Field(default="", max_length=200)
    coverage: str = Field(default="", max_length=2000)
    targets: list[SlaTargetIn]


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

"""Request and response contracts for the support service.

This module is the published API surface — a change here is a contract change, and the product's
SPA generates its client from it. The response shapes mirror
`frontend/product-support/src/api/types.ts` field-for-field so wiring the live gateway is a flag
flip rather than a reshaping: `id` is the human code (`TK-4001`, `ag-01`), times are ISO-8601
UTC, and `assignee_id` is the agent's public ref.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

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
    """Append one message to a ticket's thread, written by the authenticated caller.

    `author_type` is **not** a field here: every reply through this endpoint is the desk
    replying, and the caller is the one holding `support.desk.ticket.write` — the server fixes
    the type to `agent` rather than trusting the client to declare it, so nobody can use this
    endpoint to post a message that reads as if the customer or the system said it.
    `author_name` stays client-supplied: the forwarded principal carries no display name
    (`platform_core.auth.Principal` has no name claim) and no `Agent` row is linked to a
    calling membership in this slice, so the server has nothing else to derive it from.
    """

    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)
    author_name: str = Field(min_length=1, max_length=160)
    internal: bool = False


class AssignIn(BaseModel):
    """Assign a ticket to an agent, or unassign it with `assignee_id: null`."""

    model_config = ConfigDict(extra="forbid")

    assignee_id: str | None = Field(default=None, max_length=40)


class StatusIn(BaseModel):
    """Move a ticket through the lifecycle. The target must be a legal next state
    (`app.domain.ALLOWED_TRANSITIONS`) — anything else is a 409, not a free-form set."""

    model_config = ConfigDict(extra="forbid")

    status: TicketStatusT


class RequesterIn(BaseModel):
    """The requester snapshot taken when a ticket opens."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    company: str = Field(max_length=200)
    kind: RequesterKind = "customer"


class TicketCreateIn(BaseModel):
    """Open a ticket with its first message.

    Server-owned, never accepted here: `number` (allocated per organization), `status`
    (always starts `new`), `created_at`, `first_response_at`, `sla_due`, `sla_breached` and
    `satisfaction` — all stored server truth, not form fields (`Must not be built`).
    """

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=300)
    requester: RequesterIn
    priority: Priority = "normal"
    channel: Channel = "web"
    assignee_id: str | None = Field(default=None, max_length=40)
    tags: list[str] = Field(default_factory=list)
    body: str = Field(min_length=1)


class TicketUpdateIn(BaseModel):
    """Edit a ticket's plain properties. Absent means "leave alone"; status and assignee stay
    on their own endpoints, each with its own audit action and event."""

    model_config = ConfigDict(extra="forbid")

    priority: Priority | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    tags: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one of priority, subject or tags")
        return self


class AgentIn(BaseModel):
    """Add an agent to the desk. `ref` (`ag-04`) is server-allocated, not accepted here."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    email: EmailStr = Field(max_length=254)
    role: AgentRole = "agent"
    team: str = Field(default="Tier 1", min_length=1, max_length=80)
    online: bool = False


class AgentPatch(BaseModel):
    """Edit an agent. Absent means "leave alone". The four metric columns
    (`open_load`, `avg_first_response_min`, `csat_pct`, `solved_7d`) are a desk projection and
    are never writable here."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: EmailStr | None = Field(default=None, max_length=254)
    role: AgentRole | None = None
    team: str | None = Field(default=None, min_length=1, max_length=80)
    online: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class SlaTargetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Priority
    first_response_min: int = Field(ge=1)
    resolution_min: int = Field(ge=1)


class SlaPolicyIn(BaseModel):
    """Replace the organization's one SLA policy. `targets` must carry exactly one row per
    priority — the policy is total, not a sparse override."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    business_hours: str = Field(default="", max_length=200)
    coverage: str = Field(default="")
    targets: list[SlaTargetIn]

    @model_validator(mode="after")
    def _one_row_per_priority(self) -> Self:
        priorities = [t.priority for t in self.targets]
        if len(priorities) != len(set(priorities)) or set(priorities) != set(get_args(Priority)):
            raise ValueError("targets must carry exactly one row for each priority")
        return self

"""Request and response contracts for approval.

This module is the OpenAPI surface: a change here is a change to the published contract, and the
frontend generates its client from it. Slice 1 exposes exactly one endpoint,
`GET /api/v1/approvals/chain/{employee_id}`, so this module is thin by design rather than by
omission.

**On the response shape.** ADR-0006 §5 and §11 pin the query (a recursive CTE, depth-capped at
32, cycle-aware) and the rule that every id travels with a name. Neither the ADR nor the data
model fixes a JSON envelope for this endpoint — that lives in
`docs/design/approval-engine-screens.md` §4.F, which this module follows for field names and for
what belongs at top level versus what belongs on each rung. Two shapes it specifies that are
worth calling out because nothing forces them: the **HR fallback is always the last entry**, with
its own `is_fallback` flag rather than being an omission the client has to infer from an empty
chain, and the **dotted-line manager is a separate field**, never a rung, so its "not used for
approvals" label can never be confused with a numbered step in the routing chain.

**What this slice's copy cannot render.** The screens spec shows a designation beside each
person's name; `hierarchy_nodes` carries no designation (ADR-0006 §2's amended column list is
`display_name`, `employee_no`, `location_code` and nothing else — designation was never added to
the event contract or the copy). That field is therefore absent from `ChainRungOut` rather than
guessed at from another table this service has no business reading (ADR-0000 §10). See the final
report for this slice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class ChainRungOut(BaseModel):
    """One entry in the rendered chain: a real rung, or the trailing HR-fallback marker."""

    #: 1-based, in walk order. The fallback entry is numbered too (screens §4.F's worked
    #: example numbers it "3"), so the client can render one `<ol>` with no special case.
    level: int
    #: `None` only on the fallback entry, which names no specific employee.
    employee_id: uuid.UUID | None
    display_name: str | None
    employee_no: str | None
    #: `True` when this rung's node was `exited` and the walk passed over it when choosing an
    #: approver (ADR-0006 §5). The rung is still shown — "the walk does not stop at an exited
    #: node, it walks past it" — with this flag so the client can render the muted "no longer
    #: with the company" note without re-deriving the skip rule itself.
    skipped: bool = False
    #: The synthetic trailing entry representing "whoever holds `decide:ORG` for an unassigned
    #: step" (ADR-0006 §4, §6). Always present, always last, so a chain with no manager at all
    #: still renders a well-formed list rather than an empty one (screens §4.F "Empty" state).
    is_fallback: bool = False


class DottedLineManagerOut(BaseModel):
    """Shown, never routed (ADR-0006 §2). Absent from the response when there is none."""

    employee_id: uuid.UUID
    display_name: str | None
    employee_no: str | None


class ApprovalChainOut(BaseModel):
    employee_id: uuid.UUID
    #: The subject's own location code, for rendering any instant in their zone (ADR-0006 §11,
    #: screens §2.1). `None` falls back to organization time, labelled, client-side.
    location_code: str | None
    chain: list[ChainRungOut]
    dotted_line_manager: DottedLineManagerOut | None = None
    #: `None` for an ordinary, resolvable chain. `"cycle"` or `"depth_exceeded"` when the walk
    #: had to stop defensively (ADR-0006 §5) — the chain still renders whatever it found, and the
    #: client shows the warning state screens §4.F specifies rather than treating this as a 5xx.
    unresolved: str | None = Field(default=None, pattern="^(cycle|depth_exceeded)$")


# --------------------------------------------------------------------- routing (slice 2)


class InboxItemOut(BaseModel):
    """One `GET /inbox` row (ADR-0006 §11, screens §4.A). `request_note` and the requester's
    `employee_no` both travel here — the inbox is where triage happens and where confusing two
    people has consequences (screens §2.2, §2.9)."""

    id: uuid.UUID
    request_type: str
    source_ref: uuid.UUID
    #: Structured facts only (`date`, `from`, `to` for regularisation) — the client builds the
    #: summary line from this, never from `request_note` (ADR-0006 §3).
    summary: dict[str, Any]
    request_note: str | None
    opened_at: datetime
    requester_employee_id: uuid.UUID
    requester_name: str | None
    requester_employee_no: str | None
    requester_location_code: str | None
    #: `assigned|delegated_to_me|escalated_to_me|delegated_away|escalated_away|unassigned`
    #: (screens §2.3). The client maps this to a chip and derives nothing itself.
    inbox_reason: str
    #: Present exactly when `inbox_reason` names somebody (screens §2.3). `None` renders as a
    #: reason-only chip, never as "undefined".
    from_name: str | None


class RequestListItemOut(BaseModel):
    """One `GET /requests` row (ADR-0006 §11, screens §4.D). No `request_note` — deliberately
    (screens §2.9: the requester wrote it, nothing renders it back to them)."""

    id: uuid.UUID
    request_type: str
    source_ref: uuid.UUID
    summary: dict[str, Any]
    status: str
    opened_at: datetime
    requester_employee_id: uuid.UUID
    requester_name: str | None
    requester_employee_no: str | None
    requester_location_code: str | None
    #: The current open step's approver — `None` once decided, or while unassigned ("Waiting to
    #: be routed", screens §4.D).
    approver_employee_id: uuid.UUID | None
    approver_name: str | None
    decided_by_employee_id: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    comment: str | None
    #: `applied|failed|None` — travels here as well as on the detail (C8): "the person who asked
    #: is the one most worth telling" (ADR-0006 §11 amended).
    application_state: str | None
    application_failure_reason: str | None


class StepOut(BaseModel):
    """One row of a request's history (ADR-0006 §3, §11) — append-only, so this is the whole
    story of how the request got to where it is now."""

    level: int
    reason: str
    outcome: str
    approver_employee_id: uuid.UUID | None
    approver_name: str | None
    acting_for_employee_id: uuid.UUID | None
    acting_for_name: str | None
    opened_at: datetime
    closed_at: datetime | None
    #: Always `None` in slices 2-3 (ADR-0006 §10 amended) — no SLA clock exists yet.
    due_at: datetime | None


class RequestDetailOut(BaseModel):
    """`GET /requests/{id}` and the response of `POST .../decision` (ADR-0006 §11). The OWN-scope
    read widens to anyone who ever appeared on a step, any outcome — a past decider keeps the
    same shape a currently-eligible caller gets, minus the ability to act."""

    id: uuid.UUID
    request_type: str
    source_ref: uuid.UUID
    summary: dict[str, Any]
    #: Present only for a principal §3's rule 3 allows to see it — the requester, anyone who
    #: appears on a step, or a `read:ORG` holder. Every caller who can reach this endpoint at all
    #: qualifies, by construction of the visibility check in `app/api/routes.py`.
    request_note: str | None
    status: str
    opened_at: datetime
    requester_employee_id: uuid.UUID
    requester_name: str | None
    requester_employee_no: str | None
    requester_location_code: str | None
    #: While pending: the current open step's approver/represented pair. Once decided: the
    #: request's own decision-time `acting_for_employee_id` (ADR-0006 §7) — the same *concept*,
    #: "who this is being handled on behalf of", read from whichever column currently holds it.
    approver_employee_id: uuid.UUID | None
    approver_name: str | None
    acting_for_employee_id: uuid.UUID | None
    acting_for_name: str | None
    #: The §2.3 chip, present only while pending (screens §4.B's "Waiting on" card).
    inbox_reason: str | None
    from_name: str | None
    decided_by_employee_id: uuid.UUID | None
    decided_by_name: str | None
    decided_at: datetime | None
    comment: str | None
    application_state: str | None
    application_failure_reason: str | None
    applied_at: datetime | None
    steps: list[StepOut]


class DecisionIn(BaseModel):
    """`POST /requests/{id}/decision` (ADR-0006 §7). `comment` is optional for **both**
    outcomes — a server rule, not a UI choice (§7 amended): a mandatory field the server does not
    enforce drifts the moment a second client exists."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2000)

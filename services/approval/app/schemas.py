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

from pydantic import BaseModel, Field


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

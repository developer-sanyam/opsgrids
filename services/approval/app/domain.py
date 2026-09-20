"""Pure functions over the chain walk. No I/O — the walk itself is a query (`app/selectors.py`).

ADR-0006 §5 is explicit about the split: the recursive CTE returns at most 33 rows (the 32-rung
depth cap plus the requester's own starting row), and *choosing* an approver from those rows is a
property of picking, not of traversal — "a small pure function in `app/domain.py` ...that stays
pure and stays trivially testable over a list of at most 33 items, which is what `domain.py` is
for; what it is not is a substitute for a query." `pick_approver` is that function. It is not
exercised by an endpoint in this slice (nothing opens a request yet), but the chain endpoint
(`GET /chain/{employee_id}`) needs the identical skip rule to mark which rungs it walked past, so
it is written once, here, rather than re-implemented at each call site.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

#: The number of rungs *above the requester* the walk (and employee's own cycle check, data
#: model §4.3) will accept — a 32-rung proposed chain is accepted, a 33-rung one refused, on both
#: sides (ADR-0006 §5). The requester's own row is not a rung, so `app/selectors.py`'s CTE caps
#: total rows at `MAX_CHAIN_DEPTH + 1`, not at `MAX_CHAIN_DEPTH`.
MAX_CHAIN_DEPTH = 32


@dataclass(frozen=True, slots=True)
class Rung:
    """One row the chain-walk CTE returned — a candidate approver at some depth."""

    employee_id: uuid.UUID
    reporting_manager_id: uuid.UUID | None
    dotted_line_manager_id: uuid.UUID | None
    status: str
    display_name: str
    employee_no: str
    location_code: str | None
    depth: int


def pick_approver(rungs: list[Rung], requester_id: uuid.UUID) -> Rung | None:
    """The first rung that is neither `exited` nor the requester.

    `rungs` is the CTE's output in depth order, starting at the requester's own row (depth 1)
    and walking upward through `reporting_manager_id`. The chain walk does not stop at an exited
    node — skipping is a property of *choosing*, not of traversal, so the query returns every
    rung and this function is what skips: `requester → exited manager → director` must reach the
    director. Returns `None` when nobody usable is found — an empty chain, a chain that is all
    exited managers, or a chain that never got past the requester's own row (no manager at all).

    A one-id specialisation of `pick_next_approver` (ADR-0006 §8 added the second caller with
    more than one id to skip — see that function) — left as its own name and signature rather
    than folded away, since every routing call site already reads `pick_approver(rungs,
    requester_id)` and changing that is churn this slice has no reason to cause.
    """
    return pick_next_approver(rungs, skip_ids=(requester_id,))


def pick_next_approver(rungs: list[Rung], skip_ids: Collection[uuid.UUID]) -> Rung | None:
    """The first rung that is neither `exited` nor named in `skip_ids`.

    ADR-0006 §8's escalation walk is the second caller: it starts from the step's own original
    approver (`walk_chain(start=original_approver)`, so `rungs[0]` is that approver's own row,
    not a rung above them) and must skip **two** ids — the walk's own starting point, so a step
    never escalates to the person who was just asked, and the requester, "skipping the requester"
    being §5's rule restated for a walk that does not start at the requester this time. A single
    `skip_ids` collection covers both without a second parameter that `pick_approver`'s one-id
    callers would have to pass `None` or an empty set for.
    """
    skip = set(skip_ids)
    for rung in rungs:
        if rung.employee_id in skip:
            continue
        if rung.status == "exited":
            continue
        return rung
    return None


def skip_flags(rungs: list[Rung], requester_id: uuid.UUID) -> list[tuple[Rung, bool]]:
    """Every rung after the requester's own row, paired with whether it was skipped.

    Used to render `GET /chain` (ADR-0006 §5: "marks skipped exited rungs so the shape of a
    chain is explainable rather than mysterious"), and shares `pick_approver`'s exact rule so the
    two never drift: a rung is skipped here if and only if `pick_approver` would have passed over
    it. The requester's own starting row (depth 1) is never part of the displayed chain — the
    chain shows who approves *for* them, not themselves.

    `pick_approver` stops at the **first** rung that is neither exited nor the requester — it
    never looks past that point. So only an exited rung *before* that pick is one it actually
    walked past; an exited rung beyond it (a director who has since exited, above the manager who
    was picked) was never evaluated at all, and marking it skipped would claim the walk passed
    over someone it never reached.
    """
    picked = False
    out: list[tuple[Rung, bool]] = []
    for rung in rungs:
        if rung.employee_id == requester_id:
            continue
        if picked:
            out.append((rung, False))
            continue
        is_exited = rung.status == "exited"
        out.append((rung, is_exited))
        if not is_exited:
            picked = True
    return out


# --------------------------------------------------------------------- routing (slice 2)


#: `approval_policies`' own row defaults (ADR-0006 §10): a missing row means these, not an
#: unconfigured state. Mirrors the model's own `default=` values (`app/models.py`'s
#: `ApprovalPolicy`) so the two never drift on what "no row yet" means.
DEFAULT_SLA_MINUTES = 1440
DEFAULT_POLICY_ENABLED = True


def compute_due_at(*, opened_at: datetime, sla_minutes: int) -> datetime:
    """`due_at = opened_at + sla_minutes` — ADR-0006 §4 rule 4 at routing and delegation
    re-pointing's own carry-through, and §8's "due_at = now + sla_minutes" at escalation, where
    the caller passes its own `now()` as `opened_at` (the escalated step's opening instant).
    Calendar time only, no calendar awareness — weekends and holidays count (ADR-0006
    Consequences: "when a calendar exists, only the due_at computation changes").
    """
    return opened_at + timedelta(minutes=sla_minutes)


def delegation_is_active(
    *, revoked_at: datetime | None, starts_at: datetime, ends_at: datetime, at: datetime
) -> bool:
    """A delegation is active at instant `t` when `revoked_at IS NULL AND starts_at <= t <
    ends_at` (ADR-0006 §5) — the half-open window `ex_delegations_no_overlap` also uses, so a
    delegation that has just ended cannot overlap the one starting in its place. Shared by
    `app/services.py` (applying a delegation when a step opens) and the eligibility check
    (deciding whether a delegate may still act), so the two never drift on what "active" means.
    """
    return revoked_at is None and starts_at <= at < ends_at


#: `inbox_reason` values (screens §2.3). `escalated_to_me`/`escalated_away` need a due date to
#: exist at all, so slices 2-3 (no SLA clock, ADR-0006 §10 amended) could declare the vocabulary
#: but never produce it — slice 4 arms the sweep (`app/selectors.py`'s `_INBOX_CANDIDATES_CTE`)
#: and supplies both.
INBOX_REASON_ASSIGNED = "assigned"
INBOX_REASON_DELEGATED_TO_ME = "delegated_to_me"
INBOX_REASON_ESCALATED_TO_ME = "escalated_to_me"
INBOX_REASON_DELEGATED_AWAY = "delegated_away"
INBOX_REASON_ESCALATED_AWAY = "escalated_away"
INBOX_REASON_UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class InboxReason:
    reason: str
    from_employee_id: uuid.UUID | None


def inbox_reason_for(
    *,
    caller_id: uuid.UUID,
    approver_employee_id: uuid.UUID | None,
    acting_for_employee_id: uuid.UUID | None,
    step_reason: str,
    predecessor_approver_employee_id: uuid.UUID | None = None,
) -> InboxReason:
    """Why this request is in the caller's inbox (ADR-0006 §6, screens §2.3).

    For every reason but `escalated_to_me`, the request's *current open step* is sufficient —
    `approver_employee_id`, `acting_for_employee_id` and `step_reason` are that step's own three
    fields. `escalated_to_me` is the one exception: "the step moved up to you because {from_name}
    didn't answer in time" (screens §2.3) names the *previous* rung's approver, a fact the open
    step itself does not carry (it only knows who holds it now, not who held it before) — hence
    the separate `predecessor_approver_employee_id` parameter, which `app/selectors.py`'s
    `_INBOX_CANDIDATES_CTE` resolves with one extra self-join on `level - 1`, and
    `app/api/routes.py`'s request-detail assembly resolves for free out of the steps it has
    already loaded. `escalated_away`'s own "from" is not a predecessor at all — it is the
    *current* open step's approver, already carried in `approver_employee_id` by the caller's own
    construction of this function's other arguments (see `_INBOX_CANDIDATES_CTE`'s escalated-away
    branches).

    An unassigned step, and an assigned step whose approver has since exited (the ORG "abandoned"
    widening, ADR-0006 §6 route c), both render as `unassigned` — the screens specification
    defines no separate chip for the second case, and both describe the same fact to an ORG
    decider: nobody real is going to answer this without intervention.
    """
    if approver_employee_id is None:
        return InboxReason(INBOX_REASON_UNASSIGNED, None)
    if approver_employee_id == caller_id:
        if acting_for_employee_id is not None:
            return InboxReason(INBOX_REASON_DELEGATED_TO_ME, acting_for_employee_id)
        if step_reason == "escalated":
            return InboxReason(INBOX_REASON_ESCALATED_TO_ME, predecessor_approver_employee_id)
        return InboxReason(INBOX_REASON_ASSIGNED, None)
    if acting_for_employee_id == caller_id:
        if step_reason == "escalated":
            return InboxReason(INBOX_REASON_ESCALATED_AWAY, approver_employee_id)
        return InboxReason(INBOX_REASON_DELEGATED_AWAY, approver_employee_id)
    # Route (c), abandoned: the caller is neither the approver nor the represented original, so
    # this row can only have matched through the ORG "unassigned or abandoned" branch.
    return InboxReason(INBOX_REASON_UNASSIGNED, None)


# --------------------------------------------------------------------- delegation status (slice 3)

#: `GET /delegations` row tags and the three list sections screens §4.E specifies (Active,
#: Scheduled, Past — "Past" being `ended` and `revoked` together on the client side). The single
#: source for this vocabulary — `app/schemas.py`'s `DelegationOut.status` is typed with this
#: same alias rather than repeating the four literals, which is what lets
#: `app/api/routes.py._delegation_out` pass `delegation_status(...)`'s return straight into the
#: schema with no `cast`: previously the vocabulary was written three times (these constants,
#: the schema's own `Literal`, and the cast that reconciled the two), which is exactly the
#: shape that drifts the moment a fifth status is ever added to only one of the three.
DelegationStatus = Literal["active", "scheduled", "ended", "revoked"]

DELEGATION_STATUS_ACTIVE: Literal["active"] = "active"
DELEGATION_STATUS_SCHEDULED: Literal["scheduled"] = "scheduled"
DELEGATION_STATUS_ENDED: Literal["ended"] = "ended"
DELEGATION_STATUS_REVOKED: Literal["revoked"] = "revoked"


def delegation_status(
    *, revoked_at: datetime | None, starts_at: datetime, ends_at: datetime, at: datetime
) -> DelegationStatus:
    """One of `active|scheduled|ended|revoked`, computed from the same three instants
    `delegation_is_active` compares — so the row tag and the eligibility check can never
    disagree about what a delegation currently grants.

    `revoked` takes priority over the time-based reading even when `at` still falls inside
    `[starts_at, ends_at)`: revocation changes eligibility **immediately** (ADR-0006 §5 — "the
    delegate loses the power to decide immediately"), so the tag must say so rather than keep
    reading "Active" until the original window would have ended on its own. The boundary at
    `ends_at` is the same half-open one `delegation_is_active` and `ex_delegations_no_overlap`
    both use: a delegation is no longer active at the exact instant it ends.
    """
    if revoked_at is not None:
        return DELEGATION_STATUS_REVOKED
    if at < starts_at:
        return DELEGATION_STATUS_SCHEDULED
    if at >= ends_at:
        return DELEGATION_STATUS_ENDED
    return DELEGATION_STATUS_ACTIVE

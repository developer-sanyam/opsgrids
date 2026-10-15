"""Customer HTTP surface for approval.

Fronted by `app-gateway` at `/api/v1/approvals`, with **no `product` key** in the route table
(ADR-0006 §1, §14): approval is a platform surface every customer has, the same shape as
`/api/v1/members`, and must work for a customer who has bought nothing yet.

Slice 1 shipped `GET /chain/{employee_id}`. Slice 2 added the routing engine's read and decide
surface: `GET /inbox`, `GET /requests`, `GET /requests/{id}` and `POST /requests/{id}/decision`
(ADR-0006 §11). Slice 3 adds the delegation surface: `GET /delegate-candidates`,
`GET /delegations`, `POST /delegations` and `POST /delegations/{id}/revoke`. Slice 4 adds policy
administration — `GET /policies` (any scope that can read at all) and
`PUT /policies/{request_type}` (`policy.write`, floored at ORG) — and arms the SLA sweep, which
is what makes `GET /inbox`'s `escalated_to_me`/`escalated_away` reasons producible for the first
time (§10, §11).

**Holding the permission is never enough** (ADR-0006 §11's own words). `require(...)` below is
the first gate on every route; the second — ADR-0006 §6's eligibility check, and the OWN-scope
object-level rule on `read` — runs in `app/services.py` and this module, under the request's own
lock where a decision is at stake, and is never replaced by a UI gate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query
from pydantic import StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors, services
from app.config import settings
from app.models import SCHEMA, Delegation
from app.schemas import (
    ApprovalChainOut,
    ChainRungOut,
    DecisionIn,
    DelegateCandidateOut,
    DelegationCreateIn,
    DelegationCreateOut,
    DelegationOut,
    DottedLineManagerOut,
    InboxItemOut,
    PolicyOut,
    PolicyUpdateIn,
    RequestDetailOut,
    RequestListItemOut,
    ServiceInfo,
    StepOut,
)
from app.selectors import PersonInfo
from platform_core.auth import Scope, ScopeContext, require
from platform_core.db import get_tenant_session
from platform_core.errors import ForbiddenProblem, NotFoundProblem
from platform_core.logging import get_logger
from platform_core.pagination import Page, PageQuery, paginate

log = get_logger("approval.routes")

router = APIRouter(tags=["approval"])

#: Screens §4.E: "the cap is the server's" — a client that fixes its own `Query(le=...)` still
#: gets exactly this many, so the "showing the first 20" footer copy is never wrong.
DELEGATE_CANDIDATES_LIMIT = 20

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
ReadCtx = Annotated[ScopeContext, Depends(require("core.approval.request.read"))]
#: `decide` at **any** scope: `MEMBER` holds `decide:OWN` by default, and §6's eligibility check
#: — not the permission scope — is what actually decides who may act on any given request
#: (ADR-0006 §11: "holding decide is not enough").
DecideCtx = Annotated[ScopeContext, Depends(require("core.approval.request.decide"))]
#: `delegation.write` at **any** scope, for the same reason: below ORG it is read as OWN
#: (`app/catalogue.py`'s collapse), never refused outright, and the object-level rule that
#: actually restricts a caller lives in `app/services.create_delegation` /
#: `revoke_delegation`.
DelegationCtx = Annotated[ScopeContext, Depends(require("core.approval.delegation.write"))]
#: `policy.write`, floored at ORG (ADR-0006 §11's table: "Guarded `minimum=Scope.ORG`") — the
#: permission's own `max_scope` is already ORG (`app/catalogue.py`), so `minimum` here is what
#: refuses a hypothetical narrower grant rather than collapsing it the way `decide`/
#: `delegation.write` do; there is no "own" reading of organization-wide SLA configuration to
#: collapse to.
PolicyWriteCtx = Annotated[
    ScopeContext, Depends(require("core.approval.policy.write", minimum=Scope.ORG))
]
#: `ApprovalPolicy.request_type`'s own shape (`app/models.py`'s `request_type_shape` CHECK),
#: repeated here so a malformed path segment is a 422 at the boundary — the client's own mistake
#: named as one — rather than reaching `app/services.update_policy` at all, let alone the
#: database CHECK's generic 409.
RequestTypePath = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
]


def _person_field(
    people: dict[uuid.UUID, PersonInfo], employee_id: uuid.UUID | None, field: str
) -> str | None:
    """A field that cannot be resolved is `None` rather than absent (ADR-0006 §11) — the client
    renders its own fallback and the row stays operable. One implementation behind the three
    named wrappers below, rather than the same `None`-handling body copied three times."""
    if employee_id is None:
        return None
    person = people.get(employee_id)
    return cast("str | None", getattr(person, field)) if person else None


def _name(people: dict[uuid.UUID, PersonInfo], employee_id: uuid.UUID | None) -> str | None:
    return _person_field(people, employee_id, "display_name")


def _employee_no(people: dict[uuid.UUID, PersonInfo], employee_id: uuid.UUID | None) -> str | None:
    return _person_field(people, employee_id, "employee_no")


def _location(people: dict[uuid.UUID, PersonInfo], employee_id: uuid.UUID | None) -> str | None:
    return _person_field(people, employee_id, "location_code")


@router.get("/approvals/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/approvals/chain/{employee_id}",
    response_model=ApprovalChainOut,
    summary="The resolved approval chain for one employee",
)
async def get_chain(
    employee_id: uuid.UUID,
    session: SessionDep,
    ctx: ReadCtx,
) -> ApprovalChainOut:
    """`core.approval.request.read` — OWN reaches only the caller's own chain; TEAM reaches a
    direct report's; LOCATION reaches an employee at one of the caller's locations; ORG reaches
    any.

    Object-level check runs before either query: `assert_can_touch` is the same chokepoint every
    other detail endpoint in the platform uses, and a caller outside scope is refused with 403
    before the chain walk ever runs (ADR-0003 §3's negative-authz discipline). TEAM and LOCATION
    compare the *subject's* manager and location against the caller — never the caller's own —
    so the subject's own hierarchy row is fetched first, ahead of the check, one indexed lookup
    the walk needs anyway for the response's `location_code`. A subject the copy has not seen
    yet fails TEAM and LOCATION closed (there is nothing to compare against); OWN and ORG are
    unaffected, because neither reads the copy to decide.
    """
    subject = await selectors.get_hierarchy_node(session, ctx.organization_id, employee_id)
    ctx.assert_can_touch(
        owner_id=employee_id,
        team_owner_id=subject.reporting_manager_id if subject else None,
        location_code=subject.location_code if subject else None,
    )

    walk = await selectors.walk_chain(session, ctx.organization_id, employee_id)

    rungs: list[ChainRungOut] = []
    for level, (rung, skipped) in enumerate(domain.skip_flags(walk.rungs, employee_id), start=1):
        rungs.append(
            ChainRungOut(
                level=level,
                employee_id=rung.employee_id,
                display_name=rung.display_name,
                employee_no=rung.employee_no,
                skipped=skipped,
            )
        )
    # The HR/unassigned fallback is always the trailing entry (screens §4.F): a chain with no
    # manager at all still renders a well-formed, non-empty ordered list, and the client never
    # has to infer "nobody left" from an empty array.
    rungs.append(
        ChainRungOut(
            level=len(rungs) + 1,
            employee_id=None,
            display_name=None,
            employee_no=None,
            is_fallback=True,
        )
    )

    dotted_line_manager: DottedLineManagerOut | None = None
    if subject is not None and subject.dotted_line_manager_id is not None:
        node = await selectors.get_hierarchy_node(
            session, ctx.organization_id, subject.dotted_line_manager_id
        )
        dotted_line_manager = DottedLineManagerOut(
            employee_id=subject.dotted_line_manager_id,
            display_name=node.display_name if node else None,
            employee_no=node.employee_no if node else None,
        )

    unresolved: str | None = None
    if walk.cycle_detected:
        unresolved = "cycle"
    elif walk.depth_exceeded:
        unresolved = "depth_exceeded"

    return ApprovalChainOut(
        employee_id=employee_id,
        location_code=subject.location_code if subject else None,
        chain=rungs,
        dotted_line_manager=dotted_line_manager,
        unresolved=unresolved,
    )


# --------------------------------------------------------------------- routing (slice 2)


@router.get(
    "/approvals/inbox",
    response_model=Page[InboxItemOut],
    summary="Pending requests the caller may decide",
)
async def get_inbox(
    session: SessionDep,
    ctx: DecideCtx,
    params: PageQuery,
) -> Page[InboxItemOut]:
    """`GET /inbox` (ADR-0006 §11): pending requests where ADR-0006 §6 makes the caller eligible
    — unassigned rows only for a `decide:ORG` holder — excluding the caller's own requests.

    A caller with no `subject_ref` is eligible for nothing (§6 rule 2: "a membership with no
    employee link can decide nothing, including unassigned steps") — an empty page, not a 403,
    the same "let the server's own filtering be the gate" pattern screens §2.4 specifies for this
    endpoint.
    """
    if ctx.subject_ref is None:
        # ADR-0008 §9.3, the third of that section's three surfaces for the unlinked state and
        # the only one that lives here. `info`, not a warning: an unwritten
        # `account.memberships.employee_id` is a modelled, correctable provisioning state, not a
        # fault in this request — and §9's first bullet is why the *response* stays an empty page
        # at 200 (an exited employee reaches the same "you may decide nothing" state holding a
        # good `sref`, so an error here would split one state across two status codes). The
        # surfacing therefore happens beside the response, never in it.
        #
        # This is the only site: the other null-`subject_ref` paths in this service are already
        # loud (`POST /delegations`' 403 below, `/chain`'s `assert_can_touch`) or are a different
        # condition (`selectors.requests_stmt`'s `mine_only`, which is a client-supplied filter
        # rather than "this membership has no identity"). Routing facts only — the organization
        # and the membership, as §9 specifies — and nothing from the request or the caller's
        # person: the same read/write split the outbox role's column grant is built on.
        membership_id = ctx.principal.membership_id
        log.info(
            "approval.inbox.no_subject_ref",
            organization_id=str(ctx.organization_id),
            membership_id=str(membership_id) if membership_id else None,
        )
        return Page.build([], total=0, params=params)

    caller_id = ctx.subject_ref
    caller_node = await selectors.get_hierarchy_node(session, ctx.organization_id, caller_id)
    caller_active = caller_node is None or caller_node.status != "exited"
    is_org = ctx.scope is Scope.ORG
    # `services.db_now` is the one clock (ADR-0006 §8): reading `now()` again here, separately,
    # would be a second one, and the two could disagree on clock skew or across a slow query.
    db_now = await services.db_now(session)

    rows, total = await selectors.list_inbox(
        session,
        organization_id=ctx.organization_id,
        caller_id=caller_id,
        caller_active=caller_active,
        is_org_decider=is_org,
        now=db_now,
        limit=params.size,
        offset=params.offset,
    )

    reasons = [
        domain.inbox_reason_for(
            caller_id=caller_id,
            approver_employee_id=row.approver_employee_id,
            acting_for_employee_id=row.acting_for_employee_id,
            step_reason=row.reason,
            predecessor_approver_employee_id=row.predecessor_approver_employee_id,
        )
        for row in rows
    ]
    person_ids = {row.requester_employee_id for row in rows} | {
        reason.from_employee_id for reason in reasons if reason.from_employee_id is not None
    }
    people = await selectors.get_hierarchy_people(session, ctx.organization_id, person_ids)

    items = [
        InboxItemOut(
            id=row.request_id,
            request_type=row.request_type,
            source_ref=row.source_ref,
            summary=row.summary,
            request_note=row.request_note,
            opened_at=row.opened_at,
            requester_employee_id=row.requester_employee_id,
            requester_name=_name(people, row.requester_employee_id),
            requester_employee_no=_employee_no(people, row.requester_employee_id),
            requester_location_code=_location(people, row.requester_employee_id),
            inbox_reason=reason.reason,
            from_name=_name(people, reason.from_employee_id),
            due_at=row.due_at,
        )
        for row, reason in zip(rows, reasons, strict=True)
    ]
    return Page.build(items, total=total, params=params)


@router.get(
    "/approvals/requests",
    response_model=Page[RequestListItemOut],
    summary="Requests I raised (OWN) or every request (ORG)",
)
async def list_requests(
    session: SessionDep,
    ctx: ReadCtx,
    params: PageQuery,
    requester: Annotated[Literal["me"] | None, Query()] = None,
) -> Page[RequestListItemOut]:
    """`GET /requests` (ADR-0006 §11): OWN = `requester_employee_id = sref`; TEAM = raised by a
    direct report; LOCATION = raised by anyone at one of the caller's locations; ORG = every
    request — all via `ctx.apply()` in `selectors.requests_stmt`. No `request_note` (screens
    §2.9 — nothing renders the requester's own text back to them); `application_state` travels
    here, deliberately (C8).

    `?requester=me` is the "My requests" screen's own filter, layered on top of scope rather than
    a replacement for it: scope answers "may this caller see this row", not "is this caller's
    own" — a TEAM, LOCATION or ORG reader legitimately sees other people's requests through this
    same endpoint (the HR/ORG "all requests" view screens also describe), so without this filter
    an ORG-scoped caller asking for "my requests" would be shown the entire organization's. The
    server resolves "me" from `ctx.subject_ref`, never a client-supplied id — `Literal["me"]` is
    the whole of the accepted vocabulary, so there is no `requester=<uuid>` shape to accept or
    reject in application code at all; anything else fails FastAPI's own query validation before
    this function runs (422), which is the boundary validation CLAUDE.md asks for rather than a
    second, hand-rolled check here."""
    stmt = selectors.requests_stmt(ctx, mine_only=requester == "me")
    rows, total = await paginate(session, stmt, params)

    open_steps = await selectors.get_open_steps_for_requests(
        session, ctx.organization_id, [row.id for row in rows]
    )
    person_ids: set[uuid.UUID] = set()
    for row in rows:
        person_ids.add(row.requester_employee_id)
        if row.decided_by_employee_id is not None:
            person_ids.add(row.decided_by_employee_id)
        step = open_steps.get(row.id)
        if step is not None and step.approver_employee_id is not None:
            person_ids.add(step.approver_employee_id)
    people = await selectors.get_hierarchy_people(session, ctx.organization_id, person_ids)

    items = []
    for row in rows:
        step = open_steps.get(row.id)
        approver_id = step.approver_employee_id if step is not None else None
        items.append(
            RequestListItemOut(
                id=row.id,
                request_type=row.request_type,
                source_ref=row.source_ref,
                summary=row.summary,
                status=row.status,
                opened_at=row.opened_at,
                requester_employee_id=row.requester_employee_id,
                requester_name=_name(people, row.requester_employee_id),
                requester_employee_no=_employee_no(people, row.requester_employee_id),
                requester_location_code=_location(people, row.requester_employee_id),
                approver_employee_id=approver_id,
                approver_name=_name(people, approver_id),
                decided_by_employee_id=row.decided_by_employee_id,
                decided_by_name=_name(people, row.decided_by_employee_id),
                decided_at=row.decided_at,
                comment=row.comment,
                application_state=row.application_state,
                application_failure_reason=row.application_failure_reason,
            )
        )
    return Page.build(items, total=total, params=params)


async def _build_detail(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    viewer_subject_ref: uuid.UUID | None,
    viewer_is_org: bool,
) -> RequestDetailOut:
    """The shape both `GET /requests/{id}` and `POST .../decision` return — built once so the two
    can never drift on field names. Takes the viewer as plain values rather than a `ScopeContext`
    because the two callers hold contexts built for *different* permissions (`read` and
    `decide`), and a `ScopeContext`'s `.scope` only means "the scope this request satisfied one
    particular permission at" — reusing one across the wrong permission would be a type-shaped
    bug, so the callers pass only what this function actually needs.

    Assumes the caller has already established visibility (the GET route's own check below; the
    POST route's decider is trivially on a step of the request they just decided).
    """
    request = await selectors.get_request(session, organization_id, request_id)
    if request is None:
        raise NotFoundProblem("That request isn't available to you")

    steps = await selectors.list_steps(session, organization_id, request_id)
    open_step = next((s for s in steps if s.outcome == "open"), None)

    person_ids: set[uuid.UUID] = {request.requester_employee_id}
    if request.decided_by_employee_id is not None:
        person_ids.add(request.decided_by_employee_id)
    for step in steps:
        if step.approver_employee_id is not None:
            person_ids.add(step.approver_employee_id)
        if step.acting_for_employee_id is not None:
            person_ids.add(step.acting_for_employee_id)
    people = await selectors.get_hierarchy_people(session, organization_id, person_ids)

    # "Waiting on" (screens §4.B) — the same §2.3 chip the inbox shows, computed relative to
    # *this* viewer. Only meaningful for a viewer who is themselves a personal party to the open
    # step (an eligible decider); a requester or an uninvolved ORG reader sees no chip rather
    # than a fabricated one, because `domain.inbox_reason_for` is only correct for a caller the
    # inbox's own query would have matched. This does **not** widen to `escalated_away` the way
    # `GET /inbox` now does (`app/selectors.py`'s `_INBOX_CANDIDATES_CTE`) — that reads a step
    # this viewer no longer holds, which `steps` (already loaded, full history) could answer, but
    # doing so is left for whichever slice next touches this endpoint rather than folded in here
    # silently; the object-level rule above already lets such a viewer read and decide the
    # request, this chip is cosmetic only.
    inbox_reason: str | None = None
    from_employee_id: uuid.UUID | None = None
    approver_employee_id: uuid.UUID | None = None
    acting_for_employee_id: uuid.UUID | None = None
    if open_step is not None:
        approver_employee_id = open_step.approver_employee_id
        acting_for_employee_id = open_step.acting_for_employee_id
        caller = viewer_subject_ref
        if caller is not None and caller in (
            open_step.approver_employee_id,
            open_step.acting_for_employee_id,
        ):
            # `escalated_to_me`'s "from" is the *previous* rung, off the open step itself
            # (`domain.inbox_reason_for`'s own docstring) — free here since `steps` already holds
            # the whole history, unlike the inbox's own self-join. `outcome == "escalated"` matches
            # `app/selectors.py`'s `_INBOX_CANDIDATES_CTE` join exactly, and for the same reason:
            # delegation re-pointing inserts its successor at the *same* level as the step it
            # replaces (ADR-0006 §5 — re-pointing is not a rung), so a step that was re-pointed and
            # then escalated has a closed, `delegated`-outcome step sitting at `level - 1` too.
            # Without the outcome filter, `next(...)` would return whichever of the two happens to
            # come first in `steps` (opened order) — silently naming the re-pointed-away approver
            # instead of the one who actually let the SLA lapse.
            predecessor = next(
                (s for s in steps if s.level == open_step.level - 1 and s.outcome == "escalated"),
                None,
            )
            derived = domain.inbox_reason_for(
                caller_id=caller,
                approver_employee_id=open_step.approver_employee_id,
                acting_for_employee_id=open_step.acting_for_employee_id,
                step_reason=open_step.reason,
                predecessor_approver_employee_id=(
                    predecessor.approver_employee_id if predecessor is not None else None
                ),
            )
            inbox_reason = derived.reason
            from_employee_id = derived.from_employee_id
        elif viewer_is_org:
            approver_person = (
                people.get(open_step.approver_employee_id)
                if open_step.approver_employee_id is not None
                else None
            )
            if open_step.approver_employee_id is None or (
                approver_person is not None and approver_person.status == "exited"
            ):
                inbox_reason = domain.INBOX_REASON_UNASSIGNED
    else:
        # Decided: the same concept read from the request's own decision-time column, not the
        # (nonexistent) open step (ADR-0006 §7).
        acting_for_employee_id = request.acting_for_employee_id

    steps_out = [
        StepOut(
            level=step.level,
            reason=step.reason,
            outcome=step.outcome,
            approver_employee_id=step.approver_employee_id,
            approver_name=_name(people, step.approver_employee_id),
            acting_for_employee_id=step.acting_for_employee_id,
            acting_for_name=_name(people, step.acting_for_employee_id),
            opened_at=step.opened_at,
            closed_at=step.closed_at,
            due_at=step.due_at,
        )
        for step in steps
    ]

    # `request_note` is returned to every caller who reaches this point — the visibility check
    # above already restricted access to the requester, ORG, or a step participant, which is
    # exactly ADR-0006 §3's rule 3 for who may read it.
    return RequestDetailOut(
        id=request.id,
        request_type=request.request_type,
        source_ref=request.source_ref,
        summary=request.summary,
        request_note=request.request_note,
        status=request.status,
        opened_at=request.opened_at,
        requester_employee_id=request.requester_employee_id,
        requester_name=_name(people, request.requester_employee_id),
        requester_employee_no=_employee_no(people, request.requester_employee_id),
        requester_location_code=_location(people, request.requester_employee_id),
        approver_employee_id=approver_employee_id,
        approver_name=_name(people, approver_employee_id),
        acting_for_employee_id=acting_for_employee_id,
        acting_for_name=_name(people, acting_for_employee_id),
        inbox_reason=inbox_reason,
        from_name=_name(people, from_employee_id),
        decided_by_employee_id=request.decided_by_employee_id,
        decided_by_name=_name(people, request.decided_by_employee_id),
        decided_at=request.decided_at,
        comment=request.comment,
        application_state=request.application_state,
        application_failure_reason=request.application_failure_reason,
        applied_at=request.applied_at,
        steps=steps_out,
    )


@router.get(
    "/approvals/requests/{request_id}",
    response_model=RequestDetailOut,
    summary="Request detail",
)
async def get_request_detail(
    request_id: uuid.UUID, session: SessionDep, ctx: ReadCtx
) -> RequestDetailOut:
    """`GET /requests/{id}` (ADR-0006 §11, amended): OWN reaches the requester, **or anyone who
    appears on a step of this request, any outcome** — a past decider, a past delegate, anyone
    escalated past, keeps full read access to what they took part in. ORG reaches any. A caller
    who cannot see the request gets **404, not 403** (ADR-0006 §11: "so that request ids cannot
    be probed"), and a wrong-tenant id is indistinguishable from one that never existed.
    """
    if ctx.scope is not Scope.ORG:
        subject = ctx.subject_ref
        request = await selectors.get_request(session, ctx.organization_id, request_id)
        is_requester = (
            request is not None and subject is not None and request.requester_employee_id == subject
        )
        on_a_step = subject is not None and await selectors.caller_is_on_any_step(
            session, ctx.organization_id, request_id, subject
        )
        if request is None or not (is_requester or on_a_step):
            raise NotFoundProblem("That request isn't available to you")

    return await _build_detail(
        session,
        organization_id=ctx.organization_id,
        request_id=request_id,
        viewer_subject_ref=ctx.subject_ref,
        viewer_is_org=ctx.scope is Scope.ORG,
    )


@router.post(
    "/approvals/requests/{request_id}/decision",
    response_model=RequestDetailOut,
    summary="Approve or reject a pending request",
)
async def decide_request(
    request_id: uuid.UUID, body: DecisionIn, session: SessionDep, ctx: DecideCtx
) -> RequestDetailOut:
    """`POST /requests/{id}/decision` (ADR-0006 §7). `require("core.approval.request.decide")`
    is only the first gate; `app/services.decide_request` re-checks ADR-0006 §6 under the
    request row's lock and is what actually decides who may act — **403** not eligible, **409**
    already decided (with the recorded outcome, via the conflict message)."""
    await services.decide_request(
        session,
        organization_id=ctx.organization_id,
        request_id=request_id,
        caller_id=ctx.subject_ref,
        has_org_decide=ctx.scope is Scope.ORG,
        outcome=body.outcome,
        comment=body.comment,
        is_owner="ORG_OWNER" in ctx.principal.roles,
        self_approve=body.self_approve,
        caller_user_id=ctx.principal.sub,
    )
    await session.flush()
    # Built through the same shape `GET /requests/{id}` returns, so the two endpoints can never
    # drift — the decider is, by definition, on a step of the request they just decided, so no
    # separate visibility check is needed here.
    return await _build_detail(
        session,
        organization_id=ctx.organization_id,
        request_id=request_id,
        viewer_subject_ref=ctx.subject_ref,
        viewer_is_org=ctx.scope is Scope.ORG,
    )


# --------------------------------------------------------------------- delegation (slice 3)


def _delegation_out(
    delegation: Delegation, people: dict[uuid.UUID, PersonInfo], now: datetime
) -> DelegationOut:
    """The shape every delegation endpoint returns, built once so create/list/revoke can never
    drift on field names (the same discipline `_build_detail` uses for the request surface)."""
    return DelegationOut(
        id=delegation.id,
        delegator_employee_id=delegation.delegator_employee_id,
        delegator_name=_name(people, delegation.delegator_employee_id),
        delegator_location_code=_location(people, delegation.delegator_employee_id),
        delegate_employee_id=delegation.delegate_employee_id,
        delegate_name=_name(people, delegation.delegate_employee_id),
        starts_at=delegation.starts_at,
        ends_at=delegation.ends_at,
        # `domain.delegation_status` returns `domain.DelegationStatus`, the same alias
        # `DelegationOut.status` is typed with — no `cast` needed to reconcile the two.
        status=domain.delegation_status(
            revoked_at=delegation.revoked_at,
            starts_at=delegation.starts_at,
            ends_at=delegation.ends_at,
            at=now,
        ),
        created_by_employee_id=delegation.created_by_employee_id,
        revoked_at=delegation.revoked_at,
        revoked_by_employee_id=delegation.revoked_by_employee_id,
    )


@router.get(
    "/approvals/delegate-candidates",
    response_model=list[DelegateCandidateOut],
    summary="Search colleagues to delegate to",
)
async def list_delegate_candidates(
    session: SessionDep,
    ctx: DelegationCtx,
    q: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=2),
        Query(description="At least two characters"),
    ],
) -> list[DelegateCandidateOut]:
    """`GET /delegate-candidates` (ADR-0006 §11, screens §4.E): the delegation picker's source.
    Gated on `delegation.write` at **any** scope — reachable only by someone who could actually
    create a delegation, not a general employee directory (this is why the endpoint exists
    instead of widening an HRMS scope, ADR-0006 §11). Excludes the caller and every exited node;
    `q` is required and at least two characters **after stripping whitespace**:
    `StringConstraints(strip_whitespace=True, min_length=2)` strips before it measures, not
    after, so a request whose `q` is all whitespace (`"  "`) or is one real character padded
    with whitespace (`"a "`) is a clean 422 here rather than reaching
    `selectors.search_delegate_candidates` as an empty or one-character prefix. That distinction
    is not cosmetic: `delegation.write` collapses to OWN in the default MEMBER template
    (`app/catalogue.py`), so every employee holds it, and a prefix search with no effective floor
    is a staff-directory enumeration ("a ".."z ", "0 ".."9 ") any employee could run to
    completion in a few dozen requests — well under the gateway's per-minute rate limit. Capped
    at `DELEGATE_CANDIDATES_LIMIT` regardless of how many rows match.
    """
    candidates = await selectors.search_delegate_candidates(
        session,
        organization_id=ctx.organization_id,
        exclude_employee_id=ctx.subject_ref,
        q=q,
        limit=DELEGATE_CANDIDATES_LIMIT,
    )
    return [
        DelegateCandidateOut(
            employee_id=c.employee_id, display_name=c.display_name, employee_no=c.employee_no
        )
        for c in candidates
    ]


@router.get(
    "/approvals/delegations",
    response_model=Page[DelegationOut],
    summary="Delegations I hold (OWN) or every delegation (ORG)",
)
async def list_delegations(
    session: SessionDep,
    ctx: DelegationCtx,
    params: PageQuery,
    employee_id: Annotated[
        uuid.UUID | None,
        Query(description="Only delegations this employee is a party to, in either direction"),
    ] = None,
) -> Page[DelegationOut]:
    """`GET /delegations` (ADR-0006 §11): ORG reaches every delegation in the organization;
    below ORG (read as OWN — `delegation.write` never gains TEAM or LOCATION) reaches a row the
    caller is either party to, delegator or delegate.

    `?employee_id=` is the ORG screen's "filtered to a person" state (screens §4.E): rows where
    that employee is the delegator **or** the delegate. It narrows, it never widens — the scope
    predicate is still applied first and this `AND`s with it (`selectors.delegations_stmt`), the
    same way `?requester=me` layers on top of scope on `GET /requests`. It exists because the
    shipped UI had no server-side filter and so filtered an already-loaded page client-side while
    still rendering the server's unfiltered `total`; with this the count and the rows agree.

    **Not refused below ORG, deliberately** — unlike `POST /delegations`' `delegator_employee_id`,
    which is. The two are different questions. A below-ORG caller's scope predicate is already
    "delegator = me OR delegate = me", so `AND`ing "delegator = them OR delegate = them" can only
    return rows the caller is *also* a party to — rows their own unfiltered first page already
    contains, with both employee ids on them in plain sight. The count follows the same statement,
    so `total` cannot leak a row either. And a non-matching, non-existent or cross-tenant id is an
    empty page with `total = 0`, identical to a real employee this caller shares no delegation
    with — no existence oracle, which is exactly what `POST`'s refusal exists to prevent: there,
    naming a foreign delegator reaches `create_delegation`'s overlap check, whose 409 names the
    conflicting window and so would confirm that a stranger holds a delegation and when. Nothing
    on this path reads a row the scope predicate did not already admit.
    """
    is_org = ctx.scope is Scope.ORG
    stmt = selectors.delegations_stmt(
        organization_id=ctx.organization_id,
        caller_id=ctx.subject_ref,
        is_org=is_org,
        employee_id=employee_id,
    )
    rows, total = await paginate(session, stmt, params)

    person_ids: set[uuid.UUID] = set()
    for row in rows:
        person_ids.add(row.delegator_employee_id)
        person_ids.add(row.delegate_employee_id)
    people = await selectors.get_hierarchy_people(session, ctx.organization_id, person_ids)
    db_now = await services.db_now(session)

    items = [_delegation_out(row, people, db_now) for row in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/approvals/delegations",
    response_model=DelegationCreateOut,
    status_code=201,
    summary="Create a delegation",
)
async def create_delegation(
    body: DelegationCreateIn, session: SessionDep, ctx: DelegationCtx
) -> DelegationCreateOut:
    """`POST /delegations` (ADR-0006 §5, §11). `delegator_employee_id` omitted resolves to the
    caller; supplying a *different* id needs ORG (screens §4.E's "for another employee") — a
    below-ORG caller doing so is refused here, at the permission boundary, before
    `app/services.create_delegation` ever runs, so a business-rule refusal (self-delegation, an
    overlap) can never leak anything about a delegator the caller was never authorized to name.
    Every other refusal — self-delegation, an end before or in the past, an overlapping active
    delegation, a delegate who has left — is `app/services.create_delegation`'s.
    """
    caller_id = ctx.subject_ref
    if caller_id is None:
        # `created_by_employee_id` is `NOT NULL` — a membership with no employee link has no
        # identity to record as the creator, the same "nothing to act as" reading ADR-0006 §6
        # rule 2 gives deciding.
        raise ForbiddenProblem("No employee link to create a delegation")
    is_org = ctx.scope is Scope.ORG
    delegator_id = (
        body.delegator_employee_id if body.delegator_employee_id is not None else caller_id
    )
    if delegator_id != caller_id and not is_org:
        raise ForbiddenProblem("Creating a delegation for another employee needs ORG scope")

    delegation, repointed_count = await services.create_delegation(
        session,
        organization_id=ctx.organization_id,
        delegator_employee_id=delegator_id,
        delegate_employee_id=body.delegate_employee_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        created_by_employee_id=caller_id,
        actor_user_id=ctx.principal.sub,
    )
    await session.flush()

    people = await selectors.get_hierarchy_people(
        session, ctx.organization_id, {delegator_id, body.delegate_employee_id}
    )
    db_now = await services.db_now(session)
    base = _delegation_out(delegation, people, db_now)
    return DelegationCreateOut(**base.model_dump(), repointed_count=repointed_count)


@router.post(
    "/approvals/delegations/{delegation_id}/revoke",
    response_model=DelegationOut,
    summary="Revoke a delegation",
)
async def revoke_delegation(
    delegation_id: uuid.UUID, session: SessionDep, ctx: DelegationCtx
) -> DelegationOut:
    """`POST /delegations/{id}/revoke` (ADR-0006 §5, §11). `app/services.revoke_delegation` is
    where the object-level rule lives: below ORG only the delegator may revoke, a bystander gets
    **404**, an already-revoked delegation gets a clean **409** rather than the guard trigger's
    raw exception."""
    caller_id = ctx.subject_ref
    if caller_id is None:
        raise ForbiddenProblem("No employee link to revoke a delegation")
    is_org = ctx.scope is Scope.ORG

    delegation = await services.revoke_delegation(
        session,
        organization_id=ctx.organization_id,
        delegation_id=delegation_id,
        caller_id=caller_id,
        is_org=is_org,
        actor_user_id=ctx.principal.sub,
    )
    await session.flush()

    people = await selectors.get_hierarchy_people(
        session,
        ctx.organization_id,
        {delegation.delegator_employee_id, delegation.delegate_employee_id},
    )
    db_now = await services.db_now(session)
    return _delegation_out(delegation, people, db_now)


# ----------------------------------------------------------------- policy administration (slice 4)


@router.get(
    "/approvals/policies",
    response_model=list[PolicyOut],
    summary="SLA policy per request type",
)
async def list_policies(session: SessionDep, ctx: ReadCtx) -> list[PolicyOut]:
    """`GET /policies` (ADR-0006 §10, §11): read-only, org-wide, and — deliberately — the one
    read endpoint whose scope is not consulted at all. An SLA policy is organization
    configuration, not anybody's row, so a caller holding `request.read` at **any** scope
    (including plain `OWN`, which every member holds by default) sees exactly what an ORG reader
    does: the settings screen's own read-only rendering for someone without `policy.write:ORG`
    depends on this (screens §4.G's "No permission" state — "you can still see the current
    settings below").

    Iterates `services.KNOWN_REQUEST_TYPES` rather than only the request types a `PUT` has ever
    written, so a brand-new organization that has never opened a regularization request still
    sees the one type this service knows about, honestly at its defaults
    (`app/selectors.list_policies`; screens §4.G's "Empty" state: "Using the default settings").
    """
    policies = await selectors.list_policies(
        session, ctx.organization_id, services.KNOWN_REQUEST_TYPES
    )
    return [
        PolicyOut(request_type=p.request_type, sla_minutes=p.sla_minutes, enabled=p.enabled)
        for p in policies
    ]


@router.put(
    "/approvals/policies/{request_type}",
    response_model=PolicyOut,
    summary="Set the SLA policy for one request type",
)
async def put_policy(
    request_type: RequestTypePath,
    payload: PolicyUpdateIn,
    session: SessionDep,
    ctx: PolicyWriteCtx,
) -> PolicyOut:
    """`PUT /policies/{request_type}` (ADR-0006 §8, §10, §11), floored at `policy.write:ORG`.

    `app/schemas.PolicyUpdateIn`'s own bounds mirror `ApprovalPolicy`'s CHECK exactly, so a
    sub-floor or over-ceiling `sla_minutes` never reaches `app/services.update_policy` at all —
    it is a field-level 422 at the boundary (`errors: [{"field": "sla_minutes", ...}]`), not the
    constraint's generic, field-less 409. `app/services.update_policy` writes only
    `sla_minutes`/`enabled`: **disabling turns off escalation for this request type, and nothing
    else** (§10 — "It never turns off approval") — this endpoint has no path to a request's own
    status, by construction, not by convention.
    """
    policy = await services.update_policy(
        session,
        organization_id=ctx.organization_id,
        request_type=request_type,
        sla_minutes=payload.sla_minutes,
        enabled=payload.enabled,
        actor_id=ctx.principal.sub,
    )
    return PolicyOut(
        request_type=policy.request_type, sla_minutes=policy.sla_minutes, enabled=policy.enabled
    )

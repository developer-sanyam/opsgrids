"""Customer HTTP surface for approval.

Fronted by `app-gateway` at `/api/v1/approvals`, with **no `product` key** in the route table
(ADR-0006 §1, §14): approval is a platform surface every customer has, the same shape as
`/api/v1/members`, and must work for a customer who has bought nothing yet.

Slice 1 ships exactly one route, `GET /chain/{employee_id}` (ADR-0006 §5, §11). Every other
route this ADR describes — the inbox, requests, decisions, delegations, policies — opens on top
of tables (`approval_requests`, `approval_steps`, `delegations`, `approval_policies`) that do not
exist until slice 2.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import settings
from app.models import SCHEMA
from app.schemas import ApprovalChainOut, ChainRungOut, DottedLineManagerOut, ServiceInfo
from platform_core.auth import ScopeContext, require
from platform_core.db import get_tenant_session

router = APIRouter(tags=["approval"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]
ReadCtx = Annotated[ScopeContext, Depends(require("core.approval.request.read"))]


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

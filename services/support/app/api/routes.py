"""HTTP surface for the support service.

Every route declares the permission it needs. Support is a **shared queue**, so both reading and
writing are floored at ORG (`minimum=Scope.ORG`): there is no self-service "own tickets" reading
to protect, and a route guarded by the permission name alone would let any holder act. Per-agent
narrowing is the `?assignee=` query filter, not a security boundary.

Every principal route is also guarded by `require_product("support")` — defence in depth. The
gateway already refuses a request for a product the organization has not activated (402), but a
routing-table mistake must not become an entitlement bypass.

Reads go through `selectors`, writes through `services`. A router that builds its own query is
how an organization leaks, so none of them do. Every route takes the tenant-bound session and
opens no transaction of its own — the chokepoint owns it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, scope, selectors, services
from app.config import settings
from app.models import SCHEMA, Agent, SlaPolicy, Ticket, TicketMessage
from app.schemas import (
    AgentIn,
    AgentOut,
    AgentPatch,
    AssignIn,
    BreakdownSliceOut,
    MessageOut,
    OverviewOut,
    ReplyIn,
    Requester,
    ServiceInfo,
    SlaPolicyIn,
    SlaPolicyOut,
    SlaTargetOut,
    StatusIn,
    TicketCreateIn,
    TicketOut,
    TicketUpdateIn,
    VolumePointOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

# Registering the support scope resolver at import of the api module ties it to the product it
# serves: the process that mounts these routes is the one that answers support scope questions.
scope.register()

router = APIRouter(tags=["support"])

#: **The tenancy chokepoint.** Opens the transaction and binds it to the caller's organization
#: before the route body runs, so no route can read or write without one.
SessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]

#: The `support` entitlement gate, applied to every principal route as well as at the gateway.
Entitled = Depends(require_product("support"))

#: Reading and writing the shared queue both floor at ORG — there is no "own" reading here.
TicketRead = Annotated[
    ScopeContext, Depends(require("support.desk.ticket.read", minimum=Scope.ORG))
]
TicketWrite = Annotated[
    ScopeContext, Depends(require("support.desk.ticket.write", minimum=Scope.ORG))
]
#: Agent and SLA-policy administration are narrower than the desk's day-to-day write: only
#: SUPPORT_ADMIN holds these (`catalogue.py`), not every agent who can reply and assign.
AgentWrite = Annotated[
    ScopeContext, Depends(require("support.desk.agent.write", minimum=Scope.ORG))
]
SlaPolicyWrite = Annotated[
    ScopeContext, Depends(require("support.desk.sla_policy.write", minimum=Scope.ORG))
]


# --------------------------------------------------------------------- assemblers


def _message_out(msg: TicketMessage) -> MessageOut:
    return MessageOut(
        id=msg.ref,
        author_type=msg.author_type,
        author_name=msg.author_name,
        at=msg.at,
        body=msg.body,
        internal=msg.internal,
    )


def _ticket_out(ticket: Ticket, messages: list[TicketMessage]) -> TicketOut:
    return TicketOut(
        id=ticket.number,
        subject=ticket.subject,
        requester=Requester(
            name=ticket.requester_name,
            email=ticket.requester_email,
            company=ticket.requester_company,
            kind=ticket.requester_kind,
        ),
        priority=ticket.priority,
        status=ticket.status,
        channel=ticket.channel,
        assignee_id=ticket.assignee_ref,
        tags=list(ticket.tags),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        first_response_at=ticket.first_response_at,
        sla_due=ticket.sla_due,
        sla_breached=ticket.sla_breached,
        satisfaction=ticket.satisfaction,
        messages=[_message_out(m) for m in messages],
    )


def _agent_out(agent: Agent) -> AgentOut:
    return AgentOut(
        id=agent.ref,
        name=agent.name,
        email=agent.email,
        role=agent.role,
        team=agent.team,
        open_load=agent.open_load,
        avg_first_response_min=agent.avg_first_response_min,
        csat_pct=agent.csat_pct,
        solved_7d=agent.solved_7d,
        online=agent.online,
    )


def _policy_out(policy: SlaPolicy) -> SlaPolicyOut:
    return SlaPolicyOut(
        name=policy.name,
        business_hours=policy.business_hours,
        coverage=policy.coverage,
        targets=[SlaTargetOut(**t) for t in policy.targets],
    )


def _facts(ticket: Ticket) -> domain.TicketFacts:
    return domain.TicketFacts(
        status=ticket.status,
        priority=ticket.priority,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        first_response_at=ticket.first_response_at,
        sla_breached=ticket.sla_breached,
        satisfaction=ticket.satisfaction,
    )


# --------------------------------------------------------------------- routes


@router.get("/support/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


@router.get(
    "/support/tickets",
    response_model=Page[TicketOut],
    summary="The support queue",
    dependencies=[Entitled],
)
async def list_tickets(
    session: SessionDep,
    ctx: TicketRead,
    params: PageQuery,
    q: Annotated[str | None, Query(description="Subject, number, requester or company")] = None,
    ticket_status: Annotated[str | None, Query(alias="status")] = None,
    priority: Annotated[str | None, Query()] = None,
    assignee: Annotated[str | None, Query(description="Agent id, or 'unassigned'")] = None,
    sort: Annotated[str, Query()] = "number",
) -> Page[TicketOut]:
    stmt = selectors.tickets_stmt(
        ctx, q=q, status=ticket_status, priority=priority, assignee=assignee, sort=sort
    )
    rows, total = await paginate(session, stmt, params)
    threads = await selectors.messages_by_ticket(session, ctx.organization_id, [t.id for t in rows])
    items = [_ticket_out(t, threads.get(t.id, [])) for t in rows]
    return Page.build(items, total=total, params=params)


@router.post(
    "/support/tickets",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open a ticket",
    dependencies=[Entitled],
)
async def create_ticket(
    payload: TicketCreateIn, session: SessionDep, ctx: TicketWrite, request: Request
) -> TicketOut:
    ticket = await services.create_ticket_from(session, ctx, payload, request=request)
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)


@router.get(
    "/support/agents",
    response_model=list[AgentOut],
    summary="Agents who work the queue",
    dependencies=[Entitled],
)
async def list_agents(session: SessionDep, ctx: TicketRead) -> list[AgentOut]:
    agents = await selectors.list_agents(session, ctx.organization_id)
    return [_agent_out(a) for a in agents]


@router.post(
    "/support/agents",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an agent to the desk",
    dependencies=[Entitled],
)
async def create_agent(
    payload: AgentIn, session: SessionDep, ctx: AgentWrite, request: Request
) -> AgentOut:
    agent = await services.create_agent(session, ctx, payload, request=request)
    return _agent_out(agent)


@router.patch(
    "/support/agents/{ref}",
    response_model=AgentOut,
    summary="Edit an agent",
    dependencies=[Entitled],
)
async def update_agent(
    ref: str, payload: AgentPatch, session: SessionDep, ctx: AgentWrite, request: Request
) -> AgentOut:
    agent = await selectors.get_agent(session, ctx.organization_id, ref)
    if agent is None:
        raise NotFoundProblem("That agent is not part of this organization")
    agent = await services.update_agent(session, ctx, agent, payload, request=request)
    return _agent_out(agent)


@router.delete(
    "/support/agents/{ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate an agent",
    dependencies=[Entitled],
)
async def deactivate_agent(
    ref: str, session: SessionDep, ctx: AgentWrite, request: Request
) -> Response:
    agent = await selectors.get_agent(session, ctx.organization_id, ref)
    if agent is None:
        raise NotFoundProblem("That agent is not part of this organization")
    await services.deactivate_agent(session, ctx, agent, request=request)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/support/sla-policy",
    response_model=SlaPolicyOut,
    summary="The SLA policy governing the queue",
    dependencies=[Entitled],
)
async def get_sla_policy(session: SessionDep, ctx: TicketRead) -> SlaPolicyOut:
    # Deliberately still a 404, not a synthesized set of defaults: there is exactly one policy
    # per organization (`uq_sla_policies_org`), and inventing target minutes nobody configured
    # would read as a real SLA commitment. A new organization gets an honest "unset", and the
    # product's empty state (not this endpoint) is where "no policy yet — set one up" belongs.
    policy = await selectors.get_sla_policy(session, ctx.organization_id)
    if policy is None:
        raise NotFoundProblem("No SLA policy is configured for this organization")
    return _policy_out(policy)


@router.put(
    "/support/sla-policy",
    response_model=SlaPolicyOut,
    summary="Set the SLA policy governing the queue",
    dependencies=[Entitled],
)
async def set_sla_policy(
    payload: SlaPolicyIn, session: SessionDep, ctx: SlaPolicyWrite, request: Request
) -> SlaPolicyOut:
    policy = await services.set_sla_policy(session, ctx, payload, request=request)
    return _policy_out(policy)


@router.get(
    "/support/overview",
    response_model=OverviewOut,
    summary="Queue overview: KPIs, volume and breakdowns",
    dependencies=[Entitled],
)
async def overview(
    session: SessionDep,
    ctx: TicketRead,
    now: Annotated[datetime | None, Query(description="Anchor for the volume window")] = None,
) -> OverviewOut:
    tickets = await selectors.all_tickets(session, ctx.organization_id)
    result = domain.build_overview([_facts(t) for t in tickets], now or datetime.now(UTC))
    return OverviewOut(
        open_tickets=result.open_tickets,
        breaching_sla=result.breaching_sla,
        first_response_median_min=result.first_response_median_min,
        csat_pct=result.csat_pct,
        volume=[
            VolumePointOut(label=p.label, date=p.date, opened=p.opened, solved=p.solved)
            for p in result.volume
        ],
        priority_breakdown=[
            BreakdownSliceOut(key=b.key, label=b.label, count=b.count, pct=b.pct)
            for b in result.priority_breakdown
        ],
        status_breakdown=[
            BreakdownSliceOut(key=b.key, label=b.label, count=b.count, pct=b.pct)
            for b in result.status_breakdown
        ],
    )


@router.get(
    "/support/tickets/{number}",
    response_model=TicketOut,
    summary="One ticket, with its thread",
    dependencies=[Entitled],
)
async def read_ticket(number: str, session: SessionDep, ctx: TicketRead) -> TicketOut:
    ticket = await selectors.get_ticket(session, ctx.organization_id, number)
    if ticket is None:
        raise NotFoundProblem("That ticket is not part of this organization")
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)


@router.patch(
    "/support/tickets/{number}",
    response_model=TicketOut,
    summary="Edit a ticket's priority, subject or tags",
    dependencies=[Entitled],
)
async def update_ticket(
    number: str,
    payload: TicketUpdateIn,
    session: SessionDep,
    ctx: TicketWrite,
    request: Request,
) -> TicketOut:
    ticket = await selectors.get_ticket(session, ctx.organization_id, number)
    if ticket is None:
        raise NotFoundProblem("That ticket is not part of this organization")
    await services.update_ticket(session, ctx, ticket, payload, request=request)
    await session.refresh(ticket)
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)


@router.post(
    "/support/tickets/{number}/status",
    response_model=TicketOut,
    summary="Move a ticket through the lifecycle",
    dependencies=[Entitled],
)
async def change_ticket_status(
    number: str,
    payload: StatusIn,
    session: SessionDep,
    ctx: TicketWrite,
    request: Request,
) -> TicketOut:
    ticket = await selectors.get_ticket(session, ctx.organization_id, number)
    if ticket is None:
        raise NotFoundProblem("That ticket is not part of this organization")
    await services.change_status(session, ctx, ticket, payload.status, request=request)
    await session.refresh(ticket)
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)


@router.post(
    "/support/tickets/{number}/reply",
    response_model=TicketOut,
    status_code=status.HTTP_201_CREATED,
    summary="Append a message to a ticket's thread",
    dependencies=[Entitled],
)
async def reply_ticket(
    number: str,
    payload: ReplyIn,
    session: SessionDep,
    ctx: TicketWrite,
    request: Request,
) -> TicketOut:
    ticket = await selectors.get_ticket(session, ctx.organization_id, number)
    if ticket is None:
        raise NotFoundProblem("That ticket is not part of this organization")
    await services.add_message(
        session,
        ctx,
        ticket,
        body=payload.body,
        # Fixed to `agent`, never taken from the client — see `ReplyIn`. This endpoint is the
        # desk's own reply path, so the caller writing it *is* the agent.
        author_type="agent",
        author_name=payload.author_name,
        internal=payload.internal,
        request=request,
    )
    # Load server-generated columns (e.g. `updated_at`) inside the async context, so building the
    # response never triggers a lazy refresh outside the greenlet.
    await session.refresh(ticket)
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)


@router.post(
    "/support/tickets/{number}/assign",
    response_model=TicketOut,
    summary="Assign or unassign a ticket",
    dependencies=[Entitled],
)
async def assign_ticket(
    number: str,
    payload: AssignIn,
    session: SessionDep,
    ctx: TicketWrite,
    request: Request,
) -> TicketOut:
    ticket = await selectors.get_ticket(session, ctx.organization_id, number)
    if ticket is None:
        raise NotFoundProblem("That ticket is not part of this organization")
    await services.assign_ticket(session, ctx, ticket, payload.assignee_id, request=request)
    await session.refresh(ticket)
    messages = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    return _ticket_out(ticket, messages)

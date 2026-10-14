"""Writes for the support service.

Every function follows the platform shape: change the rows, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens that transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened. Events are
staged into the outbox, never published inline (ADR-0000).

The conversation thread is **append-only**: `add_message` inserts, and nothing here ever updates
or deletes a `TicketMessage`. A correction is a new message, never an edit.

Two counters are allocated here under a Postgres advisory lock rather than a counter table:
ticket numbers (`TK-####`) and agent refs (`ag-##`), each per organization, and message refs
(`m#`) per ticket. A plain "read the max, add one" — what this module did before — races: two
concurrent writers can read the same max and both insert the same number, and the unique
constraint that catches it (`uq_tickets_org_number`, `uq_agents_org_ref`,
`uq_ticket_messages_ticket_ref`) turns that into an unhandled 500 rather than a clean retry. The
lock is transaction-scoped (`pg_advisory_xact_lock`), releases automatically at commit or
rollback, and serializes only the writers racing for the same counter — a different
organization's ticket numbering, or a different ticket's message refs, is never blocked by it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Integer, cast, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.domain import IllegalTransition, TicketStatus, assert_can_transition
from app.models import Agent, AuditLog, OutboxEvent, SlaPolicy, Ticket, TicketMessage
from app.schemas import (
    AgentIn,
    AgentPatch,
    SlaPolicyIn,
    TicketCreateIn,
    TicketUpdateIn,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import actor_for, publish_event

EVT_TICKET_CREATED = "support.desk.ticket.created"
EVT_TICKET_REPLIED = "support.desk.ticket.replied"
EVT_TICKET_ASSIGNED = "support.desk.ticket.assigned"
EVT_TICKET_STATUS_CHANGED = "support.desk.ticket.status_changed"
EVT_TICKET_UPDATED = "support.desk.ticket.updated"


def _next_message_ref(count: int) -> str:
    return f"m{count + 1}"


async def _advisory_lock(session: AsyncSession, key: str) -> None:
    """Transaction-scoped, keyed lock — releases automatically at commit or rollback."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


async def next_ticket_number(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The next `TK-####` for this organization — max existing numeric suffix + 1, under an
    advisory lock keyed by the organization so two concurrent creates cannot compute the same
    number and collide on `uq_tickets_org_number`."""
    await _advisory_lock(session, f"support.tickets:{org_id}")
    stmt = select(func.max(cast(func.substring(Ticket.number, 4), Integer))).where(
        Ticket.organization_id == org_id, Ticket.number.like("TK-%")
    )
    top = (await session.execute(stmt)).scalar_one_or_none()
    return f"TK-{(top or 0) + 1:04d}"


async def next_agent_ref(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The next `ag-##` for this organization. Considers soft-deleted agents too —
    `uq_agents_org_ref` is not a partial index, so a deactivated agent's ref stays reserved and
    must not be handed to a new one."""
    await _advisory_lock(session, f"support.agents:{org_id}")
    stmt = select(func.max(cast(func.substring(Agent.ref, 4), Integer))).where(
        Agent.organization_id == org_id, Agent.ref.like("ag-%")
    )
    top = (await session.execute(stmt)).scalar_one_or_none()
    return f"ag-{(top or 0) + 1:02d}"


# --------------------------------------------------------------------- agents & policy
#
# `upsert_agent` and `upsert_sla_policy` are the idempotent, natural-key writers the demo seed
# uses to open a tenant's reference data deterministically. `create_agent` / `update_agent` /
# `deactivate_agent` below are the HTTP-facing, audited paths a SUPPORT_ADMIN uses instead —
# unlike the seed, they allocate the ref themselves and record who did what.


async def upsert_agent(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    ref: str,
    name: str,
    email: str,
    role: str = "agent",
    team: str = "Tier 1",
    open_load: int = 0,
    avg_first_response_min: int = 0,
    csat_pct: int = 0,
    solved_7d: int = 0,
    online: bool = False,
) -> Agent:
    """Create or update one agent, keyed by `ref` within the organization. Idempotent."""
    agent = await selectors.get_agent(session, ctx.organization_id, ref)
    if agent is None:
        agent = Agent(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            ref=ref,
        )
        session.add(agent)
    else:
        agent.updated_by = str(ctx.principal.sub)
    agent.name = name
    agent.email = email
    agent.role = role
    agent.team = team
    agent.open_load = open_load
    agent.avg_first_response_min = avg_first_response_min
    agent.csat_pct = csat_pct
    agent.solved_7d = solved_7d
    agent.online = online
    await session.flush()
    return agent


async def create_agent(
    session: AsyncSession, ctx: ScopeContext, payload: AgentIn, *, request: Request | None = None
) -> Agent:
    """Add an agent to the desk. `ref` is allocated here, never accepted from the client."""
    ref = await next_agent_ref(session, ctx.organization_id)
    agent = Agent(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ref=ref,
        name=payload.name,
        email=payload.email,
        role=payload.role,
        team=payload.team,
        online=payload.online,
    )
    session.add(agent)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="agent.created",
        entity="agent",
        entity_id=agent.id,
        after={"ref": ref, "name": agent.name, "email": agent.email, "role": agent.role},
        request=request,
    )
    return agent


async def update_agent(
    session: AsyncSession,
    ctx: ScopeContext,
    agent: Agent,
    payload: AgentPatch,
    *,
    request: Request | None = None,
) -> Agent:
    """Patch an agent's plain fields. `ref` is immutable — it is the queue's assignee value."""
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(agent, field) for field in changes}
    for field, value in changes.items():
        setattr(agent, field, value)
    agent.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="agent.updated",
        entity="agent",
        entity_id=agent.id,
        before=before,
        after=changes,
        request=request,
    )
    return agent


async def deactivate_agent(
    session: AsyncSession, ctx: ScopeContext, agent: Agent, *, request: Request | None = None
) -> None:
    """Soft-delete an agent. Every ticket assigned to them is unassigned in the same
    transaction — `assignee_ref` is a soft reference, not a foreign key, and a vanished agent
    must leave the queue's tickets unassigned rather than pointing at a ghost."""
    if agent.deleted_at is not None:
        return  # idempotent: a retried DELETE finds the row already gone

    now = datetime.now(UTC)
    agent.deleted_at = now
    agent.updated_by = str(ctx.principal.sub)

    await session.execute(
        update(Ticket)
        .where(Ticket.organization_id == ctx.organization_id, Ticket.assignee_ref == agent.ref)
        .values(assignee_ref=None, updated_by=str(ctx.principal.sub))
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="agent.deactivated",
        entity="agent",
        entity_id=agent.id,
        before={"deleted_at": None},
        after={"deleted_at": now.isoformat(), "ref": agent.ref},
        request=request,
    )


async def upsert_sla_policy(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    business_hours: str,
    coverage: str,
    targets: list[dict[str, Any]],
    request: Request | None = None,
) -> SlaPolicy:
    """Create or replace the organization's one SLA policy. Idempotent — used by the demo seed
    and by `set_sla_policy` below, the audited HTTP path."""
    policy = await selectors.get_sla_policy(session, ctx.organization_id)
    before = None if policy is None else {"name": policy.name, "targets": policy.targets}
    if policy is None:
        policy = SlaPolicy(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
        )
        session.add(policy)
    else:
        policy.updated_by = str(ctx.principal.sub)
    policy.name = name
    policy.business_hours = business_hours
    policy.coverage = coverage
    policy.targets = targets
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="sla_policy.set",
        entity="sla_policy",
        entity_id=policy.id,
        before=before,
        after={"name": policy.name, "targets": policy.targets},
        request=request,
    )
    return policy


async def set_sla_policy(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: SlaPolicyIn,
    *,
    request: Request | None = None,
) -> SlaPolicy:
    """HTTP convenience over `upsert_sla_policy`."""
    return await upsert_sla_policy(
        session,
        ctx,
        name=payload.name,
        business_hours=payload.business_hours,
        coverage=payload.coverage,
        targets=[t.model_dump() for t in payload.targets],
        request=request,
    )


# --------------------------------------------------------------------- tickets


async def create_ticket(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    number: str,
    subject: str,
    requester: dict[str, str],
    priority: str,
    status: str,
    channel: str,
    assignee_ref: str | None,
    tags: list[str],
    created_at: datetime,
    updated_at: datetime | None = None,
    first_response_at: datetime | None = None,
    sla_due: datetime | None = None,
    sla_breached: bool = False,
    satisfaction: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    request: Request | None = None,
) -> Ticket:
    """Open a ticket with its opening thread. Audited, with a `ticket.created` event staged.

    Used by administration and the demo seed. `number` is unique per organization; a repeat is a
    conflict rather than a duplicate.
    """
    if await selectors.get_ticket(session, ctx.organization_id, number):
        raise ConflictProblem(f"A ticket numbered {number!r} already exists")

    ticket = Ticket(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        number=number,
        subject=subject,
        requester_name=requester["name"],
        requester_email=requester["email"],
        requester_company=requester["company"],
        requester_kind=requester.get("kind", "customer"),
        priority=priority,
        status=status,
        channel=channel,
        assignee_ref=assignee_ref,
        tags=list(tags),
        created_at=created_at,
        updated_at=updated_at or created_at,
        first_response_at=first_response_at,
        sla_due=sla_due,
        sla_breached=sla_breached,
        satisfaction=satisfaction,
    )
    session.add(ticket)
    await session.flush()

    for i, msg in enumerate(messages or []):
        session.add(
            TicketMessage(
                organization_id=ctx.organization_id,
                created_by=str(ctx.principal.sub),
                ticket_id=ticket.id,
                ref=msg.get("id") or _next_message_ref(i),
                author_type=msg["author_type"],
                author_name=msg["author_name"],
                body=msg["body"],
                internal=bool(msg.get("internal", False)),
                at=msg["at"],
            )
        )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ticket.created",
        entity="ticket",
        entity_id=ticket.id,
        after={"number": ticket.number, "subject": ticket.subject, "status": ticket.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TICKET_CREATED,
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ticket_id": str(ticket.id),
            "number": ticket.number,
            "priority": ticket.priority,
            "status": ticket.status,
            "channel": ticket.channel,
        },
        actor=actor_for(ctx),
    )
    return ticket


async def create_ticket_from(
    session: AsyncSession,
    ctx: ScopeContext,
    payload: TicketCreateIn,
    *,
    request: Request | None = None,
) -> Ticket:
    """The HTTP open path: allocate the next number, resolve the assignee, stamp the SLA clock
    from the org's policy when one exists, and open the thread with the requester's own
    message. A new ticket always starts `new`."""
    if payload.assignee_id is not None:
        agent = await selectors.get_agent(session, ctx.organization_id, payload.assignee_id)
        if agent is None:
            raise NotFoundProblem("That agent is not part of this organization")

    now = datetime.now(UTC)
    number = await next_ticket_number(session, ctx.organization_id)

    sla_due: datetime | None = None
    policy = await selectors.get_sla_policy(session, ctx.organization_id)
    if policy is not None:
        target = next((t for t in policy.targets if t.get("priority") == payload.priority), None)
        if target is not None:
            sla_due = now + timedelta(minutes=int(target["first_response_min"]))

    return await create_ticket(
        session,
        ctx,
        number=number,
        subject=payload.subject,
        requester={
            "name": payload.requester.name,
            "email": payload.requester.email,
            "company": payload.requester.company,
            "kind": payload.requester.kind,
        },
        priority=payload.priority,
        status=TicketStatus.NEW.value,
        channel=payload.channel,
        assignee_ref=payload.assignee_id,
        tags=list(payload.tags),
        created_at=now,
        sla_due=sla_due,
        messages=[
            {
                "author_type": "customer",
                "author_name": payload.requester.name,
                "body": payload.body,
                "at": now,
            }
        ],
        request=request,
    )


async def update_ticket(
    session: AsyncSession,
    ctx: ScopeContext,
    ticket: Ticket,
    payload: TicketUpdateIn,
    *,
    request: Request | None = None,
) -> Ticket:
    """Patch a ticket's plain properties. Status and assignee stay on their own endpoints —
    each carries its own audit action and event, so this one never touches them."""
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(ticket, field) for field in changes}
    for field, value in changes.items():
        setattr(ticket, field, value)
    ticket.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ticket.updated",
        entity="ticket",
        entity_id=ticket.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TICKET_UPDATED,
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ticket_id": str(ticket.id),
            "number": ticket.number,
            "changed": sorted(changes),
        },
        actor=actor_for(ctx),
    )
    return ticket


async def change_status(
    session: AsyncSession,
    ctx: ScopeContext,
    ticket: Ticket,
    target: str,
    *,
    request: Request | None = None,
) -> Ticket:
    """Move a ticket through the lifecycle. `assert_can_transition` is the one place the legal
    moves are declared, so an illegal one is always a 409 — there is no free-form status set.
    Solving a ticket clears `sla_due`: the clock has nothing left to measure (`models.py:131`)."""
    current = TicketStatus(ticket.status)
    target_status = TicketStatus(target)
    try:
        assert_can_transition(current, target_status)
    except IllegalTransition as exc:
        raise ConflictProblem(str(exc)) from exc

    before_status = ticket.status
    ticket.status = target_status.value
    if target_status is TicketStatus.SOLVED:
        ticket.sla_due = None
    ticket.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ticket.status_changed",
        entity="ticket",
        entity_id=ticket.id,
        before={"status": before_status},
        after={"status": ticket.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TICKET_STATUS_CHANGED,
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ticket_id": str(ticket.id),
            "number": ticket.number,
            "from": before_status,
            "to": ticket.status,
        },
        actor=actor_for(ctx),
    )
    return ticket


async def add_message(
    session: AsyncSession,
    ctx: ScopeContext,
    ticket: Ticket,
    *,
    body: str,
    author_type: str,
    author_name: str,
    internal: bool = False,
    at: datetime | None = None,
    request: Request | None = None,
) -> TicketMessage:
    """Append one message to a ticket's thread — the append-only write.

    An agent's first non-internal reply also stamps `first_response_at` (once), which is what the
    first-response SLA and the overview median are measured against. A reply to a solved ticket
    reopens it, via the lifecycle state machine.

    Takes the ticket's advisory lock before counting the existing thread: two concurrent
    replies on the same ticket would otherwise both read the same count and compute the same
    `m{n+1}` ref, colliding on `uq_ticket_messages_ticket_ref` instead of landing as `m{n+1}`
    and `m{n+2}`.
    """
    await _advisory_lock(session, f"support.ticket.messages:{ticket.id}")
    existing = await selectors.get_messages(session, ctx.organization_id, ticket.id)
    now = at or datetime.now(UTC)

    message = TicketMessage(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        ticket_id=ticket.id,
        ref=_next_message_ref(len(existing)),
        author_type=author_type,
        author_name=author_name,
        body=body,
        internal=internal,
        at=now,
    )
    session.add(message)

    if author_type == "agent" and not internal and ticket.first_response_at is None:
        ticket.first_response_at = now
    if author_type == "agent" and not internal and ticket.status == TicketStatus.NEW.value:
        ticket.status = TicketStatus.OPEN.value
    elif ticket.status == TicketStatus.SOLVED.value:
        # A reply on a solved ticket reopens it — a legal lifecycle step.
        assert_can_transition(TicketStatus.SOLVED, TicketStatus.OPEN)
        ticket.status = TicketStatus.OPEN.value
    ticket.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ticket.replied",
        entity="ticket",
        entity_id=ticket.id,
        after={"message_ref": message.ref, "author_type": author_type, "internal": internal},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TICKET_REPLIED,
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ticket_id": str(ticket.id),
            "number": ticket.number,
            "message_ref": message.ref,
            "author_type": author_type,
            "internal": internal,
        },
        actor=actor_for(ctx),
    )
    return message


async def assign_ticket(
    session: AsyncSession,
    ctx: ScopeContext,
    ticket: Ticket,
    assignee_ref: str | None,
    *,
    request: Request | None = None,
) -> Ticket:
    """Assign a ticket to an agent, or unassign it. A named agent must exist in the organization —
    an unknown ref reads as not-found rather than silently assigning to a ghost."""
    if assignee_ref is not None:
        agent = await selectors.get_agent(session, ctx.organization_id, assignee_ref)
        if agent is None:
            raise NotFoundProblem("That agent is not part of this organization")

    before = ticket.assignee_ref
    ticket.assignee_ref = assignee_ref
    ticket.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="ticket.assigned",
        entity="ticket",
        entity_id=ticket.id,
        before={"assignee_id": before},
        after={"assignee_id": assignee_ref},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_TICKET_ASSIGNED,
        aggregate_type="ticket",
        aggregate_id=ticket.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "ticket_id": str(ticket.id),
            "number": ticket.number,
            "assignee_id": assignee_ref,
        },
        actor=actor_for(ctx),
    )
    return ticket

"""Writes for the support service.

Every function follows the platform shape: change the rows, record the audit entry, stage the
event — **all inside one transaction**. `get_tenant_session` opens that transaction and does not
commit for the route, so a crash between the state change and the event cannot leave one without
the other, and a rolled-back write never leaves an audit row claiming it happened. Events are
staged into the outbox, never published inline (ADR-0000).

The conversation thread is **append-only**: `add_message` inserts, and nothing here ever updates
or deletes a `TicketMessage`. A correction is a new message, never an edit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.domain import TicketStatus, assert_can_transition
from app.models import Agent, AuditLog, OutboxEvent, SlaPolicy, Ticket, TicketMessage
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

EVT_TICKET_CREATED = "support.desk.ticket.created"
EVT_TICKET_REPLIED = "support.desk.ticket.replied"
EVT_TICKET_ASSIGNED = "support.desk.ticket.assigned"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _next_message_ref(count: int) -> str:
    return f"m{count + 1}"


# --------------------------------------------------------------------- agents & policy
#
# Agents and the SLA policy are the desk's reference data. They are written by administration and
# the demo seed rather than by an end-user affordance in this slice, so they have a plain upsert
# path (idempotent by the natural key) and no domain event — nothing downstream reacts to them.


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


async def upsert_sla_policy(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    business_hours: str,
    coverage: str,
    targets: list[dict[str, Any]],
) -> SlaPolicy:
    """Create or replace the organization's one SLA policy. Idempotent."""
    policy = await selectors.get_sla_policy(session, ctx.organization_id)
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
    return policy


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
        actor=_actor(ctx),
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
    """
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
        actor=_actor(ctx),
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
        actor=_actor(ctx),
    )
    return ticket

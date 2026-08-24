"""Reads for the support service. Visibility rules live here, in one place.

Every scoped query calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
explicit filter is not redundant with row-level security: RLS is the net under the tightrope,
not permission to walk without one (CLAUDE.md, ADR-0002). Both stay.

A list endpoint that builds its own query instead of coming through a selector is how an
organization leaks, so routers never write SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, SlaPolicy, Ticket, TicketMessage
from platform_core.auth import ScopeContext

#: The sort keys the queue accepts. `number` is the stable default so the live queue reads like
#: the fixtures the product was designed against.
SORTS = {
    "number": (Ticket.number.asc(),),
    "created": (Ticket.created_at.desc(), Ticket.number.asc()),
    "updated": (Ticket.updated_at.desc(), Ticket.number.asc()),
    "priority": (Ticket.priority.asc(), Ticket.created_at.desc()),
}


def tickets_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    sort: str = "number",
) -> Select[Any]:
    """Scope-filtered ticket list. `ctx.apply` is the enforcement point.

    Support reads are ORG-scoped (a shared queue), so `apply` narrows nothing here — but it is
    called all the same, because a list that skips it is the named leak and every list route
    carries the negative-authz test that proves it does not.
    """
    stmt = select(Ticket).where(
        Ticket.organization_id == ctx.organization_id,
        Ticket.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Ticket.assignee_ref)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Ticket.subject).like(needle),
                func.lower(Ticket.number).like(needle),
                func.lower(Ticket.requester_name).like(needle),
                func.lower(Ticket.requester_company).like(needle),
            )
        )
    if status:
        stmt = stmt.where(Ticket.status == status)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if assignee == "unassigned":
        stmt = stmt.where(Ticket.assignee_ref.is_(None))
    elif assignee:
        stmt = stmt.where(Ticket.assignee_ref == assignee)

    return stmt.order_by(*SORTS.get(sort, SORTS["number"]))


async def get_ticket(session: AsyncSession, org_id: uuid.UUID, number: str) -> Ticket | None:
    """Org-scoped fetch by ticket number. Returns None on a tenant mismatch — confirming a
    number exists in *some* organization is itself a disclosure."""
    stmt = select(Ticket).where(
        Ticket.number == number,
        Ticket.organization_id == org_id,
        Ticket.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_messages(
    session: AsyncSession, org_id: uuid.UUID, ticket_id: uuid.UUID
) -> list[TicketMessage]:
    """A ticket's thread, in conversation order. Org-scoped like every read here."""
    stmt = (
        select(TicketMessage)
        .where(
            TicketMessage.ticket_id == ticket_id,
            TicketMessage.organization_id == org_id,
        )
        .order_by(TicketMessage.at.asc(), TicketMessage.ref.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def messages_by_ticket(
    session: AsyncSession, org_id: uuid.UUID, ticket_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[TicketMessage]]:
    """Threads for many tickets in one query, grouped by ticket id — so a list endpoint reads
    threads without a query per row."""
    if not ticket_ids:
        return {}
    stmt = (
        select(TicketMessage)
        .where(
            TicketMessage.ticket_id.in_(ticket_ids),
            TicketMessage.organization_id == org_id,
        )
        .order_by(TicketMessage.at.asc(), TicketMessage.ref.asc())
    )
    grouped: dict[uuid.UUID, list[TicketMessage]] = {}
    for msg in (await session.execute(stmt)).scalars().all():
        grouped.setdefault(msg.ticket_id, []).append(msg)
    return grouped


async def all_tickets(session: AsyncSession, org_id: uuid.UUID) -> list[Ticket]:
    """Every ticket in the organization — the source the overview rollups fold over."""
    stmt = (
        select(Ticket)
        .where(Ticket.organization_id == org_id, Ticket.deleted_at.is_(None))
        .order_by(Ticket.number.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_agents(session: AsyncSession, org_id: uuid.UUID) -> list[Agent]:
    """The organization's support agents, in a stable order."""
    stmt = (
        select(Agent)
        .where(Agent.organization_id == org_id, Agent.deleted_at.is_(None))
        .order_by(Agent.ref.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_agent(session: AsyncSession, org_id: uuid.UUID, ref: str) -> Agent | None:
    stmt = select(Agent).where(
        Agent.ref == ref,
        Agent.organization_id == org_id,
        Agent.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_sla_policy(session: AsyncSession, org_id: uuid.UUID) -> SlaPolicy | None:
    """The one SLA policy governing this organization's queue, or None if unset."""
    stmt = select(SlaPolicy).where(
        SlaPolicy.organization_id == org_id,
        SlaPolicy.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()

"""Writes for the customer service.

Every function follows the platform shape: change the row, record the audit entry, stage
the event — **all inside one transaction**. `get_tenant_session` opens the transaction and
does not commit for the route, so a crash between the state change and the event cannot
leave one without the other, and a rolled-back write never leaves an audit row claiming it
happened. Events are staged into the outbox, never published inline (ADR-0000).

The CRM SPA is read-only in this slice — its client has no create or update calls — so
these writers are not yet exposed as public routes. They exist to finalise the domain
rules and to drive the demo seed through the real, RLS-bound path (the finance-service
precedent), so every seeded row lands with an audit row and an outbox event exactly as the
live product will produce them.

A contact's phone arrives already in its masked display form and is stored as such — this
slice never accepts a raw number (see `app.models`). `last_activity_at` on a contact is
maintained by `touch_contact_activity`, called when the pipeline logs a touch.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog, Company, Contact, OutboxEvent, Owner
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem
from platform_core.events import publish_event

# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_COMPANY_CREATED = "crm.book.company.created"
EVT_CONTACT_CREATED = "crm.book.contact.created"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


# ------------------------------------------------------------------ owners


async def create_owner(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    full_name: str,
    email: str,
    role: str = "",
    owner_id: uuid.UUID | None = None,
) -> Owner:
    """Add a rep to the directory. `owner_id` may be supplied (the demo seed derives
    deterministic ids so deal-service's rows cross-link); omitted, one is generated.
    Reference data, so no domain event — a rep row is vocabulary, not a business fact."""
    if await selectors.get_owner_by_email(session, ctx.organization_id, email):
        raise ConflictProblem(f"An owner with email {email!r} already exists")

    owner = Owner(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        email=email,
        role=role,
    )
    if owner_id is not None:
        owner.id = owner_id
    session.add(owner)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="owner.created",
        entity="owner",
        entity_id=owner.id,
        after={"full_name": full_name, "email": email, "role": role},
    )
    return owner


# ------------------------------------------------------------------ companies


async def create_company(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    name: str,
    owner_id: uuid.UUID,
    domain: str = "",
    industry: str = "",
    size_band: str = "",
    city: str = "",
    country: str = "",
    status: str = "prospect",
    annual_revenue_minor: int = 0,
    currency: str = "INR",
    company_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    request: Request | None = None,
) -> Company:
    """The core company writer. Inserts the account, records the audit row and stages the
    `created` event — all in the caller's transaction. `annual_revenue_minor` is integer
    minor units of `currency`; no float is accepted anywhere on this path."""
    if await selectors.get_company_by_name(session, ctx.organization_id, name):
        raise ConflictProblem(f"A company named {name!r} already exists")

    company = Company(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        name=name,
        domain=domain,
        industry=industry,
        size_band=size_band,
        city=city,
        country=country,
        status=status,
        owner_id=owner_id,
        annual_revenue_minor=annual_revenue_minor,
        currency=currency.upper(),
    )
    if company_id is not None:
        company.id = company_id
    if created_at is not None:
        company.created_at = created_at
        company.updated_at = created_at
    session.add(company)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="company.created",
        entity="company",
        entity_id=company.id,
        after={"name": name, "status": status, "industry": industry},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_COMPANY_CREATED,
        aggregate_type="company",
        aggregate_id=company.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "company_id": str(company.id),
            "name": name,
            "status": status,
            "owner_id": str(owner_id),
        },
        actor=_actor(ctx),
    )
    return company


# ------------------------------------------------------------------ contacts


async def create_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    full_name: str,
    email: str,
    company_id: uuid.UUID,
    owner_id: uuid.UUID,
    title: str = "",
    phone: str = "",
    status: str = "lead",
    last_activity_at: datetime | None = None,
    contact_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    request: Request | None = None,
) -> Contact:
    """Add a person to an account. Validates the company within the organization — the FK
    would catch a bad id, but a cross-tenant guess must read as not-found, not as a
    constraint error. `phone` is the masked display form (see `app.models`)."""
    if await selectors.get_contact_by_email(session, ctx.organization_id, email):
        raise ConflictProblem(f"A contact with email {email!r} already exists")
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")

    contact = Contact(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        title=title,
        email=email,
        phone=phone,
        company_id=company.id,
        status=status,
        owner_id=owner_id,
        last_activity_at=last_activity_at,
    )
    if contact_id is not None:
        contact.id = contact_id
    if created_at is not None:
        contact.created_at = created_at
        contact.updated_at = created_at
    session.add(contact)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="contact.created",
        entity="contact",
        entity_id=contact.id,
        after={"full_name": full_name, "email": email, "status": status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CONTACT_CREATED,
        aggregate_type="contact",
        aggregate_id=contact.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "contact_id": str(contact.id),
            "company_id": str(company.id),
            "full_name": full_name,
            "status": status,
        },
        actor=_actor(ctx),
    )
    return contact


async def touch_contact_activity(
    session: AsyncSession,
    ctx: ScopeContext,
    contact: Contact,
    *,
    at: datetime,
) -> Contact:
    """Advance a contact's `last_activity_at`. Monotonic: an out-of-order or backfilled
    touch never winds the clock backwards. Called by the pipeline's activity consumer;
    no audit row — this is derived bookkeeping, not a business decision."""
    if contact.last_activity_at is None or at > contact.last_activity_at:
        contact.last_activity_at = at
        contact.updated_by = str(ctx.principal.sub)
        await session.flush()
    return contact

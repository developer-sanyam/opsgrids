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

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import settings
from app.models import AuditLog, Company, Contact, OutboxEvent, Owner
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import publish_event
from platform_core.pii import open_sealed, seal

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
    raw_phone: str | None = None,
    status: str = "lead",
    last_activity_at: datetime | None = None,
    contact_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    request: Request | None = None,
) -> Contact:
    """Add a person to an account. Validates the company within the organization — the FK
    would catch a bad id, but a cross-tenant guess must read as not-found, not as a
    constraint error.

    Two ways to supply a number, and they are not interchangeable. `phone` is the masked
    display form, taken as given — which is what the seeds and the read-only slice have always
    passed. `raw_phone` is a real number: it is sealed into `phone_enc` and the mask is
    *derived* from it, overriding `phone`, so the pair can never disagree. Callers that pass
    neither get the previous behaviour exactly, which is why no existing caller changes.
    """
    if await selectors.get_contact_by_email(session, ctx.organization_id, email):
        raise ConflictProblem(f"A contact with email {email!r} already exists")
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")

    phone_enc: bytes | None = None
    if raw_phone is not None:
        raw = _canonical_e164(raw_phone)
        phone_enc, phone = seal(raw, kind="phone", settings=settings())

    contact = Contact(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        title=title,
        email=email,
        phone=phone,
        phone_enc=phone_enc,
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


# --------------------------------------------------------------------- the contact's real number
#
# A masked number cannot be messaged, so WhatsApp — and every channel after it — needs the real
# digits. The platform already has an answer for sensitive data a machine must read, and this
# uses it rather than inventing a second one: `platform_core.pii.seal` on the way in,
# `open_sealed` on the way out, and exactly two audited paths back to plaintext.

#: E.164: a leading `+`, a non-zero country digit, up to fifteen digits in total.
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _canonical_e164(raw: str) -> str:
    """Normalise a supplied number, or refuse it.

    Validated here, at the write, because this is where the data quality belongs — a number
    stored un-canonical is a message that fails much later, in a channel, with nothing useful
    to say. Separators a human typed are stripped; a number with no country code is **refused**
    rather than guessed at, because guessing a country code sends a stranger someone else's
    invoice.

    The rule is duplicated from notification-service's own check rather than shared, which is a
    deliberate few lines: moving it into `platform_core` would be the right home and a wider
    change than this one. Notification still normalises what it receives, so the two agree by
    construction rather than by trust.
    """
    compact = re.sub(r"[\s\-().]", "", raw.strip())
    if not compact.startswith("+"):
        raise ValidationProblem(
            f"{raw!r} is not in E.164 — a messageable number needs its country code"
        )
    if not _E164.match(compact):
        raise ValidationProblem(f"{raw!r} is not a valid E.164 number")
    return compact


async def set_contact_phone(
    session: AsyncSession,
    ctx: ScopeContext,
    contact: Contact,
    raw_phone: str,
    *,
    request: Request | None = None,
) -> Contact:
    """Seal a real number onto an existing contact, deriving the mask from it.

    The write is audited and the audit records that a number was set — never the number. A
    caller who can see the audit trail must not be able to read the phone book out of it.
    """
    raw = _canonical_e164(raw_phone)
    ciphertext, masked = seal(raw, kind="phone", settings=settings())

    before = contact.phone
    contact.phone_enc = ciphertext
    contact.phone = masked
    contact.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="contact.phone_set",
        entity="contact",
        entity_id=contact.id,
        # Masks only. The point of sealing the number is that it does not appear in rows a
        # reporting query can reach, and an audit row is such a row.
        before={"phone": before},
        after={"phone": masked},
        request=request,
    )
    return contact


async def unmask_contact_phone(
    session: AsyncSession,
    ctx: ScopeContext,
    contact: Contact,
    *,
    request: Request | None = None,
) -> str | None:
    """Reveal a contact's real number to a human — the audited, stepped-up path.

    Reaching here means the caller held `crm.book.contact.read_sensitive` and stepped up
    (`amr={"otp"}`), and the route re-applied their scope to this one contact. The unmask is
    itself an auditable read, so an `entity.sensitive_read` row is written **in the caller's
    transaction**: a revealed value never exists without a record that it was revealed.

    Returns None when nothing is sealed. The audit row is written either way, because the
    auditable act is the attempt, not whether a value happened to be there.
    """
    plaintext: str | None = None
    if contact.phone_enc is not None:
        plaintext = open_sealed(contact.phone_enc, settings=settings())

    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="contact",
        entity_id=contact.id,
        fields=["phone"],
        request=request,
    )
    return plaintext


async def resolve_messaging_phone(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    contact: Contact,
    channel: str,
    service: str,
) -> str | None:
    """Open a contact's number for a **service** that is about to message them.

    The other audited path, and the one WhatsApp uses. There is no principal here — the caller
    is a service holding a short-lived token addressed to customer by name — so the audit row
    carries no actor and names the service in `acting_as` instead. "Which service read this
    customer's number, when, and to send on which channel" is answerable; it would not be if
    this read were unaudited just because a machine made it.

    Deliberately **not** reachable from the automation engine directly: notification asks
    customer over the internal API, so decryption stays inside the service that owns the data
    and the automation engine never holds a key.
    """
    plaintext: str | None = None
    if contact.phone_enc is not None:
        plaintext = open_sealed(contact.phone_enc, settings=settings())

    record_sensitive_read(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        entity="contact",
        entity_id=contact.id,
        fields=[f"phone:{channel}"],
    )
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as=service,
        action="contact.messaging_target_resolved",
        entity="contact",
        entity_id=contact.id,
        after={"channel": channel, "resolved": plaintext is not None},
    )
    return plaintext

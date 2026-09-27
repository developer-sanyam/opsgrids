"""Writes for the customer service.

Every function follows the platform shape: change the row, record the audit entry, stage
the event — **all inside one transaction**. `get_tenant_session` opens the transaction and
does not commit for the route, so a crash between the state change and the event cannot
leave one without the other, and a rolled-back write never leaves an audit row claiming it
happened. Events are staged into the outbox, never published inline (ADR-0000).

These writers drive both the public write routes (`app/api/routes.py`) and the demo seed,
which goes through the same real, RLS-bound path (the finance-service precedent), so every
seeded row lands with an audit row and an outbox event exactly as the live product
produces them.

A contact's phone arrives either already masked (the seed's historical shape) or as a raw
`raw_phone`, sealed here and never stored in the clear (see `app.models`).
`last_activity_at` on a contact is maintained by `touch_contact_activity`, called when the
pipeline logs a touch. Delete is soft throughout: a row's `deleted_at` is set, never
removed, and a company with contacts still attached refuses rather than cascading — the
smaller, safer change (CLAUDE.md; no hard delete of anything with children).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.config import settings
from app.models import AuditLog, Company, Contact, OutboxEvent, Owner
from app.schemas import CompanyPatch, ContactPatch, OwnerPatch
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import Principal, Scope, ScopeContext
from platform_core.cache import enforce_rate_limit
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    RateLimitedProblem,
    UpstreamUnavailableProblem,
    ValidationProblem,
)
from platform_core.events import publish_event
from platform_core.logging import get_logger
from platform_core.models import utcnow
from platform_core.pii import blind_index, open_sealed, seal

log = get_logger("customer.services")

#: The daily reveal budget's window. Not a setting: "per day" is what the budget is called.
_REVEAL_DAILY_WINDOW_SECONDS = 86_400

#: The longest the reveal budget's Redis round trip may take. It is spent inside the request's
#: database transaction (after the contact has been looked up and before it is opened), so an
#: unreachable-but-not-refusing Redis — a dropped route, a stopped container behind a proxy — must
#: not pin a pooled connection until some socket timeout nobody set. Over it is the same 503 an
#: outage is: nothing is revealed.
_REVEAL_BUDGET_TIMEOUT_SECONDS = 2.0


class CompanyDeletedProblem(ConflictProblem):
    """The company id `:convert` was given for this lead already names a row — but it has
    been deleted from the book. Distinct `problem_type` from the generic dedup 409 on purpose:
    lead-service's own client reads `type`, not just the status code, to tell "pick a
    different name" apart from "this specific company is gone" — the two need different
    messages on lead-service's own side, and neither should ever come back with the deleted
    company's own id quietly reused as though nothing were wrong."""

    problem_type = "company-deleted"
    title = "That company has been deleted"


class ContactDeletedProblem(ConflictProblem):
    """The contact-side twin of `CompanyDeletedProblem`, for the same reason: a lead's
    deterministic contact id landing on a row that has since been soft-deleted must refuse,
    not silently hand back a phantom contact's id."""

    problem_type = "contact-deleted"
    title = "That contact has been deleted"


# Event names. `crm.*` because these are the product's own, not the platform's.
EVT_COMPANY_CREATED = "crm.book.company.created"
EVT_COMPANY_UPDATED = "crm.book.company.updated"
EVT_COMPANY_DELETED = "crm.book.company.deleted"
EVT_CONTACT_CREATED = "crm.book.contact.created"
EVT_CONTACT_UPDATED = "crm.book.contact.updated"
EVT_CONTACT_DELETED = "crm.book.contact.deleted"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


#: The permission the pipeline's `activity.logged` consumer runs under. `touch_contact_activity`
#: writes no audit row of its own (derived bookkeeping, not a business decision — see its
#: docstring), so this exists only to give the write path a `ScopeContext`, not to gate anything.
_ACTIVITY_TOUCH_PERMISSION = "crm.book.contact.write"


def service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """The scope context the activity-touch consumer runs under.

    `sub` is a stable, all-zero UUID rather than a random one, so this write is
    attributable to "the platform" rather than to an id nobody can join. ORG scope: the
    event names the contact directly, so there is no caller-owned row to narrow to.
    """
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_ACTIVITY_TOUCH_PERMISSION}:ORG"],
        entitlements=["crm"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=_ACTIVITY_TOUCH_PERMISSION)


#: The permission `ensure_company_and_contact_for_lead` runs under, for the same reason
#: `_ACTIVITY_TOUCH_PERMISSION` exists: to give an internal-API write a `ScopeContext`, not to
#: gate anything — `require_service_token` on the route is what already gated the call.
_LEAD_CONVERSION_PERMISSION = "crm.book.company.write"


def lead_conversion_ctx(organization_id: uuid.UUID, actor_id: uuid.UUID | None) -> ScopeContext:
    """The scope context `:convert`'s internal call runs under. ORG scope because there is no
    caller-owned row to narrow a lead's brand-new company to.

    `actor_id` is the human who asked lead-service to convert, carried in the service token's
    `sub` claim (`app/api/internal.py` checks the token names this organization before this is
    ever called) — attributing the audit trail to the person who acted rather than to nobody.
    Falls back to the stable all-zero id `service_ctx` also uses when the token carried none
    (an older caller, or a genuinely system-initiated conversion), so a missing claim degrades
    to "the platform" rather than raising.
    """
    principal = Principal(
        sub=actor_id if actor_id is not None else uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_LEAD_CONVERSION_PERMISSION}:ORG"],
        entitlements=["crm"],
    )
    return ScopeContext(
        principal=principal, scope=Scope.ORG, permission=_LEAD_CONVERSION_PERMISSION
    )


async def _assert_owner_assignable(
    session: AsyncSession, ctx: ScopeContext, owner_id: uuid.UUID
) -> None:
    """`owner_id` must resolve to an active rep in this organization, and an OWN-scoped
    caller may only assign their own accounts to themselves — they may not hand a company to
    another rep any more than they could already see another rep's book. A deactivated rep
    reads the same as a rep who does not exist: `deactivate_owner` already refused to leave
    one holding a live record, so an assignment reaching one here is either stale client
    state or a former rep's id resurfacing, and either way starting a new assignment on it
    should fail the same way a nonexistent id does.

    Row-locked (`FOR UPDATE`): without it, this read and a concurrent `deactivate_owner` on
    the same rep race — this assignment reads `is_active=True` a moment before the other
    transaction flips it and commits, and the assignment proceeds against a rep who is, by
    the time this transaction commits, already deactivated."""
    owner = await selectors.get_owner_for_update(session, ctx.organization_id, owner_id)
    if owner is None or not owner.is_active:
        raise NotFoundProblem("That owner is not part of this organization")
    if ctx.scope is Scope.OWN and ctx.subject_ref != owner_id:
        raise ForbiddenProblem("You can only assign companies you own to yourself")


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
        raise ConflictProblem("An owner with that email already exists")

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


async def update_owner(
    session: AsyncSession,
    ctx: ScopeContext,
    owner: Owner,
    payload: OwnerPatch,
) -> Owner:
    """Edit a rep's plain fields. Renaming onto another live rep's email refuses, the same
    conflict `create_owner` already guards against. Reference data, so no domain event —
    unchanged from `create_owner`'s own reasoning."""
    changes = payload.model_dump(exclude_unset=True, exclude={"is_active"})

    if "email" in changes and changes["email"] != owner.email:
        existing = await selectors.get_owner_by_email(
            session, ctx.organization_id, changes["email"]
        )
        if existing is not None and existing.id != owner.id:
            raise ConflictProblem("An owner with that email already exists")

    before = {field: getattr(owner, field) for field in changes}
    for field, value in changes.items():
        setattr(owner, field, value)
    owner.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="owner.updated",
        entity="owner",
        entity_id=owner.id,
        before=before,
        after=changes,
    )
    return owner


async def deactivate_owner(
    session: AsyncSession,
    ctx: ScopeContext,
    owner: Owner,
) -> Owner:
    """Deactivate a rep. Refuses (409) while they still own a live company or contact —
    reassign those first (`update_company`/`update_contact`'s `owner_id`), deactivate second.
    The smaller, safer choice over cascading a reassignment automatically: this service has
    no way to know who *should* inherit the book, and guessing is how an account silently
    changes hands. A rep already inactive is a no-op, not an error — deactivating twice is
    exactly the retry a client should be free to make.

    Re-fetches and row-locks `owner` by id before checking anything: the route's own read is
    unlocked, and without re-locking here this could run its "still owns a live record" check
    against a book that a concurrent `create_company`/`update_company` is, in that same
    instant, assigning to this same rep — both transactions would see zero open records and
    both would commit, leaving a deactivated owner holding a company after all."""
    locked = await selectors.get_owner_for_update(session, ctx.organization_id, owner.id)
    if locked is None:
        raise NotFoundProblem("That owner is not part of this organization")
    owner = locked
    if not owner.is_active:
        return owner

    open_records = await selectors.count_open_records_for_owner(
        session, ctx.organization_id, owner.id
    )
    if open_records > 0:
        noun = "company" if open_records == 1 else "companies"
        raise ConflictProblem(
            f"{owner.full_name} still owns {open_records} live {noun} or contact(s). "
            "Reassign them to another rep before deactivating."
        )

    owner.is_active = False
    owner.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="owner.deactivated",
        entity="owner",
        entity_id=owner.id,
        before={"is_active": True},
        after={"is_active": False},
    )
    return owner


async def reactivate_owner(
    session: AsyncSession,
    ctx: ScopeContext,
    owner: Owner,
) -> Owner:
    """The reverse of `deactivate_owner`. No guard on the way back in — a rep who left and
    returned starts with an empty book, so there is nothing to conflict with."""
    if owner.is_active:
        return owner

    owner.is_active = True
    owner.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="owner.reactivated",
        entity="owner",
        entity_id=owner.id,
        before={"is_active": False},
        after={"is_active": True},
    )
    return owner


# ------------------------------------------------------------------ companies


async def _lock_company_name(session: AsyncSession, org_id: uuid.UUID, name: str) -> None:
    """Serializes every writer racing to claim the same (organization, case-insensitive name)
    pair for the rest of this transaction.

    `Company`'s own unique index stayed case-*sensitive* (see its comment in `models.py`) so a
    migration adding a case-insensitive one would not fail outright on an organization that
    already holds "Acme Ltd" and "ACME LTD" as two rows. That means the database itself no
    longer refuses a *new* case-insensitive duplicate — this lock is what "no new
    case-insensitive duplicate" actually rests on now: two concurrent creates (or a create and
    a rename) for the same name serialize here, so the second one to reach the lock always
    sees the first one's row already committed, rather than both reading "not found" from a
    plain `SELECT` and both inserting.

    `pg_advisory_xact_lock` releases automatically at commit or rollback — there is nothing to
    unlock by hand, and holding it for the rest of the transaction is exactly what is wanted:
    the row this transaction is about to check-then-insert (or check-then-rename) must not
    become visible to a concurrent transaction's own check until this one has committed.
    """
    # Case and whitespace are folded **in SQL**, by the same `lower`/`btrim` the lookup
    # (`selectors.get_company_by_name`) compares with — never in Python. The two disagree on
    # exactly the inputs that matter here ("İ" lowercases to two code points in Python and to
    # one in Postgres, and a C-locale database folds only ASCII at all), so a Python-folded
    # key could hash differently for two spellings the SQL lookup treats as one name, and the
    # lock would serialize nothing. `hashtextextended` (64-bit) rather than `hashtext` (32-bit):
    # a collision only ever costs an unnecessary wait, never a missed one, but there is no
    # reason to make waits likelier than they need to be.
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('company:' || CAST(:org AS text) || ':' "
            "|| lower(btrim(CAST(:name AS text))), 0))"
        ),
        {"org": str(org_id), "name": name},
    )


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
    acting_as: str | None = None,
) -> Company:
    """The core company writer. Inserts the account, records the audit row and stages the
    `created` event — all in the caller's transaction. `annual_revenue_minor` is integer
    minor units of `currency`; no float is accepted anywhere on this path.

    `acting_as` names the calling *service* on the audit row (e.g. `"lead"`) when this write
    was not a person clicking a button but a service holding a token addressed to customer by
    name — `ctx.principal.sub` still carries the human who ultimately asked for it, when the
    caller has one to give (`lead_conversion_ctx`)."""
    await _assert_owner_assignable(session, ctx, owner_id)
    await _lock_company_name(session, ctx.organization_id, name)
    if await selectors.get_company_by_name(session, ctx.organization_id, name):
        raise ConflictProblem("A company with that name already exists")

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
        acting_as=acting_as,
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


async def update_company(
    session: AsyncSession,
    ctx: ScopeContext,
    company: Company,
    payload: CompanyPatch,
    *,
    request: Request | None = None,
) -> Company:
    """Patch the plain company fields. Absent means "leave alone"; only the fields sent
    are checked, changed and audited. Renaming onto another live company's name, or
    reassigning to an owner outside the org (or, for an OWN-scoped caller, to anyone but
    themselves), both refuse."""
    changes = payload.model_dump(exclude_unset=True)

    # Lock order, on every path that takes both: the owner's row first, the name lock second
    # (`create_company` and `ensure_company_and_contact_for_lead` already do it this way). This
    # function used to take them the other way round, so a rename-plus-reassign racing a
    # create naming the same owner and the same name each held the lock the other was waiting
    # for — a deadlock Postgres resolves by killing one of them.
    if "owner_id" in changes:
        await _assert_owner_assignable(session, ctx, changes["owner_id"])

    if "name" in changes and changes["name"] != company.name:
        await _lock_company_name(session, ctx.organization_id, changes["name"])
        existing = await selectors.get_company_by_name(
            session, ctx.organization_id, changes["name"]
        )
        if existing is not None and existing.id != company.id:
            raise ConflictProblem("A company with that name already exists")

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()

    before = {field: getattr(company, field) for field in changes}
    for field, value in changes.items():
        setattr(company, field, value)
    company.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="company.updated",
        entity="company",
        entity_id=company.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_COMPANY_UPDATED,
        aggregate_type="company",
        aggregate_id=company.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "company_id": str(company.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return company


async def delete_company(
    session: AsyncSession,
    ctx: ScopeContext,
    company: Company,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a company. Refused (409) while it still has contacts — cascading the
    soft delete onto them would be the alternative, but refusing is the smaller, safer
    change (CLAUDE.md: no hard delete of anything with children; deal-service cannot see
    this delete, so a deleted company's deals keep the id and render it as "—")."""
    remaining = await selectors.count_contacts_for_company(session, ctx.organization_id, company.id)
    if remaining > 0:
        raise ConflictProblem(
            "This company still has contacts. Reassign or delete them before deleting the account."
        )

    before_deleted_at = company.deleted_at
    company.deleted_at = utcnow()
    company.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="company.deleted",
        entity="company",
        entity_id=company.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": company.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_COMPANY_DELETED,
        aggregate_type="company",
        aggregate_id=company.id,
        payload={"organization_id": str(ctx.organization_id), "company_id": str(company.id)},
        actor=_actor(ctx),
    )


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
    acting_as: str | None = None,
) -> Contact:
    """Add a person to an account. Validates the company within the organization — the FK
    would catch a bad id, but a cross-tenant guess must read as not-found, not as a
    constraint error. `acting_as` — see `create_company`.

    Two ways to supply a number, and they are not interchangeable. `phone` is the masked
    display form, taken as given — which is what the seeds and the read-only slice have always
    passed. `raw_phone` is a real number: it is sealed into `phone_enc` and the mask is
    *derived* from it, overriding `phone`, so the pair can never disagree. Callers that pass
    neither get the previous behaviour exactly, which is why no existing caller changes.
    """
    await _assert_owner_assignable(session, ctx, owner_id)
    if await selectors.get_contact_by_email(session, ctx.organization_id, email):
        raise ConflictProblem("A contact with that email already exists")
    company = await selectors.get_company(session, ctx.organization_id, company_id)
    if company is None:
        raise NotFoundProblem("That company is not part of this organization")

    phone_enc: bytes | None = None
    phone_index: str | None = None
    if raw_phone is not None:
        raw = _canonical_e164(raw_phone)
        cfg = settings()
        phone_enc, phone = seal(raw, kind="phone", settings=cfg)
        phone_index = blind_index(raw, kind="phone", settings=cfg)

    contact = Contact(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        full_name=full_name,
        title=title,
        email=email,
        phone=phone,
        phone_enc=phone_enc,
        phone_index=phone_index,
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
        acting_as=acting_as,
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


async def update_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    contact: Contact,
    payload: ContactPatch,
    *,
    request: Request | None = None,
) -> Contact:
    """Patch the plain contact fields. Never the phone — `ContactPatch` has no `phone` or
    `raw_phone` field, so this can never touch `phone`, `phone_enc` or `phone_index`; the
    number stays on its own dedicated route. Renaming onto another live contact's email,
    moving to a company outside the org, or reassigning to an owner outside the org (or,
    for an OWN-scoped caller, to anyone but themselves), all refuse — the same rule
    `update_company` applies to `owner_id`."""
    changes = payload.model_dump(exclude_unset=True)

    if "email" in changes and changes["email"] != contact.email:
        existing = await selectors.get_contact_by_email(
            session, ctx.organization_id, changes["email"]
        )
        if existing is not None and existing.id != contact.id:
            raise ConflictProblem("A contact with that email already exists")

    if "owner_id" in changes:
        await _assert_owner_assignable(session, ctx, changes["owner_id"])

    if "company_id" in changes:
        company = await selectors.get_company(session, ctx.organization_id, changes["company_id"])
        if company is None:
            raise NotFoundProblem("That company is not part of this organization")

    before = {field: getattr(contact, field) for field in changes}
    for field, value in changes.items():
        setattr(contact, field, value)
    contact.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="contact.updated",
        entity="contact",
        entity_id=contact.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CONTACT_UPDATED,
        aggregate_type="contact",
        aggregate_id=contact.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "contact_id": str(contact.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return contact


async def delete_contact(
    session: AsyncSession,
    ctx: ScopeContext,
    contact: Contact,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a contact. No children to worry about — activities reference a
    contact by plain cross-schema UUID, never a foreign key, so nothing here cascades."""
    before_deleted_at = contact.deleted_at
    contact.deleted_at = utcnow()
    contact.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="contact.deleted",
        entity="contact",
        entity_id=contact.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": contact.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_CONTACT_DELETED,
        aggregate_type="contact",
        aggregate_id=contact.id,
        payload={"organization_id": str(ctx.organization_id), "contact_id": str(contact.id)},
        actor=_actor(ctx),
    )


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


#: The one message both branches below raise. Never the submitted value: a phone number in an
#: error body is a phone number in every place that body travels — a browser console, an error
#: tracker, a support ticket, and (the path that made this a review finding rather than a
#: style note) another service's own exception message when this validation runs behind
#: `ensure_company_and_contact_for_lead` and the caller is lead-service relaying a *lead's*
#: number, not the number the human at the keyboard just typed.
_E164_MESSAGE = "That phone number is not a valid E.164 number, with a country code."


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
    if not compact.startswith("+") or not _E164.match(compact):
        raise ValidationProblem(_E164_MESSAGE)
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
    cfg = settings()
    ciphertext, masked = seal(raw, kind="phone", settings=cfg)

    before = contact.phone
    contact.phone_enc = ciphertext
    contact.phone = masked
    # Written in the same statement as the sealed value, so the two can never disagree — an
    # index pointing at a number the row no longer holds would match the wrong contact.
    contact.phone_index = blind_index(raw, kind="phone", settings=cfg)
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

    Reaching here means the caller held `crm.book.contact.read_sensitive` and presented a
    step-up token (`amr` with `otp` and the step-up marker), and the route re-applied their scope
    to this one contact. The unmask is
    itself an auditable read, so an `entity.sensitive_read` row is written **in the caller's
    transaction**: a revealed value never exists without a record that it was revealed.

    Returns None when nothing is sealed. The audit row is written either way, because the
    auditable act is the attempt, not whether a value happened to be there.
    """
    plaintext: str | None = None
    if contact.phone_enc is not None:
        plaintext = open_sealed(contact.phone_enc, settings=settings())

    # The same action and shape `record_sensitive_read` writes (`{"fields": [...]}`), plus the
    # session that did it. `record_sensitive_read` cannot carry it, so this is `record_audit`
    # directly. The row's own `request_id` comes from `X-Request-ID` when the client sent one,
    # so it is a correlation aid and not evidence of anything; `session_id` — the token's `sid`
    # claim, forwarded by the gateway — is what ties a reveal to the sign-in that authorised it,
    # and `amr` — the token's authentication methods — says *how*: `["pwd", "otp", "stepup"]` for a
    # code proved for this reveal, and the row could not otherwise tell that from a session that
    # gave a code at sign-in. The field's *name* only, never its value.
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="entity.sensitive_read",
        entity="contact",
        entity_id=contact.id,
        after={
            "fields": ["phone"],
            "session_id": ctx.principal.session_id,
            "amr": list(ctx.principal.amr),
        },
        request=request,
    )
    return plaintext


def log_reveal_refused(
    principal: Principal,
    *,
    reason: str,
    contact_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> None:
    """One structured WARNING per refused reveal: who, where, which contact, and why.

    A log line, deliberately not an audit row. A refusal changes nothing, so it is not an
    `entity.sensitive_read` (that action means a value was opened), and an audit table a prober
    can grow one 403 at a time is a cost rather than a control. What a refusal *does* need is to
    be visible — somebody walking contact ids, or a session without the step-up trying every
    contact, is exactly the pattern worth grepping for.

    Never a value: the ids and a reason code, nothing read from the contact. `request_id`
    correlates the line with the request's other logs; it is whatever the client sent as
    `X-Request-ID` when it sent one, so it locates a line and proves nothing about who made the
    call — `actor_id` is the identity.
    """
    log.warning(
        "contact.reveal.refused",
        reason=reason,
        organization_id=str(principal.organization_id),
        actor_id=str(principal.sub),
        contact_id=str(contact_id) if contact_id is not None else None,
        request_id=getattr(request.state, "request_id", None) if request is not None else None,
    )


async def enforce_reveal_budget(ctx: ScopeContext, *, request: Request | None = None) -> None:
    """Spend one reveal from this user's budget in this organization, or raise 429.

    Two fixed windows, both keyed on the organization *and* the user, so one person's budget is
    never another's and the same person in two organizations has two (a membership is
    per-organization): `reveal_burst_limit` per `reveal_burst_window_seconds`, and
    `reveal_daily_limit` per UTC day. The keys are shared with lead-service's lead reveal on
    purpose — the thing being bounded is how much PII one person can read, and two routes with
    two budgets would double it. **Keep the key prefix identical to lead's.**

    **Spent when a contact is about to be opened, not when a request arrives.** It runs after the
    permission, the step-up and the entitlement have passed and after the contact has been shown
    to exist in this organization and to be in the caller's scope — and before it is opened
    (`reveal_contact_phone`). A request that was going to be refused spends nothing, so a stolen
    token cannot exhaust the real person's budget with invalid ids or contacts they may not touch,
    and cannot deny them the reveals they are entitled to. What it costs: probing with ids that
    turn out to be foreign or absent is no longer counted here, only by the gateway's own limits
    — and it reads nothing, since absent and foreign are the same 404.

    A window is fixed, not sliding: a caller can spend the end of one and the start of the next
    (`platform_core.cache.enforce_rate_limit`).

    **Fails closed**, and bounded: `enforce_rate_limit` answers 503 when Redis cannot be reached,
    and a Redis that neither answers nor refuses is the same 503 after
    `_REVEAL_BUDGET_TIMEOUT_SECONDS`, so a Redis outage stops reveals rather than lifting their
    limit or holding a database connection. Nothing else in this service uses Redis.
    """
    cfg = settings()
    who = f"{ctx.organization_id}:{ctx.principal.sub}"
    budgets = (
        ("burst", cfg.reveal_burst_limit, cfg.reveal_burst_window_seconds),
        ("daily", cfg.reveal_daily_limit, _REVEAL_DAILY_WINDOW_SECONDS),
    )
    for name, limit, window_seconds in budgets:
        try:
            async with asyncio.timeout(_REVEAL_BUDGET_TIMEOUT_SECONDS):
                await enforce_rate_limit(
                    f"crm.reveal.{name}:{who}", limit=limit, window_seconds=window_seconds
                )
        except RateLimitedProblem:
            log_reveal_refused(ctx.principal, reason=f"rate_limited_{name}", request=request)
            raise
        except TimeoutError as exc:
            log.error("contact.reveal.budget_timeout", window=name)
            raise UpstreamUnavailableProblem(
                "Cannot verify request limits right now. Please retry shortly."
            ) from exc


async def reveal_contact_phone(
    session: AsyncSession,
    ctx: ScopeContext,
    contact_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> tuple[Contact, str | None]:
    """The reveal route's whole decision, in one function: fetch the contact inside the
    caller's organization (404 for an id that is absent or another organization's — the same
    answer, so nothing is confirmed about ids outside it), re-apply the caller's scope to this
    one contact (403), spend the reveal budget (`enforce_reveal_budget`: 429 when spent, 503 when
    it cannot be checked), then `unmask_contact_phone`. Every refusal logs one WARNING
    (`log_reveal_refused`) — not an audit row."""
    contact = await selectors.get_contact(session, ctx.organization_id, contact_id)
    if contact is None:
        log_reveal_refused(
            ctx.principal, reason="contact_not_found", contact_id=contact_id, request=request
        )
        raise NotFoundProblem("That contact is not part of this organization")
    try:
        ctx.assert_can_touch(owner_id=contact.owner_id)
    except ForbiddenProblem:
        log_reveal_refused(
            ctx.principal, reason="out_of_scope", contact_id=contact.id, request=request
        )
        raise
    # Everything that could refuse this request without a number being opened has now run, so
    # this is where the budget is spent: after the cheap checks, before the decrypt. (A contact
    # with nothing sealed still spends it: the audit row below is written either way.)
    await enforce_reveal_budget(ctx, request=request)
    return contact, await unmask_contact_phone(session, ctx, contact, request=request)


async def invalid_owner_ids(
    session: AsyncSession, organization_id: uuid.UUID, owner_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """Which of `owner_ids` do **not** resolve to an active rep — the read behind
    `owners:validate`. Read-only and unaudited: asking whether an id is a known, active
    owner discloses nothing an internal caller already restricted to lead-service's own
    write paths does not already have a legitimate reason to ask."""
    active = await selectors.active_owner_ids(session, organization_id, owner_ids)
    return [owner_id for owner_id in owner_ids if owner_id not in active]


async def _reconcile_reused_contact_phone(
    session: AsyncSession, ctx: ScopeContext, contact: Contact, *, raw_phone: str | None
) -> None:
    """`ensure_company_and_contact_for_lead`'s own rule for a contact matched by *email*
    (m3): the same person is the same person regardless of which company's lead brought them
    up, and the book already has a number on file for them if it has one at all. A lead's
    phone only ever *fills a gap* — it never overwrites a number this book already trusts,
    because there is no way from here to know which of the two is actually still current.

    Nothing to reconcile when the lead carried no phone. When the contact has none yet, the
    lead's becomes the contact's, sealed the same way `create_contact` seals a fresh one, and
    audited as an ordinary update. When the contact already has a different one, that number
    is kept, and what the lead brought is recorded — masked — as a fact seen during this
    conversion, not silently dropped and not silently applied.
    """
    if not raw_phone:
        return
    cfg = settings()
    raw = _canonical_e164(raw_phone)

    if contact.phone_enc is None:
        before = {"phone": contact.phone}
        phone_enc, phone_masked = seal(raw, kind="phone", settings=cfg)
        phone_index = blind_index(raw, kind="phone", settings=cfg)
        contact.phone_enc = phone_enc
        contact.phone = phone_masked
        contact.phone_index = phone_index
        contact.updated_by = str(ctx.principal.sub)
        await session.flush()
        record_audit(
            session,
            AuditLog,
            organization_id=ctx.organization_id,
            actor_id=ctx.principal.sub,
            acting_as="lead",
            action="contact.updated",
            entity="contact",
            entity_id=contact.id,
            before=before,
            after={"phone": phone_masked},
        )
        return

    # A different number already on file: kept, not overwritten. `seal` for the mask alone —
    # this is a fresh raw value the caller handed in, not a decrypt of anything already
    # sealed, so it carries no `entity.sensitive_read` audit row of its own (same reasoning
    # `create_contact`'s own initial seal already relies on).
    _, lead_phone_masked = seal(raw, kind="phone", settings=cfg)
    if lead_phone_masked == contact.phone:
        return
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        acting_as="lead",
        action="contact.reused_for_lead",
        entity="contact",
        entity_id=contact.id,
        after={"phone_from_lead": lead_phone_masked, "kept_existing_phone": contact.phone},
    )


async def ensure_company_and_contact_for_lead(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    company_id: uuid.UUID,
    contact_id: uuid.UUID,
    company_name: str,
    owner_id: uuid.UUID,
    contact_full_name: str,
    contact_email: str | None,
    contact_raw_phone: str | None,
) -> tuple[Company, Contact | None]:
    """Ensure a company (+ contact) exists for a converting lead — idempotent by id first,
    by name/email second, so retrying `:convert` with the same deterministic ids never
    creates a second row (ADR-0017).

    Order of checks, per entity: the caller's id **in any state** — a replay of a call that
    already succeeded reuses that row, but if it was since *deleted* this refuses outright
    (`CompanyDeletedProblem`/`ContactDeletedProblem`) rather than quietly handing back a
    phantom id or attempting a second `INSERT` at a primary key the table already holds. This
    check runs before `contact_email` is even looked at, so it applies the same way whether or
    not the lead carried an email. Absent a live row at that id, falls to the natural key (a
    lead converting into a company or person this book already has, under a *different* id —
    reused rather than duplicated), then a fresh insert. `contact_email` absent skips the
    contact entirely: `Contact.email` is not nullable, so a lead with no email becomes a
    company with no contact yet, not a contact with a placeholder address.
    """
    await _assert_owner_assignable(session, ctx, owner_id)

    company = await selectors.get_company_by_id_any_state(session, ctx.organization_id, company_id)
    if company is not None and company.deleted_at is not None:
        raise CompanyDeletedProblem(
            "The company this lead would convert into has been deleted from the CRM book"
        )
    if company is None:
        await _lock_company_name(session, ctx.organization_id, company_name)
        company = await selectors.get_company_by_name(session, ctx.organization_id, company_name)
    if company is None:
        company = await create_company(
            session,
            ctx,
            name=company_name,
            owner_id=owner_id,
            status="prospect",
            company_id=company_id,
            acting_as="lead",
        )

    contact: Contact | None = None
    if contact_email:
        contact = await selectors.get_contact_by_id_any_state(
            session, ctx.organization_id, contact_id
        )
        if contact is not None and contact.deleted_at is not None:
            raise ContactDeletedProblem(
                "The contact this lead would convert into has been deleted from the CRM book"
            )
        if contact is None:
            contact = await selectors.get_contact_by_email(
                session, ctx.organization_id, contact_email
            )
            if contact is not None:
                await _reconcile_reused_contact_phone(
                    session, ctx, contact, raw_phone=contact_raw_phone
                )
        if contact is None:
            contact = await create_contact(
                session,
                ctx,
                full_name=contact_full_name,
                email=contact_email,
                company_id=company.id,
                owner_id=owner_id,
                raw_phone=contact_raw_phone,
                status="customer",
                contact_id=contact_id,
                acting_as="lead",
            )

    return company, contact


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

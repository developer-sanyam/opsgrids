"""Reads for the customer service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that
builds its own query instead of coming through a selector is how an organization leaks,
so routers never write SQL.

CRM scope is a shared book with an OWN reading of "the accounts I own" — the owner column
is the rep id. TEAM and LOCATION are not offered (see `scope.py`); a selector passes only
`owner_col`, and `ScopeContext.apply` refuses those rungs rather than widening.

This module also holds the ORM→schema projections, because shaping a row for the wire is
part of reading it, so routes stay thin.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Company, Contact, Owner
from app.schemas import CompanyOut, ContactOut, OwnerOut
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ owners


async def owners(session: AsyncSession, org_id: uuid.UUID) -> list[Owner]:
    """The org's rep directory — reference data for the whole product, ordered by name.
    Read requires `company.read` but is not narrowed by scope: a rep's name has to be
    resolvable on any row a caller can already see, whoever owns that row."""
    stmt = (
        select(Owner)
        .where(Owner.organization_id == org_id, Owner.deleted_at.is_(None))
        .order_by(Owner.full_name.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_owner(session: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID) -> Owner | None:
    """Org-scoped fetch by id — used to verify a create/update's `owner_id` resolves to a
    real rep before it is written onto a company, contact or deal."""
    stmt = select(Owner).where(
        Owner.id == owner_id,
        Owner.organization_id == org_id,
        Owner.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_owner_by_email(session: AsyncSession, org_id: uuid.UUID, email: str) -> Owner | None:
    stmt = select(Owner).where(
        Owner.email == email,
        Owner.organization_id == org_id,
        Owner.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_owner_for_update(
    session: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID
) -> Owner | None:
    """The same fetch as `get_owner`, row-locked for the rest of this transaction.

    `deactivate_owner` and `_assert_owner_assignable` both read-then-write this row (check
    `is_active`/open-record count, then flip a flag or use the id elsewhere in the same
    transaction) — without the lock, two concurrent requests against the same owner race: one
    deactivating while the other assigns them a new company both read a stale `is_active` and
    neither one's check ever sees the other's write.

    `populate_existing=True`: a caller that has already fetched this owner earlier in the same
    session (the internal `leads:convert` route reads owners while validating, for instance)
    would otherwise get the identity map's cached object back unrefreshed by this lock's own
    read — real for the row Postgres locked, stale for the attributes this function returns.
    """
    stmt = (
        select(Owner)
        .where(Owner.id == owner_id, Owner.organization_id == org_id, Owner.deleted_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def active_owner_ids(
    session: AsyncSession, org_id: uuid.UUID, owner_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of `owner_ids` resolve to an active rep in this organization — one query, not
    N, for `owners:validate`'s batch check."""
    if not owner_ids:
        return set()
    stmt = select(Owner.id).where(
        Owner.organization_id == org_id,
        Owner.id.in_(owner_ids),
        Owner.is_active.is_(True),
        Owner.deleted_at.is_(None),
    )
    return set((await session.execute(stmt)).scalars().all())


async def count_open_records_for_owner(
    session: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID
) -> int:
    """How many live companies and contacts this rep still owns — the check that refuses
    `deactivate_owner` rather than leaving `owner_id` pointing at someone no longer active.
    Deal-service's deals are out of reach here (no cross-schema read), so a rep's open deals
    are not part of this count; recorded as an accepted gap rather than silently checked."""
    company_count = (
        await session.execute(
            select(func.count())
            .select_from(Company)
            .where(
                Company.organization_id == org_id,
                Company.owner_id == owner_id,
                Company.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    contact_count = (
        await session.execute(
            select(func.count())
            .select_from(Contact)
            .where(
                Contact.organization_id == org_id,
                Contact.owner_id == owner_id,
                Contact.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    return int(company_count) + int(contact_count)


# ------------------------------------------------------------------ companies


def companies_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered company list. `ctx.apply` is the enforcement point. The search
    matches name, industry and city — the same three fields the SPA filters on."""
    stmt = select(Company).where(
        Company.organization_id == ctx.organization_id,
        Company.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Company.owner_id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Company.name).like(needle),
                func.lower(Company.industry).like(needle),
                func.lower(Company.city).like(needle),
            )
        )
    if status:
        stmt = stmt.where(Company.status == status)

    return stmt.order_by(Company.name.asc())


async def get_company(
    session: AsyncSession, org_id: uuid.UUID, company_id: uuid.UUID
) -> Company | None:
    """Org-scoped fetch by id. None on a tenant mismatch or a soft-deleted row —
    confirming an id exists in *some* organization is itself a disclosure."""
    stmt = select(Company).where(
        Company.id == company_id,
        Company.organization_id == org_id,
        Company.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_company_by_id_any_state(
    session: AsyncSession, org_id: uuid.UUID, company_id: uuid.UUID
) -> Company | None:
    """Org-scoped fetch by id, soft-deleted or not — for an idempotency check that must
    recognise "this id was already used" even after the row it named was deleted, rather than
    attempting a second `INSERT` with a primary key Postgres already holds
    (`ensure_company_and_contact_for_lead`)."""
    stmt = select(Company).where(Company.id == company_id, Company.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def count_contacts_for_company(
    session: AsyncSession, org_id: uuid.UUID, company_id: uuid.UUID
) -> int:
    """How many live contacts a company still has — the check that refuses its delete
    rather than cascading onto them."""
    stmt = (
        select(func.count())
        .select_from(Contact)
        .where(
            Contact.organization_id == org_id,
            Contact.company_id == company_id,
            Contact.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def get_company_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> Company | None:
    """Case-insensitive: "Acme Ltd" and "ACME LTD" name the same account, backed by the
    non-unique `ix_companies_org_name_ci` index.

    Not unique at the database level (see that index's own comment in `models.py` and the
    migration that added it) — an organization from before this lookup existed may already
    hold two rows differing only by case. `scalars().first()` ordered by `created_at`, then
    `id` as the tiebreak, never `scalar_one_or_none()`, so a pre-existing duplicate is a
    deterministic "the oldest one wins" (and two created in the same instant still resolve the
    same way every time) rather than an unhandled `MultipleResultsFound` on a read.

    **Both** sides of the comparison are folded by Postgres's own `lower(btrim(...))` — the
    stored column as well as the input — the same expression `services._lock_company_name`
    hashes. Trimming only the input is a bug this function already had once: stored names are
    never trimmed on the way in, so "Acme " (kept as typed) compared against `btrim("Acme ")`
    never matched itself, the check missed, and the insert hit `uq_companies_org_name` as an
    unhandled `IntegrityError` instead of a 409. A Python-side `str.lower()` would disagree
    with the column's own SQL `lower` on exactly the inputs ("İ") where it matters most.
    `ix_companies_org_name_ci` is on this same `lower(btrim(name))` expression.
    """
    stmt = (
        select(Company)
        .where(
            func.lower(func.btrim(Company.name)) == func.lower(func.btrim(name)),
            Company.organization_id == org_id,
            Company.deleted_at.is_(None),
        )
        .order_by(Company.created_at.asc(), Company.id.asc())
    )
    return (await session.execute(stmt)).scalars().first()


# ------------------------------------------------------------------ contacts


def contacts_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    status: str | None = None,
    company_id: uuid.UUID | None = None,
) -> Select[Any]:
    """Scope-filtered contact list. The search matches name, title and email — the same
    three fields the SPA filters on. `company_id` narrows to one account's contacts (a
    filter within scope, never a widening) — the index `ix_contacts_org_company` backs it,
    so a company's contact card stops fetching the whole book client-side."""
    stmt = select(Contact).where(
        Contact.organization_id == ctx.organization_id,
        Contact.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Contact.owner_id)

    if company_id is not None:
        stmt = stmt.where(Contact.company_id == company_id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.full_name).like(needle),
                func.lower(Contact.title).like(needle),
                func.lower(Contact.email).like(needle),
            )
        )
    if status:
        stmt = stmt.where(Contact.status == status)

    return stmt.order_by(Contact.full_name.asc())


async def get_contact(
    session: AsyncSession, org_id: uuid.UUID, contact_id: uuid.UUID
) -> Contact | None:
    stmt = select(Contact).where(
        Contact.id == contact_id,
        Contact.organization_id == org_id,
        Contact.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_contact_by_id_any_state(
    session: AsyncSession, org_id: uuid.UUID, contact_id: uuid.UUID
) -> Contact | None:
    """Same reasoning as `get_company_by_id_any_state`, for the contact half of the same
    idempotency check."""
    stmt = select(Contact).where(Contact.id == contact_id, Contact.organization_id == org_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_contact_by_email(
    session: AsyncSession, org_id: uuid.UUID, email: str
) -> Contact | None:
    stmt = select(Contact).where(
        Contact.email == email,
        Contact.organization_id == org_id,
        Contact.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ------------------------------------------------------------------ projections (ORM → wire)


def owner_out(owner: Owner) -> OwnerOut:
    return OwnerOut(
        id=str(owner.id),
        full_name=owner.full_name,
        email=owner.email,
        role=owner.role,
        is_active=owner.is_active,
    )


def company_out(company: Company) -> CompanyOut:
    return CompanyOut(
        id=str(company.id),
        name=company.name,
        domain=company.domain,
        industry=company.industry,
        size_band=company.size_band,
        city=company.city,
        country=company.country,
        status=company.status,
        owner_id=str(company.owner_id),
        annual_revenue_minor=company.annual_revenue_minor,
        currency=company.currency,
        created_at=company.created_at,
    )


def contact_out(contact: Contact) -> ContactOut:
    return ContactOut(
        id=str(contact.id),
        full_name=contact.full_name,
        title=contact.title,
        email=contact.email,
        phone=contact.phone,
        company_id=str(contact.company_id),
        status=contact.status,
        owner_id=str(contact.owner_id),
        last_activity_at=contact.last_activity_at,
        created_at=contact.created_at,
    )


async def get_contact_by_phone_index(
    session: AsyncSession, org_id: uuid.UUID, phone_index: str
) -> Contact | None:
    """The contact holding this number, found without decrypting anything.

    Org-scoped like every read here, so an inbound message on one tenant's number can only ever
    match that tenant's contacts. Returns None when nobody holds it — the ordinary case for a
    stranger messaging the business.

    `scalars().first()` rather than `one_or_none()`: nothing stops two contacts in one
    organization carrying the same number (a shared office line, a person listed twice), and a
    lookup that raised on that would turn a data-quality wrinkle into a failed webhook. The
    oldest match wins, deterministically.
    """
    stmt = (
        select(Contact)
        .where(
            Contact.phone_index == phone_index,
            Contact.organization_id == org_id,
            Contact.deleted_at.is_(None),
        )
        .order_by(Contact.created_at.asc())
    )
    return (await session.execute(stmt)).scalars().first()


# ------------------------------------------------------------------ insights export (internal)


def companies_export_stmt(
    *, after_updated_at: datetime | None, after_id: uuid.UUID | None, limit: int
) -> Select[Any]:
    """Keyset page of **every** company, soft-deleted included, in `(updated_at, id)` order —
    insights' backfill read (`GET /internal/v1/insights/export/companies`, ADR-0020 §3,
    `docs/insights/api.md` §6.1). No `domain`, `industry`, `city`, `country` or revenue: only
    what `company_facts` stores — `name`, `status`, `owner_id`.
    """
    stmt = select(Company)
    if after_updated_at is not None and after_id is not None:
        stmt = stmt.where(
            tuple_(Company.updated_at, Company.id)
            > tuple_(literal(after_updated_at), literal(after_id))
        )
    return stmt.order_by(Company.updated_at, Company.id).limit(limit)

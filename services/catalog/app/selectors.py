"""Reads for catalog. Visibility rules live here, in one place.

The definitional tables are global and un-policied, so isolation is **not** the database's job
here the way it is for a tenant table — it is this module's. The load-bearing rule is the
bespoke-visibility filter (`_audience_ok`): a published version restricted to one organization
is returned to that organization and to no other. It replaces the row-level security a global
table cannot carry, so it is defended by a negative-authz test.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    PLAN_ACTIVE,
    PRICE_FLAT,
    VERSION_DRAFT,
    VERSION_PUBLISHED,
    Plan,
    PlanVersion,
    Price,
)


def _audience_ok(org_id: uuid.UUID) -> Any:
    """The bespoke filter: a version is visible if it is public, or restricted to *this* org.

    `restricted_to_org_id IS NULL` is a public version; a non-null value is an audience of
    one. Any other organization is excluded — the whole point of an enterprise price nobody
    else can see (ADR-0004 §2).
    """
    return or_(
        PlanVersion.restricted_to_org_id.is_(None),
        PlanVersion.restricted_to_org_id == org_id,
    )


def _with_children(stmt: Select[Any]) -> Select[Any]:
    return stmt.options(
        selectinload(PlanVersion.prices),
        selectinload(PlanVersion.features),
        selectinload(PlanVersion.seat_classes),
    )


async def visible_plans(
    session: AsyncSession, *, product_key: str, org_id: uuid.UUID
) -> list[tuple[Plan, list[PlanVersion]]]:
    """Active plans for a product, each with the published versions this caller may see.

    A plan whose only published versions are restricted to someone else comes back with an
    empty version list rather than being hidden — the plan exists, its enterprise pricing is
    simply not for this customer.
    """
    plans = list(
        (
            await session.execute(
                select(Plan)
                .where(Plan.product_key == product_key, Plan.state == "active")
                .order_by(Plan.sort_order, Plan.name)
            )
        )
        .scalars()
        .all()
    )
    if not plans:
        return []

    plan_ids = [p.id for p in plans]
    versions = list(
        (
            await session.execute(
                _with_children(
                    select(PlanVersion).where(
                        PlanVersion.plan_id.in_(plan_ids),
                        PlanVersion.state == VERSION_PUBLISHED,
                        _audience_ok(org_id),
                    )
                ).order_by(PlanVersion.version_no)
            )
        )
        .scalars()
        .all()
    )

    by_plan: dict[uuid.UUID, list[PlanVersion]] = {}
    for v in versions:
        by_plan.setdefault(v.plan_id, []).append(v)
    return [(p, by_plan.get(p.id, [])) for p in plans]


async def offerable_versions(
    session: AsyncSession, *, product_key: str, org_id: uuid.UUID
) -> list[tuple[Plan, PlanVersion]]:
    """The versions staff may switch this organization onto, for one product.

    Reuses `visible_plans` and so `_audience_ok`: an active plan, a published version, public or
    negotiated for exactly this organization — another organization's bespoke price is never
    returned. On top of that, **superseded versions are dropped**: publishing a newer version of a
    plan does not grandfather the older one (nothing flips its state, ADR-0004 §2), so an old
    public version stays published and pinnable by id. Offering it beside its successor would let a
    switch land a customer on a price the catalogue has already moved past. A version is superseded
    when a higher-numbered published version of the same plan has the same audience, cadence and
    currency — those are the things a customer cannot cross (one currency and one cadence per
    subscription), so a monthly version is not superseded by a yearly one.
    """
    offered: list[tuple[Plan, PlanVersion]] = []
    for plan, versions in await visible_plans(session, product_key=product_key, org_id=org_id):
        newest: dict[tuple[uuid.UUID | None, str, str], int] = {}
        for version in versions:
            key = (version.restricted_to_org_id, version.billing_interval, version.currency)
            newest[key] = max(newest.get(key, 0), version.version_no)
        offered.extend(
            (plan, version)
            for version in versions
            if version.version_no
            == newest[(version.restricted_to_org_id, version.billing_interval, version.currency)]
        )
    return offered


async def get_visible_plan(
    session: AsyncSession, plan_id: uuid.UUID, *, org_id: uuid.UUID
) -> tuple[Plan, list[PlanVersion]] | None:
    """One plan by id, with the published versions this caller may see."""
    plan = await session.get(Plan, plan_id)
    if plan is None or plan.state != "active":
        return None
    versions = list(
        (
            await session.execute(
                _with_children(
                    select(PlanVersion).where(
                        PlanVersion.plan_id == plan_id,
                        PlanVersion.state == VERSION_PUBLISHED,
                        _audience_ok(org_id),
                    )
                ).order_by(PlanVersion.version_no)
            )
        )
        .scalars()
        .all()
    )
    return plan, versions


# --------------------------------------------------------------------- operator reads


async def get_plan(session: AsyncSession, plan_id: uuid.UUID) -> Plan | None:
    """A plan for an operator — no audience filter, every version regardless of state."""
    return await session.get(Plan, plan_id)


async def get_plan_versions(session: AsyncSession, plan_id: uuid.UUID) -> list[PlanVersion]:
    return list(
        (
            await session.execute(
                _with_children(select(PlanVersion).where(PlanVersion.plan_id == plan_id)).order_by(
                    PlanVersion.version_no
                )
            )
        )
        .scalars()
        .all()
    )


async def get_version(session: AsyncSession, version_id: uuid.UUID) -> PlanVersion | None:
    """One version with its prices and features loaded, for an operator or a clone source."""
    return (
        await session.execute(
            _with_children(select(PlanVersion).where(PlanVersion.id == version_id))
        )
    ).scalar_one_or_none()


async def get_version_names(
    session: AsyncSession, version_ids: Sequence[uuid.UUID]
) -> list[tuple[PlanVersion, Plan]]:
    """Each requested version with its plan, in **one** join — the batch read behind console's
    subscription list, which would otherwise call catalog once per row. Only the version and plan
    columns: no prices, features or seat classes are loaded, since a name is all the caller wants.
    Ids catalog does not know are simply absent from the result."""
    if not version_ids:
        return []
    rows = (
        await session.execute(
            select(PlanVersion, Plan)
            .join(Plan, Plan.id == PlanVersion.plan_id)
            .where(PlanVersion.id.in_(version_ids))
        )
    ).all()
    return [(version, plan) for version, plan in rows]


async def latest_version_no(session: AsyncSession, plan_id: uuid.UUID) -> int:
    highest = (
        await session.execute(
            select(func.max(PlanVersion.version_no)).where(PlanVersion.plan_id == plan_id)
        )
    ).scalar_one_or_none()
    return int(highest or 0)


async def clone_source(
    session: AsyncSession, plan_id: uuid.UUID, *, restricted_to_org_id: uuid.UUID | None
) -> PlanVersion | None:
    """The version a new draft clones from: the highest-numbered version of the plan **with the
    same audience** the new draft is being authored for.

    Used only for its prices and features when the caller does not supply their own, so a
    "bump the version" edit does not have to re-type an unchanged price sheet.

    MAJOR-5 (security review, fix round 1): a plan's version numbers are one shared sequence
    across every audience, so before this filter the highest `version_no` on a plan could be a
    bespoke version negotiated for one organization — cloned into whichever new draft asked next,
    public or another organization's, leaking that organization's negotiated price into it. The
    filter is `IS NOT DISTINCT FROM` because `restricted_to_org_id` is nullable and Postgres's
    `IS DISTINCT FROM` is the equality operator that treats two `NULL`s as equal — an owner
    bumping the *public* plan (`restricted_to_org_id IS NULL`) must clone the newest *public*
    version, never a bespoke one, and `=` alone would silently exclude every public version.
    """
    version_id = (
        await session.execute(
            select(PlanVersion.id)
            .where(
                PlanVersion.plan_id == plan_id,
                PlanVersion.restricted_to_org_id.is_not_distinct_from(restricted_to_org_id),
            )
            .order_by(PlanVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version_id is None:
        return None
    return await get_version(session, version_id)


# --------------------------------------------------------------------- bespoke pricing (console)


async def product_has_any_plan(session: AsyncSession, product_key: str) -> bool:
    """Whether catalog has ever defined a plan for this product — the "unknown product" check
    the bespoke-price endpoint refuses with 404, regardless of a plan's state."""
    return (
        await session.execute(select(Plan.id).where(Plan.product_key == product_key).limit(1))
    ).scalar_one_or_none() is not None


async def get_public_flat_plan(
    session: AsyncSession, product_key: str
) -> tuple[Plan, PlanVersion] | None:
    """The product's public flat plan: an active plan whose newest published, public version
    prices a `flat` component. This is the version a trial pins (ADR-0013 §2) and the one a
    bespoke price clones from — `None` when the product sells nothing this shape, which the
    caller turns into a 409 ("no public flat plan to clone").
    """
    version_id = (
        await session.execute(
            select(PlanVersion.id)
            .join(Plan, PlanVersion.plan_id == Plan.id)
            .join(Price, Price.plan_version_id == PlanVersion.id)
            .where(
                Plan.product_key == product_key,
                Plan.state == PLAN_ACTIVE,
                PlanVersion.state == VERSION_PUBLISHED,
                PlanVersion.restricted_to_org_id.is_(None),
                Price.kind == PRICE_FLAT,
            )
            .order_by(PlanVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version_id is None:
        return None
    version = await get_version(session, version_id)
    if version is None:  # pragma: no cover - defensive, the row was just selected
        return None
    plan = await session.get(Plan, version.plan_id)
    if plan is None:  # pragma: no cover - defensive, FK-guaranteed
        return None
    return plan, version


async def all_public_flat_plans(session: AsyncSession) -> list[tuple[Plan, PlanVersion]]:
    """Every product's public flat plan, one row each — the console pricing screen's catalogue
    of "what a product publicly costs". Small, global, definitional data: loaded whole and
    reduced in Python rather than a window-function query, for the same reason `visible_plans`
    does not paginate.
    """
    rows = (
        await session.execute(
            select(Plan, PlanVersion)
            .join(PlanVersion, PlanVersion.plan_id == Plan.id)
            .join(Price, Price.plan_version_id == PlanVersion.id)
            .where(
                Plan.state == PLAN_ACTIVE,
                PlanVersion.state == VERSION_PUBLISHED,
                PlanVersion.restricted_to_org_id.is_(None),
                Price.kind == PRICE_FLAT,
            )
            .options(selectinload(PlanVersion.prices))
            .order_by(PlanVersion.version_no)
        )
    ).all()
    best: dict[str, tuple[Plan, PlanVersion]] = {}
    for plan, version in rows:
        # Ascending order means the last write per product key is the newest version.
        best[plan.product_key] = (plan, version)
    return [best[key] for key in sorted(best)]


async def find_bespoke_version(
    session: AsyncSession,
    *,
    plan_id: Any,
    organization_id: Any,
    currency: str,
    amount_minor: int,
    billing_interval: str,
) -> PlanVersion | None:
    """A version of this plan already restricted to this organization, at exactly this flat
    price — draft or published. This is what makes authoring a bespoke price idempotent by its
    own content (docs/io-outside-transactions.md): a retried request, or a crash between the
    catalog call and console's own record of it, finds the version it already made instead of
    minting a second one.
    """
    version_id = (
        await session.execute(
            select(PlanVersion.id)
            .join(Price, Price.plan_version_id == PlanVersion.id)
            .where(
                PlanVersion.plan_id == plan_id,
                PlanVersion.restricted_to_org_id == organization_id,
                PlanVersion.currency == currency,
                PlanVersion.billing_interval == billing_interval,
                PlanVersion.state.in_((VERSION_DRAFT, VERSION_PUBLISHED)),
                Price.kind == PRICE_FLAT,
                Price.amount_minor == amount_minor,
            )
            .order_by(PlanVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await get_version(session, version_id) if version_id is not None else None


__all__ = [
    "all_public_flat_plans",
    "clone_source",
    "find_bespoke_version",
    "get_plan",
    "get_plan_versions",
    "get_public_flat_plan",
    "get_version",
    "get_visible_plan",
    "latest_version_no",
    "offerable_versions",
    "product_has_any_plan",
    "visible_plans",
]

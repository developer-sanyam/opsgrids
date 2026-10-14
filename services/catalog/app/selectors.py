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

from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models import (
    PLAN_ACTIVE,
    PLATFORM_ORG,
    PRICE_FLAT,
    VERSION_PUBLISHED,
    Feature,
    IdempotencyKey,
    Plan,
    PlanVersion,
    Price,
    SeatClass,
)
from platform_core.errors import ValidationProblem
from platform_core.pagination import decode_keyset_cursor, encode_keyset_cursor


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
    # `plan` too: `PlanVersionOut` reads the plan's name, product and state off it, and a lazy
    # load inside an async session would raise rather than quietly issue a query.
    return stmt.options(
        selectinload(PlanVersion.prices),
        selectinload(PlanVersion.features),
        selectinload(PlanVersion.seat_classes),
        selectinload(PlanVersion.plan),
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

    A version withdrawn on the Plans page (`published -> grandfathered`, ADR-0030) is no longer
    published, so it is never offered — and, for the same reason, it supersedes nothing: withdraw
    the newest version of a plan and the published one before it is the current one again. Withdraw
    that one too if it must stop selling.
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


async def superseding_version_no(session: AsyncSession, version: PlanVersion) -> int | None:
    """The newest **published** version of the same plan that would supersede `version` the moment
    it is published, or None.

    Superseding is `offerable_versions`' rule: a higher-numbered published version with the same
    audience, cadence and currency. Every read of "what this plan sells now" takes that newest
    one, so a draft numbered below it would be published and still never be offered or shown as
    the price. `IS NOT DISTINCT FROM` on the audience so two public (NULL) versions compare equal.
    """
    newer = (
        await session.execute(
            select(func.max(PlanVersion.version_no)).where(
                PlanVersion.plan_id == version.plan_id,
                PlanVersion.state == VERSION_PUBLISHED,
                PlanVersion.version_no > version.version_no,
                PlanVersion.restricted_to_org_id.is_not_distinct_from(version.restricted_to_org_id),
                PlanVersion.billing_interval == version.billing_interval,
                PlanVersion.currency == version.currency,
            )
        )
    ).scalar_one_or_none()
    return int(newer) if newer is not None else None


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
    """A **published** version of this plan, restricted to this organization, whose sheet is the
    exact shape console authors: one price, `flat`, at this amount and currency, and no seat
    classes. This is what makes authoring a bespoke price idempotent by its own content
    (docs/io-outside-transactions.md): a retried request, or a crash between the catalog call and
    console's own record of it, finds the version it already made instead of minting a second.

    **Published only, and only that shape.** Console drafts and publishes in one transaction, so
    it never leaves a draft behind; a matching *draft* can only be someone else's, such as an
    owner's negotiated sheet under review on the Plans page. Reusing it would let the console
    path, which `billing_ops` may call, publish an owner's draft without the owner-only gate, the
    reason or the review (ADR-0030 §1). For the same reason a published version with any other
    component (a per-seat price, a usage rate, a seat class) is not "the same price" and is not
    returned: the caller asked for one flat amount and nothing else.
    """
    any_price = aliased(Price)
    version_id = (
        await session.execute(
            select(PlanVersion.id)
            .where(
                PlanVersion.plan_id == plan_id,
                PlanVersion.restricted_to_org_id == organization_id,
                PlanVersion.currency == currency,
                PlanVersion.billing_interval == billing_interval,
                PlanVersion.state == VERSION_PUBLISHED,
                select(Price.id)
                .where(
                    Price.plan_version_id == PlanVersion.id,
                    Price.kind == PRICE_FLAT,
                    Price.amount_minor == amount_minor,
                    Price.currency == currency,
                )
                .exists(),
                select(func.count(any_price.id))
                .where(any_price.plan_version_id == PlanVersion.id)
                .scalar_subquery()
                == 1,
                ~select(SeatClass.id).where(SeatClass.plan_version_id == PlanVersion.id).exists(),
            )
            .order_by(PlanVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return await get_version(session, version_id) if version_id is not None else None


# --------------------------------------------------------------------- operator Plans & prices page
#
# Global, un-policied definitional data read by staff (owner and billing_ops), so there is no
# `ctx.apply()` here: the audience filter below is a *view* choice, not isolation. The gate is the
# route's operator-role dependency, tested per route.


def _public_version_exists() -> Any:
    return (
        select(PlanVersion.id)
        .where(PlanVersion.plan_id == Plan.id, PlanVersion.restricted_to_org_id.is_(None))
        .exists()
    )


def _any_version_exists() -> Any:
    return select(PlanVersion.id).where(PlanVersion.plan_id == Plan.id).exists()


def _restricted_version_exists() -> Any:
    return (
        select(PlanVersion.id)
        .where(PlanVersion.plan_id == Plan.id, PlanVersion.restricted_to_org_id.is_not(None))
        .exists()
    )


async def list_plans_page(
    session: AsyncSession,
    *,
    product_key: str | None,
    audience: str,
    state: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Plan], str | None]:
    """One keyset page of plans, ordered `product_key, sort_order, code, id`.

    `audience` chooses which plans appear: `public` (the default) is a plan with a public version
    or with no version yet — a plan being authored is public until a bespoke price says
    otherwise; `restricted` is a plan with at least one audience-of-one version; `all` is every
    plan. Bespoke versions multiply on the one public flat plan per product, so the default view
    is the bounded one.
    """
    stmt = select(Plan)
    if product_key is not None:
        stmt = stmt.where(Plan.product_key == product_key)
    if state is not None:
        stmt = stmt.where(Plan.state == state)
    if audience == "public":
        stmt = stmt.where(or_(_public_version_exists(), ~_any_version_exists()))
    elif audience == "restricted":
        stmt = stmt.where(_restricted_version_exists())
    if cursor is not None:
        key, sort_order, code, plan_id = decode_keyset_cursor(cursor, expected_parts=4)
        try:
            after = (key, int(sort_order), code, uuid.UUID(plan_id))
        except ValueError as exc:
            raise ValidationProblem("Invalid cursor") from exc
        stmt = stmt.where(tuple_(Plan.product_key, Plan.sort_order, Plan.code, Plan.id) > after)
    stmt = stmt.order_by(Plan.product_key, Plan.sort_order, Plan.code, Plan.id).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_keyset_cursor(
            last.product_key, str(last.sort_order), last.code, str(last.id)
        )
    return page, next_cursor


async def version_counts(
    session: AsyncSession, plan_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Per plan: public versions by state, and every audience-of-one version as `bespoke`."""
    if not plan_ids:
        return {}
    is_public = PlanVersion.restricted_to_org_id.is_(None)
    rows = (
        await session.execute(
            select(PlanVersion.plan_id, PlanVersion.state, is_public, func.count())
            .where(PlanVersion.plan_id.in_(plan_ids))
            .group_by(PlanVersion.plan_id, PlanVersion.state, is_public)
        )
    ).all()
    counts: dict[uuid.UUID, dict[str, int]] = {pid: {} for pid in plan_ids}
    for plan_id, state, public, n in rows:
        bucket = counts[plan_id]
        key = state if public else "bespoke"
        bucket[key] = bucket.get(key, 0) + int(n)
    return counts


async def latest_version_numbers(
    session: AsyncSession, plan_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not plan_ids:
        return {}
    rows = (
        await session.execute(
            select(PlanVersion.plan_id, func.max(PlanVersion.version_no))
            .where(PlanVersion.plan_id.in_(plan_ids))
            .group_by(PlanVersion.plan_id)
        )
    ).all()
    return {plan_id: int(n) for plan_id, n in rows}


async def current_public_versions(
    session: AsyncSession, plan_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, PlanVersion]:
    """Each plan's newest published **public** version, with its sheet — what it sells today."""
    if not plan_ids:
        return {}
    newest = (
        select(PlanVersion.id)
        .where(
            PlanVersion.plan_id.in_(plan_ids),
            PlanVersion.state == VERSION_PUBLISHED,
            PlanVersion.restricted_to_org_id.is_(None),
        )
        .order_by(PlanVersion.plan_id, PlanVersion.version_no.desc())
        .distinct(PlanVersion.plan_id)
    )
    ids = list((await session.execute(newest)).scalars().all())
    if not ids:
        return {}
    versions = (
        (await session.execute(_with_children(select(PlanVersion).where(PlanVersion.id.in_(ids)))))
        .scalars()
        .all()
    )
    return {v.plan_id: v for v in versions}


async def version_summaries_page(
    session: AsyncSession,
    plan_id: uuid.UUID,
    *,
    audience: str,
    cursor: str | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of a plan's versions **without** their sheets, newest first, with how many prices
    and features each carries — the plan detail loads a version's sheet only when it is opened."""
    price_count = (
        select(func.count(Price.id))
        .where(Price.plan_version_id == PlanVersion.id)
        .correlate(PlanVersion)
        .scalar_subquery()
    )
    feature_count = (
        select(func.count(Feature.id))
        .where(Feature.plan_version_id == PlanVersion.id)
        .correlate(PlanVersion)
        .scalar_subquery()
    )
    stmt = select(PlanVersion, price_count, feature_count).where(PlanVersion.plan_id == plan_id)
    if audience == "public":
        stmt = stmt.where(PlanVersion.restricted_to_org_id.is_(None))
    elif audience == "restricted":
        stmt = stmt.where(PlanVersion.restricted_to_org_id.is_not(None))
    if cursor is not None:
        (raw,) = decode_keyset_cursor(cursor, expected_parts=1)
        try:
            before = int(raw)
        except ValueError as exc:
            raise ValidationProblem("Invalid cursor") from exc
        stmt = stmt.where(PlanVersion.version_no < before)
    stmt = stmt.order_by(PlanVersion.version_no.desc()).limit(limit + 1)
    rows = (await session.execute(stmt)).all()
    page = rows[:limit]
    items = [
        {
            "id": v.id,
            "plan_id": v.plan_id,
            "version_no": v.version_no,
            "state": v.state,
            "billing_interval": v.billing_interval,
            "currency": v.currency,
            "restricted_to_org_id": v.restricted_to_org_id,
            "published_at": v.published_at,
            "created_at": v.created_at,
            "price_count": int(prices),
            "feature_count": int(features),
        }
        for v, prices, features in page
    ]
    next_cursor = (
        encode_keyset_cursor(str(page[-1][0].version_no)) if len(rows) > limit and page else None
    )
    return items, next_cursor


async def get_version_for_update(
    session: AsyncSession, version_id: uuid.UUID
) -> PlanVersion | None:
    """One version, its row locked for the rest of the transaction, with its sheet loaded.

    Every state-changing write (publish, withdraw, delete) reads through this, so two operators
    acting on the same version serialize on the row and the second sees the first's outcome —
    a clean business-rule refusal rather than a trigger error from a stale read.
    """
    # `populate_existing`: an earlier read in this transaction may have the row in the identity
    # map already, and the whole point of taking the lock is to judge the state it now holds.
    stmt = (
        _with_children(select(PlanVersion).where(PlanVersion.id == version_id))
        .with_for_update(of=PlanVersion)
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_plan_for_update(session: AsyncSession, plan_id: uuid.UUID) -> Plan | None:
    """One plan, its row locked — retire and reactivate judge its state under the lock."""
    return await session.get(Plan, plan_id, with_for_update=True, populate_existing=True)


async def get_idempotency_claim(session: AsyncSession, *, key: str) -> IdempotencyKey | None:
    """The committed claim on this key, read after an `ON CONFLICT DO NOTHING` found it taken."""
    return (
        await session.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.organization_id == PLATFORM_ORG, IdempotencyKey.key == key
            )
        )
    ).scalar_one_or_none()


__all__ = [
    "all_public_flat_plans",
    "clone_source",
    "current_public_versions",
    "find_bespoke_version",
    "get_idempotency_claim",
    "get_plan",
    "get_plan_for_update",
    "get_plan_versions",
    "get_public_flat_plan",
    "get_version",
    "get_version_for_update",
    "get_visible_plan",
    "latest_version_no",
    "latest_version_numbers",
    "list_plans_page",
    "offerable_versions",
    "product_has_any_plan",
    "version_counts",
    "version_summaries_page",
    "visible_plans",
]

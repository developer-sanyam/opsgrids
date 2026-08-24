"""Reads for catalog. Visibility rules live here, in one place.

The definitional tables are global and un-policied, so isolation is **not** the database's job
here the way it is for a tenant table — it is this module's. The load-bearing rule is the
bespoke-visibility filter (`_audience_ok`): a published version restricted to one organization
is returned to that organization and to no other. It replaces the row-level security a global
table cannot carry, so it is defended by a negative-authz test.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import VERSION_PUBLISHED, Plan, PlanVersion


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
    return stmt.options(selectinload(PlanVersion.prices), selectinload(PlanVersion.features))


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


async def latest_version_no(session: AsyncSession, plan_id: uuid.UUID) -> int:
    highest = (
        await session.execute(
            select(func.max(PlanVersion.version_no)).where(PlanVersion.plan_id == plan_id)
        )
    ).scalar_one_or_none()
    return int(highest or 0)


async def clone_source(session: AsyncSession, plan_id: uuid.UUID) -> PlanVersion | None:
    """The version a new draft clones from: the highest-numbered version of the plan.

    Used only for its prices and features when the caller does not supply their own, so a
    "bump the version" edit does not have to re-type an unchanged price sheet.
    """
    version_id = (
        await session.execute(
            select(PlanVersion.id)
            .where(PlanVersion.plan_id == plan_id)
            .order_by(PlanVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if version_id is None:
        return None
    return await get_version(session, version_id)


__all__ = [
    "clone_source",
    "get_plan",
    "get_plan_versions",
    "get_version",
    "get_visible_plan",
    "latest_version_no",
    "visible_plans",
]

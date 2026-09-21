"""Reads for the itsm service. Visibility rules live here, in one place.

Every scoped list calls `ctx.apply(...)` **and** filters `organization_id` explicitly. The
filter is not redundant with row-level security: RLS is the net under the tightrope, not
permission to walk without one (CLAUDE.md, ADR-0002). Both stay. A list endpoint that builds
its own query instead of coming through a selector is how an organization leaks, so routers
never write SQL.

ITSM scope is org-wide with an OWN reading of "the tickets I raised" — the owner column is the
requester id. TEAM and LOCATION are not offered (see `scope.py`); a selector passes only
`owner_col`, and `ScopeContext.apply` refuses those rungs rather than widening.

This module also holds the ORM→schema projections, because shaping a row for the wire is part of
reading it: the human ids, the denormalised catalog name, the derived `affected_asset_tag` and
`open_incident_ids` are all resolved here so routes stay thin.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.models import Asset, CatalogItem, Incident, IncidentWorklog, ServiceRequest
from app.schemas import (
    AssetOut,
    CatalogItemOut,
    IncidentOut,
    ServiceRequestOut,
    WorklogEntryOut,
)
from platform_core.auth import ScopeContext

# ------------------------------------------------------------------ incidents

_PRIORITY_SORT = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


def incidents_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    priority: str | None = None,
    status: str | None = None,
    sort: str | None = None,
) -> Select[Any]:
    """Scope-filtered incident list. `ctx.apply` is the enforcement point."""
    stmt = select(Incident).where(
        Incident.organization_id == ctx.organization_id,
        Incident.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Incident.requester_id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Incident.number).like(needle),
                func.lower(Incident.summary).like(needle),
                func.lower(Incident.assignee).like(needle),
                func.lower(Incident.requester).like(needle),
            )
        )
    if priority:
        stmt = stmt.where(Incident.priority == priority)
    if status:
        stmt = stmt.where(Incident.status == status)

    if sort == "priority":
        # Deterministic P1→P4 ordering, most severe first, then most recently touched.
        rank = case(_PRIORITY_SORT, value=Incident.priority, else_=99)
        return stmt.order_by(rank.asc(), Incident.updated_at.desc())
    if sort == "created":
        return stmt.order_by(Incident.created_at.desc())
    return stmt.order_by(Incident.updated_at.desc())


async def get_incident(session: AsyncSession, org_id: uuid.UUID, number: str) -> Incident | None:
    """Org-scoped fetch by human number. None on a tenant mismatch or a soft-deleted row —
    confirming a number exists in *some* organization is itself a disclosure."""
    stmt = select(Incident).where(
        Incident.number == number,
        Incident.organization_id == org_id,
        Incident.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def worklogs_for(
    session: AsyncSession, org_id: uuid.UUID, incident_id: uuid.UUID
) -> list[IncidentWorklog]:
    """The activity thread for one incident, oldest first."""
    stmt = (
        select(IncidentWorklog)
        .where(
            IncidentWorklog.organization_id == org_id,
            IncidentWorklog.incident_id == incident_id,
        )
        .order_by(IncidentWorklog.at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def all_incidents(session: AsyncSession, ctx: ScopeContext) -> list[Incident]:
    """Every in-scope incident — powers the Overview rollups."""
    return list((await session.execute(incidents_stmt(ctx))).scalars().all())


# ------------------------------------------------------------------ assets & the incident links


async def asset_tag_map(
    session: AsyncSession, org_id: uuid.UUID, asset_ids: Sequence[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    """Map a set of asset ids to their tags, for the `affected_asset_tag` projection. `None`s in
    the input (incidents with no affected asset) are skipped."""
    ids = [a for a in asset_ids if a is not None]
    if not ids:
        return {}
    stmt = select(Asset.id, Asset.tag).where(Asset.organization_id == org_id, Asset.id.in_(ids))
    return {row.id: row.tag for row in (await session.execute(stmt)).all()}


def resolve_tag(tags: dict[uuid.UUID, str], asset_id: uuid.UUID | None) -> str | None:
    """The tag for one (possibly absent) asset id, from an `asset_tag_map` result."""
    return tags.get(asset_id) if asset_id is not None else None


async def open_incident_numbers(
    session: AsyncSession, org_id: uuid.UUID
) -> dict[uuid.UUID, list[str]]:
    """For each asset, the numbers of the incidents currently open against it — the drill-down
    behind `Asset.open_incident_ids`. Derived, so it is always consistent with the incidents."""
    stmt = (
        select(Incident.affected_asset_id, Incident.number)
        .where(
            Incident.organization_id == org_id,
            Incident.affected_asset_id.is_not(None),
            Incident.status.in_(domain.OPEN_STATUSES),
            Incident.deleted_at.is_(None),
        )
        .order_by(Incident.number.desc())
    )
    out: dict[uuid.UUID, list[str]] = {}
    for asset_id, number in (await session.execute(stmt)).all():
        out.setdefault(asset_id, []).append(number)
    return out


def assets_stmt(
    ctx: ScopeContext,
    *,
    q: str | None = None,
    asset_type: str | None = None,
    status: str | None = None,
) -> Select[Any]:
    """Scope-filtered CMDB asset list."""
    stmt = select(Asset).where(
        Asset.organization_id == ctx.organization_id,
        Asset.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Asset.owner_id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Asset.tag).like(needle),
                func.lower(Asset.name).like(needle),
                func.lower(func.coalesce(Asset.owner, "")).like(needle),
                func.lower(Asset.serial).like(needle),
            )
        )
    if asset_type:
        stmt = stmt.where(Asset.asset_type == asset_type)
    if status:
        stmt = stmt.where(Asset.status == status)

    return stmt.order_by(Asset.tag.asc())


async def get_asset(session: AsyncSession, org_id: uuid.UUID, tag: str) -> Asset | None:
    stmt = select(Asset).where(
        Asset.tag == tag,
        Asset.organization_id == org_id,
        Asset.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_asset_by_tag_for_write(
    session: AsyncSession, org_id: uuid.UUID, tag: str
) -> Asset | None:
    """Same as `get_asset` — named for the write path that resolves an affected asset."""
    return await get_asset(session, org_id, tag)


async def all_assets(session: AsyncSession, ctx: ScopeContext) -> list[Asset]:
    return list((await session.execute(assets_stmt(ctx))).scalars().all())


# ------------------------------------------------------------------ catalog & requests


def catalog_stmt(org_id: uuid.UUID) -> Select[Any]:
    """The org's catalog as a statement, for paginated listing. Not scope-narrowed: the catalog
    is shared reference data within the organization (read still requires `request.read`)."""
    return (
        select(CatalogItem)
        .where(CatalogItem.organization_id == org_id, CatalogItem.deleted_at.is_(None))
        .order_by(CatalogItem.code.asc())
    )


async def catalog_items(session: AsyncSession, org_id: uuid.UUID) -> list[CatalogItem]:
    """The org's service catalog — reference data for the request surface, ordered by code.
    Read requires `request.read` but is not narrowed by scope: the catalog is shared reference
    within the organization, not per-person data."""
    stmt = (
        select(CatalogItem)
        .where(CatalogItem.organization_id == org_id, CatalogItem.deleted_at.is_(None))
        .order_by(CatalogItem.code.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_catalog_item(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> CatalogItem | None:
    stmt = select(CatalogItem).where(
        CatalogItem.code == code,
        CatalogItem.organization_id == org_id,
        CatalogItem.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def requests_stmt(
    ctx: ScopeContext, *, q: str | None = None, status: str | None = None
) -> Select[Any]:
    """Scope-filtered service-request list."""
    stmt = select(ServiceRequest).where(
        ServiceRequest.organization_id == ctx.organization_id,
        ServiceRequest.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=ServiceRequest.requester_id)

    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(ServiceRequest.number).like(needle),
                func.lower(ServiceRequest.requester).like(needle),
                func.lower(ServiceRequest.subject).like(needle),
            )
        )
    if status:
        stmt = stmt.where(ServiceRequest.status == status)

    return stmt.order_by(ServiceRequest.updated_at.desc())


async def get_request(
    session: AsyncSession, org_id: uuid.UUID, number: str
) -> ServiceRequest | None:
    stmt = select(ServiceRequest).where(
        ServiceRequest.number == number,
        ServiceRequest.organization_id == org_id,
        ServiceRequest.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_requests(session: AsyncSession, ctx: ScopeContext) -> list[ServiceRequest]:
    return list((await session.execute(requests_stmt(ctx))).scalars().all())


async def catalog_name_map(session: AsyncSession, org_id: uuid.UUID) -> dict[str, str]:
    """Code→name for the denormalised `catalog_item_name` on a request row."""
    stmt = select(CatalogItem.code, CatalogItem.name).where(
        CatalogItem.organization_id == org_id, CatalogItem.deleted_at.is_(None)
    )
    return {row.code: row.name for row in (await session.execute(stmt)).all()}


# ------------------------------------------------------------------ projections (ORM → wire)


def worklog_out(entry: IncidentWorklog) -> WorklogEntryOut:
    return WorklogEntryOut(
        id=str(entry.id), author=entry.author, body=entry.body, at=entry.at, kind=entry.kind
    )


def incident_out(
    incident: Incident,
    *,
    asset_tag: str | None,
    worklog: Sequence[IncidentWorklog] = (),
) -> IncidentOut:
    return IncidentOut(
        id=incident.number,
        summary=incident.summary,
        description=incident.description,
        priority=incident.priority,
        status=incident.status,
        category=incident.category,
        assignee=incident.assignee,
        requester=incident.requester,
        affected_asset_tag=asset_tag,
        sla_state=incident.sla_state,
        sla_minutes_remaining=incident.sla_minutes_remaining,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        worklog=[worklog_out(w) for w in worklog],
    )


def catalog_out(item: CatalogItem) -> CatalogItemOut:
    return CatalogItemOut(
        id=item.code,
        name=item.name,
        category=item.category,
        description=item.description,
        sla_days=item.sla_days,
        requests_30d=item.requests_30d,
    )


def request_out(request: ServiceRequest, *, catalog_name: str) -> ServiceRequestOut:
    return ServiceRequestOut(
        id=request.number,
        catalog_item_id=request.catalog_item_code,
        catalog_item_name=catalog_name,
        category=request.category,
        requester=request.requester,
        subject=request.subject,
        status=request.status,
        assignee=request.assignee,
        decision_note=request.decision_note,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def asset_out(asset: Asset, *, open_numbers: Sequence[str] = ()) -> AssetOut:
    return AssetOut(
        tag=asset.tag,
        name=asset.name,
        type=asset.asset_type,
        status=asset.status,
        owner=asset.owner,
        location=asset.location,
        serial=asset.serial,
        purchased_at=asset.purchased_at,
        warranty_expires_at=asset.warranty_expires_at,
        cost_minor=asset.cost_minor,
        cost_currency=asset.cost_currency,
        open_incident_ids=list(open_numbers),
    )

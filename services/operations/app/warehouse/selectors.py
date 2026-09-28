"""Reads for warehouse geometry (plan §2/F7), plus the ORM -> wire projections and the two
compound reads that raise their own problem — `validate_layout` and `find_route` — the same
"a read that answers a business question, not just a row" shape `app.dispatch.selectors
.unfulfilled_order_lines` already sets. Every scoped query filters on `organization_id`
explicitly; RLS is the net, not the filter (CLAUDE.md).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.models import LayoutVersion as LayoutVersionModel
from app.models import Location, LocationPlacement, WalkEdge, WalkNode, WarehouseFloor
from app.schemas import (
    LayoutValidationIssueOut,
    LayoutValidationOut,
    LayoutVersionOut,
    LocationPlacementOut,
    WalkEdgeOut,
    WalkNodeOut,
    WarehouseFloorOut,
)
from platform_core.errors import ConflictProblem, NotFoundProblem

# ------------------------------------------------------------------ floors


def floors_stmt(org_id: uuid.UUID, warehouse_id: uuid.UUID) -> Select[Any]:
    return (
        select(WarehouseFloor)
        .where(
            WarehouseFloor.organization_id == org_id,
            WarehouseFloor.warehouse_id == warehouse_id,
            WarehouseFloor.deleted_at.is_(None),
        )
        .order_by(WarehouseFloor.floor_number.asc())
    )


async def get_floor(
    session: AsyncSession, org_id: uuid.UUID, floor_id: uuid.UUID
) -> WarehouseFloor | None:
    stmt = select(WarehouseFloor).where(
        WarehouseFloor.id == floor_id,
        WarehouseFloor.organization_id == org_id,
        WarehouseFloor.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_floor_by_number(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID, floor_number: int
) -> WarehouseFloor | None:
    stmt = select(WarehouseFloor).where(
        WarehouseFloor.organization_id == org_id,
        WarehouseFloor.warehouse_id == warehouse_id,
        WarehouseFloor.floor_number == floor_number,
        WarehouseFloor.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def floor_out(floor: WarehouseFloor) -> WarehouseFloorOut:
    return WarehouseFloorOut(
        id=str(floor.id),
        warehouse_id=str(floor.warehouse_id),
        floor_number=floor.floor_number,
        name=floor.name,
    )


# ------------------------------------------------------------------ layout versions


def layout_versions_stmt(org_id: uuid.UUID, floor_id: uuid.UUID) -> Select[Any]:
    """A floor's version history, newest first — every version this floor has ever had, `draft`,
    `published` and `archived` alike (nothing here is ever deleted once published), which is what
    lets the UI's version-history list offer a rollback target."""
    return (
        select(LayoutVersionModel)
        .where(
            LayoutVersionModel.organization_id == org_id,
            LayoutVersionModel.warehouse_floor_id == floor_id,
            LayoutVersionModel.deleted_at.is_(None),
        )
        .order_by(LayoutVersionModel.version_number.desc())
    )


async def get_layout_version(
    session: AsyncSession, org_id: uuid.UUID, version_id: uuid.UUID, *, for_update: bool = False
) -> LayoutVersionModel | None:
    stmt = select(LayoutVersionModel).where(
        LayoutVersionModel.id == version_id,
        LayoutVersionModel.organization_id == org_id,
        LayoutVersionModel.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_draft_version(
    session: AsyncSession, org_id: uuid.UUID, floor_id: uuid.UUID
) -> LayoutVersionModel | None:
    stmt = select(LayoutVersionModel).where(
        LayoutVersionModel.organization_id == org_id,
        LayoutVersionModel.warehouse_floor_id == floor_id,
        LayoutVersionModel.status == "draft",
        LayoutVersionModel.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_published_version(
    session: AsyncSession, org_id: uuid.UUID, floor_id: uuid.UUID, *, for_update: bool = False
) -> LayoutVersionModel | None:
    stmt = select(LayoutVersionModel).where(
        LayoutVersionModel.organization_id == org_id,
        LayoutVersionModel.warehouse_floor_id == floor_id,
        LayoutVersionModel.status == "published",
        LayoutVersionModel.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def next_version_number(session: AsyncSession, org_id: uuid.UUID, floor_id: uuid.UUID) -> int:
    stmt = select(func.coalesce(func.max(LayoutVersionModel.version_number), 0)).where(
        LayoutVersionModel.organization_id == org_id,
        LayoutVersionModel.warehouse_floor_id == floor_id,
    )
    return int((await session.execute(stmt)).scalar_one()) + 1


def layout_version_out(version: LayoutVersionModel) -> LayoutVersionOut:
    return LayoutVersionOut(
        id=str(version.id),
        warehouse_floor_id=str(version.warehouse_floor_id),
        version_number=version.version_number,
        status=version.status,
        label=version.label,
        published_at=version.published_at,
        published_by=version.published_by,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


# ------------------------------------------------------------------ geometry (placements, walk
# graph) belonging to one layout version


async def placements_for_version(
    session: AsyncSession, org_id: uuid.UUID, version_id: uuid.UUID
) -> list[LocationPlacement]:
    stmt = select(LocationPlacement).where(
        LocationPlacement.organization_id == org_id,
        LocationPlacement.layout_version_id == version_id,
    )
    return list((await session.execute(stmt)).scalars().all())


async def nodes_for_version(
    session: AsyncSession, org_id: uuid.UUID, version_id: uuid.UUID
) -> list[WalkNode]:
    stmt = select(WalkNode).where(
        WalkNode.organization_id == org_id, WalkNode.layout_version_id == version_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def edges_for_version(
    session: AsyncSession, org_id: uuid.UUID, version_id: uuid.UUID
) -> list[WalkEdge]:
    stmt = select(WalkEdge).where(
        WalkEdge.organization_id == org_id, WalkEdge.layout_version_id == version_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def locations_by_id(
    session: AsyncSession, org_id: uuid.UUID, location_ids: set[uuid.UUID]
) -> dict[uuid.UUID, Location]:
    if not location_ids:
        return {}
    stmt = select(Location).where(Location.organization_id == org_id, Location.id.in_(location_ids))
    return {loc.id: loc for loc in (await session.execute(stmt)).scalars().all()}


def placement_out(placement: LocationPlacement, location: Location) -> LocationPlacementOut:
    return LocationPlacementOut(
        location_id=str(placement.location_id),
        x_mm=placement.x_mm,
        y_mm=placement.y_mm,
        z_mm=placement.z_mm,
        width_mm=placement.width_mm,
        depth_mm=placement.depth_mm,
        height_mm=placement.height_mm,
        location_code=location.code,
        location_kind=location.kind,
    )


def walk_node_out(node: WalkNode) -> WalkNodeOut:
    return WalkNodeOut(
        id=str(node.id),
        code=node.code,
        location_id=str(node.location_id) if node.location_id else None,
        x_mm=node.x_mm,
        y_mm=node.y_mm,
        z_mm=node.z_mm,
    )


def walk_edge_out(edge: WalkEdge, code_by_node_id: dict[uuid.UUID, str]) -> WalkEdgeOut:
    return WalkEdgeOut(
        id=str(edge.id),
        from_node_id=str(edge.from_node_id),
        to_node_id=str(edge.to_node_id),
        from_code=code_by_node_id.get(edge.from_node_id, ""),
        to_code=code_by_node_id.get(edge.to_node_id, ""),
        distance_mm=edge.distance_mm,
        is_traversable=edge.is_traversable,
        bidirectional=edge.bidirectional,
    )


# ------------------------------------------------------------------ validation (plan §7: "no
# overlapping locations, no fully disconnected required nodes") and routing


async def validate_layout(
    session: AsyncSession, org_id: uuid.UUID, version: LayoutVersionModel
) -> LayoutValidationOut:
    """Read `version`'s own geometry and run `app.domain`'s pure checks over it. Never mutates or
    persists anything — `app.warehouse.services.publish_layout_version` calls this same function
    as its own real gate, so "preview" (this, over HTTP) and "the thing that actually blocks a
    publish" can never drift apart into two different answers for the same draft."""
    placements = await placements_for_version(session, org_id, version.id)
    nodes = await nodes_for_version(session, org_id, version.id)
    edges = await edges_for_version(session, org_id, version.id)

    boxes = [
        domain.PlacementBox(
            location_id=p.location_id,
            x_mm=p.x_mm,
            y_mm=p.y_mm,
            width_mm=p.width_mm,
            depth_mm=p.depth_mm,
        )
        for p in placements
    ]
    graph_nodes = [domain.GraphNode(id=n.id, location_id=n.location_id) for n in nodes]
    graph_edges = [
        domain.GraphEdge(
            from_node_id=e.from_node_id,
            to_node_id=e.to_node_id,
            distance_mm=e.distance_mm,
            is_traversable=e.is_traversable,
            bidirectional=e.bidirectional,
        )
        for e in edges
    ]

    issues: list[LayoutValidationIssueOut] = []
    for a_id, b_id in domain.overlapping_placements(boxes):
        issues.append(
            LayoutValidationIssueOut(
                kind="overlap",
                message=f"Locations {a_id} and {b_id} overlap on the floor plan",
                location_ids=[str(a_id), str(b_id)],
            )
        )
    node_by_id = {n.id: n for n in nodes}
    for node_id in domain.disconnected_required_nodes(graph_nodes, graph_edges):
        node = node_by_id[node_id]
        issues.append(
            LayoutValidationIssueOut(
                kind="disconnected_node",
                message=f"Walk node {node.code} has no traversable path to the rest of the graph",
                location_ids=[str(node.location_id)] if node.location_id else [],
                node_id=str(node_id),
            )
        )
    return LayoutValidationOut(is_valid=not issues, issues=issues)


async def find_route(
    session: AsyncSession,
    org_id: uuid.UUID,
    floor: WarehouseFloor,
    *,
    from_location_id: uuid.UUID,
    to_location_id: uuid.UUID,
) -> domain.RoutePlan:
    """A path between two locations over `floor`'s **published** layout (plan §7's routing
    requirement) — the one graph every caller agrees is "the real one" at any moment, never a
    draft still being edited. Raises `NotFoundProblem`/`ConflictProblem` itself: there is no
    partial result worth handing back to a caller that cannot use it."""
    published = await get_published_version(session, org_id, floor.id)
    if published is None:
        raise NotFoundProblem(f"Floor {floor.name or floor.floor_number} has no published layout")

    nodes = await nodes_for_version(session, org_id, published.id)
    edges = await edges_for_version(session, org_id, published.id)
    node_by_location = {n.location_id: n for n in nodes if n.location_id is not None}

    from_node = node_by_location.get(from_location_id)
    if from_node is None:
        raise NotFoundProblem(
            f"Location {from_location_id} has no walk node in this floor's published layout"
        )
    to_node = node_by_location.get(to_location_id)
    if to_node is None:
        raise NotFoundProblem(
            f"Location {to_location_id} has no walk node in this floor's published layout"
        )

    graph_nodes = [domain.GraphNode(id=n.id, location_id=n.location_id) for n in nodes]
    graph_edges = [
        domain.GraphEdge(
            from_node_id=e.from_node_id,
            to_node_id=e.to_node_id,
            distance_mm=e.distance_mm,
            is_traversable=e.is_traversable,
            bidirectional=e.bidirectional,
        )
        for e in edges
    ]
    plan = domain.shortest_walk_path(graph_nodes, graph_edges, from_node.id, to_node.id)
    if plan is None:
        raise ConflictProblem(
            f"No traversable path exists between {from_location_id} and {to_location_id} on "
            "this floor's published layout"
        )
    return plan

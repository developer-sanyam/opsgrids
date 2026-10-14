"""Writes for warehouse geometry (plan §2/F7): floors, draft layout versions, a draft's geometry
(placements + walk graph, saved as one full replace), and the two dangerous moves — publish and
its own rollback (the same function, called again on an `archived` version).

Same shape as every other writes module here: change the rows, record the audit entry, stage the
event, all in the caller's transaction (CLAUDE.md: enqueue background work only after commit — the
outbox is what makes that true, not this module).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.models import (
    AuditLog,
    LocationPlacement,
    OutboxEvent,
    WalkEdge,
    WalkNode,
    Warehouse,
    WarehouseFloor,
)
from app.models import LayoutVersion as LayoutVersionModel
from app.schemas import LayoutGeometryIn, WarehouseFloorPatch
from app.warehouse import selectors
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import ConflictProblem, NotFoundProblem, ValidationProblem
from platform_core.events import actor_for, publish_event

EVT_FLOOR_CREATED = "operations.warehouse.floor.created"
EVT_FLOOR_UPDATED = "operations.warehouse.floor.updated"
EVT_LAYOUT_VERSION_OPENED = "operations.warehouse.layout_version.opened"
EVT_LAYOUT_VERSION_DISCARDED = "operations.warehouse.layout_version.discarded"
EVT_LAYOUT_GEOMETRY_SAVED = "operations.warehouse.layout_version.geometry_saved"
EVT_LAYOUT_PUBLISHED = "operations.warehouse.layout_version.published"


# ------------------------------------------------------------------ floors


async def create_floor(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    *,
    floor_number: int,
    name: str,
    request: Request | None = None,
) -> WarehouseFloor:
    org_id = ctx.organization_id
    if await selectors.get_floor_by_number(session, org_id, warehouse.id, floor_number):
        raise ConflictProblem(f"Floor {floor_number} already exists in warehouse {warehouse.code}")
    floor = WarehouseFloor(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        warehouse_id=warehouse.id,
        floor_number=floor_number,
        name=name,
    )
    session.add(floor)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="warehouse_floor.created",
        entity="warehouse_floor",
        entity_id=floor.id,
        after={"warehouse_id": str(warehouse.id), "floor_number": floor_number, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_FLOOR_CREATED,
        aggregate_type="warehouse_floor",
        aggregate_id=floor.id,
        payload={
            "organization_id": str(org_id),
            "warehouse_floor_id": str(floor.id),
            "warehouse_id": str(warehouse.id),
            "floor_number": floor_number,
        },
        actor=actor_for(ctx),
    )
    return floor


async def update_floor(
    session: AsyncSession,
    ctx: ScopeContext,
    floor: WarehouseFloor,
    payload: WarehouseFloorPatch,
    *,
    request: Request | None = None,
) -> WarehouseFloor:
    org_id = ctx.organization_id
    changes = payload.model_dump(exclude_unset=True)
    if "floor_number" in changes and changes["floor_number"] != floor.floor_number:
        existing = await selectors.get_floor_by_number(
            session, org_id, floor.warehouse_id, changes["floor_number"]
        )
        if existing is not None and existing.id != floor.id:
            raise ConflictProblem(
                f"Floor {changes['floor_number']} already exists in this warehouse"
            )

    before = {field: getattr(floor, field) for field in changes}
    for field, value in changes.items():
        setattr(floor, field, value)
    floor.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="warehouse_floor.updated",
        entity="warehouse_floor",
        entity_id=floor.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_FLOOR_UPDATED,
        aggregate_type="warehouse_floor",
        aggregate_id=floor.id,
        payload={"organization_id": str(org_id), "warehouse_floor_id": str(floor.id), **changes},
        actor=actor_for(ctx),
    )
    return floor


# ------------------------------------------------------------------ layout versions


async def _clone_geometry(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    source_version_id: uuid.UUID,
    dest_version_id: uuid.UUID,
) -> None:
    """Copy one version's placements and walk graph onto another, freshly created one — every
    row gets a new id (`open_draft_version`'s "start from what is live" clone), never the source
    version's own rows, which stay exactly as published forever."""
    placements = await selectors.placements_for_version(session, org_id, source_version_id)
    for p in placements:
        session.add(
            LocationPlacement(
                organization_id=org_id,
                layout_version_id=dest_version_id,
                location_id=p.location_id,
                x_mm=p.x_mm,
                y_mm=p.y_mm,
                z_mm=p.z_mm,
                width_mm=p.width_mm,
                depth_mm=p.depth_mm,
                height_mm=p.height_mm,
            )
        )

    nodes = await selectors.nodes_for_version(session, org_id, source_version_id)
    new_node_by_old_id: dict[uuid.UUID, WalkNode] = {}
    for n in nodes:
        new_node = WalkNode(
            organization_id=org_id,
            layout_version_id=dest_version_id,
            code=n.code,
            location_id=n.location_id,
            x_mm=n.x_mm,
            y_mm=n.y_mm,
            z_mm=n.z_mm,
        )
        session.add(new_node)
        new_node_by_old_id[n.id] = new_node
    await session.flush()  # assigns each new_node.id, needed to remap the edges below

    edges = await selectors.edges_for_version(session, org_id, source_version_id)
    for e in edges:
        session.add(
            WalkEdge(
                organization_id=org_id,
                layout_version_id=dest_version_id,
                from_node_id=new_node_by_old_id[e.from_node_id].id,
                to_node_id=new_node_by_old_id[e.to_node_id].id,
                distance_mm=e.distance_mm,
                is_traversable=e.is_traversable,
                bidirectional=e.bidirectional,
            )
        )
    await session.flush()


async def open_draft_version(
    session: AsyncSession,
    ctx: ScopeContext,
    floor: WarehouseFloor,
    *,
    label: str = "",
    clone_from_published: bool = True,
    request: Request | None = None,
) -> LayoutVersionModel:
    org_id = ctx.organization_id
    if await selectors.get_draft_version(session, org_id, floor.id) is not None:
        raise ConflictProblem(
            "This floor already has a draft layout version; edit or discard it before opening "
            "another"
        )
    number = await selectors.next_version_number(session, org_id, floor.id)
    version = LayoutVersionModel(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        warehouse_floor_id=floor.id,
        version_number=number,
        status="draft",
        label=label,
    )
    session.add(version)
    await session.flush()

    cloned_from: uuid.UUID | None = None
    if clone_from_published:
        published = await selectors.get_published_version(session, org_id, floor.id)
        if published is not None:
            await _clone_geometry(
                session, org_id, source_version_id=published.id, dest_version_id=version.id
            )
            cloned_from = published.id

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="layout_version.opened",
        entity="layout_version",
        entity_id=version.id,
        after={
            "warehouse_floor_id": str(floor.id),
            "version_number": number,
            "cloned_from": str(cloned_from) if cloned_from else None,
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LAYOUT_VERSION_OPENED,
        aggregate_type="layout_version",
        aggregate_id=version.id,
        payload={
            "organization_id": str(org_id),
            "layout_version_id": str(version.id),
            "warehouse_floor_id": str(floor.id),
            "version_number": number,
        },
        actor=actor_for(ctx),
    )
    return version


async def discard_draft_version(
    session: AsyncSession,
    ctx: ScopeContext,
    version: LayoutVersionModel,
    *,
    request: Request | None = None,
) -> None:
    """Abandon a draft outright — never archived (`app.models.LAYOUT_VERSION_STATUSES`'s own
    docstring: "archived" always means "this was published once"). Soft-deleted like every other
    row in this service, so its geometry rows are simply orphaned under a version nothing lists
    or loads again, rather than a cascading hard delete this codebase otherwise never does."""
    if version.status != "draft":
        raise ConflictProblem("Only a draft layout version can be discarded")
    org_id = ctx.organization_id
    version.deleted_at = datetime.now(UTC)
    version.updated_by = str(ctx.principal.sub)
    version.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="layout_version.discarded",
        entity="layout_version",
        entity_id=version.id,
        before={"version_number": version.version_number, "status": version.status},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LAYOUT_VERSION_DISCARDED,
        aggregate_type="layout_version",
        aggregate_id=version.id,
        payload={"organization_id": str(org_id), "layout_version_id": str(version.id)},
        actor=actor_for(ctx),
    )


async def save_draft_geometry(
    session: AsyncSession,
    ctx: ScopeContext,
    floor: WarehouseFloor,
    version: LayoutVersionModel,
    payload: LayoutGeometryIn,
    *,
    request: Request | None = None,
) -> LayoutVersionModel:
    """Replace `version`'s whole geometry with exactly what `payload` describes (see
    `LayoutGeometryIn`'s own docstring). Every referenced location must already exist in this
    floor's own warehouse. Does **not** touch `Location.floor_id` — a location's floor is set
    only when a layout naming it is actually published (`publish_layout_version`), the same
    "draft coordinates never leak" discipline this whole module is built around, applied to this
    one column too."""
    if version.status != "draft":
        raise ConflictProblem("Only a draft layout version can be edited")
    org_id = ctx.organization_id

    location_ids = {p.location_id for p in payload.placements}
    location_ids |= {n.location_id for n in payload.walk_nodes if n.location_id is not None}
    locations = await selectors.locations_by_id(session, org_id, location_ids)
    missing = location_ids - locations.keys()
    if missing:
        raise NotFoundProblem(
            "Unknown location id(s): " + ", ".join(sorted(str(m) for m in missing))
        )
    for location in locations.values():
        if location.warehouse_id != floor.warehouse_id:
            raise ValidationProblem(
                f"Location {location.code} does not belong to this floor's warehouse"
            )

    # A full replace (`LayoutGeometryIn`'s own docstring): drop everything this draft had, then
    # rebuild exactly what was sent. Edges first (they reference nodes), then nodes, then
    # placements — the FK `ondelete="CASCADE"` from each to `layout_versions` would clean these up
    # too if the version itself were deleted, but here the version survives and only its children
    # are replaced, so each table is cleared explicitly and in dependency order.
    await session.execute(
        delete(WalkEdge).where(
            WalkEdge.organization_id == org_id, WalkEdge.layout_version_id == version.id
        )
    )
    await session.execute(
        delete(WalkNode).where(
            WalkNode.organization_id == org_id, WalkNode.layout_version_id == version.id
        )
    )
    await session.execute(
        delete(LocationPlacement).where(
            LocationPlacement.organization_id == org_id,
            LocationPlacement.layout_version_id == version.id,
        )
    )
    await session.flush()

    for p in payload.placements:
        session.add(
            LocationPlacement(
                organization_id=org_id,
                layout_version_id=version.id,
                location_id=p.location_id,
                x_mm=p.x_mm,
                y_mm=p.y_mm,
                z_mm=p.z_mm,
                width_mm=p.width_mm,
                depth_mm=p.depth_mm,
                height_mm=p.height_mm,
            )
        )

    nodes_by_code: dict[str, WalkNode] = {}
    for n in payload.walk_nodes:
        node = WalkNode(
            organization_id=org_id,
            layout_version_id=version.id,
            code=n.code,
            location_id=n.location_id,
            x_mm=n.x_mm,
            y_mm=n.y_mm,
            z_mm=n.z_mm,
        )
        session.add(node)
        nodes_by_code[n.code] = node
    await session.flush()  # assigns each node's id — `LayoutGeometryIn` already proved every
    # `walk_edges[].from_code`/`to_code` names a code present in `walk_nodes`, so the lookups
    # below cannot raise `KeyError`.

    for e in payload.walk_edges:
        session.add(
            WalkEdge(
                organization_id=org_id,
                layout_version_id=version.id,
                from_node_id=nodes_by_code[e.from_code].id,
                to_node_id=nodes_by_code[e.to_code].id,
                distance_mm=e.distance_mm,
                is_traversable=e.is_traversable,
                bidirectional=e.bidirectional,
            )
        )

    version.updated_by = str(ctx.principal.sub)
    version.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="layout_version.geometry_saved",
        entity="layout_version",
        entity_id=version.id,
        after={
            "placements": len(payload.placements),
            "walk_nodes": len(payload.walk_nodes),
            "walk_edges": len(payload.walk_edges),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LAYOUT_GEOMETRY_SAVED,
        aggregate_type="layout_version",
        aggregate_id=version.id,
        payload={
            "organization_id": str(org_id),
            "layout_version_id": str(version.id),
            "placements": len(payload.placements),
            "walk_nodes": len(payload.walk_nodes),
            "walk_edges": len(payload.walk_edges),
        },
        actor=actor_for(ctx),
    )
    return version


async def publish_layout_version(
    session: AsyncSession,
    ctx: ScopeContext,
    floor: WarehouseFloor,
    version: LayoutVersionModel,
    *,
    request: Request | None = None,
) -> LayoutVersionModel:
    """The dangerous move (`operations.warehouse.layout.publish`) — makes `version` the floor's
    one current layout. Works on a `draft` (the ordinary publish, re-validated here as the real
    gate — `app.warehouse.selectors.validate_layout`'s own docstring: the `/validate` preview and
    this check are deliberately the same function, so they can never disagree) or on an `archived`
    version (a rollback: republishing something that really was live before). Never on the version
    that is already `published` — nothing changed, so there is nothing to do.

    The floor's previous `published` version, if any and if different from `version` itself,
    becomes `archived` in the same transaction — never edited otherwise, never deleted: this is
    the one write this whole module makes to a version that is not the one the caller named,
    and it is exactly the "no destructive overwrite of a published version" rule's other half.
    """
    if not domain.can_transition_layout_version(version.status, "published"):
        raise ConflictProblem(
            f"Layout version {version.version_number} is {version.status} and cannot be "
            "published directly"
        )
    org_id = ctx.organization_id
    report = await selectors.validate_layout(session, org_id, version)
    if not report.is_valid:
        detail = "; ".join(issue.message for issue in report.issues)
        raise ValidationProblem(f"This layout cannot be published: {detail}")

    # `Location.floor_id` is written here, on publish — never on a draft save
    # (`save_draft_geometry`'s own docstring) — so a location's recorded floor only ever reflects
    # a layout that actually went live, the same "draft never leaks" rule this module applies to
    # coordinates extended to this one identity-ish column.
    placements = await selectors.placements_for_version(session, org_id, version.id)
    locations = await selectors.locations_by_id(
        session, org_id, {p.location_id for p in placements}
    )
    for location in locations.values():
        if location.floor_id != floor.id:
            location.floor_id = floor.id
            location.updated_by = str(ctx.principal.sub)

    was_rollback = version.status == "archived"
    previous_published = await selectors.get_published_version(
        session, org_id, floor.id, for_update=True
    )
    if previous_published is not None and previous_published.id != version.id:
        previous_published.status = "archived"
        previous_published.updated_by = str(ctx.principal.sub)
        previous_published.updated_at = datetime.now(UTC)
        # Flushed *before* `version` is marked published, in its own statement: the partial
        # unique index `uq_layout_versions_org_floor_published` allows at most one `published`
        # row per floor at any instant Postgres checks it, and that check is per-statement, not
        # deferred. Setting both rows' `status` in the same flush leaves the two UPDATEs' actual
        # order up to SQLAlchemy's unit-of-work, which does not promise archiving-before-
        # publishing here — so this old row is archived, and that is durable within the
        # transaction, before the new one is ever marked published.
        await session.flush()

    version.status = "published"
    version.published_at = datetime.now(UTC)
    version.published_by = str(ctx.principal.sub)
    version.updated_by = str(ctx.principal.sub)
    version.updated_at = datetime.now(UTC)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="layout_version.rolled_back" if was_rollback else "layout_version.published",
        entity="layout_version",
        entity_id=version.id,
        before={"status": "archived" if was_rollback else "draft"},
        after={
            "status": "published",
            "version_number": version.version_number,
            "superseded_version_id": (
                str(previous_published.id)
                if previous_published is not None and previous_published.id != version.id
                else None
            ),
        },
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LAYOUT_PUBLISHED,
        aggregate_type="layout_version",
        aggregate_id=version.id,
        payload={
            "organization_id": str(org_id),
            "layout_version_id": str(version.id),
            "warehouse_floor_id": str(floor.id),
            "version_number": version.version_number,
            "rollback": was_rollback,
        },
        actor=actor_for(ctx),
    )
    return version

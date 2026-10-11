"""Writes for inventory items, warehouses and stock.

Same platform shape as `app/services.py`: change the rows, record the audit entry, stage the
event — all in the caller's transaction. Every stock change goes through
`app.inventory.ledger.post_move`; nothing here writes a quantity.

The item-level API predates locations: an item has one `home_location_id` (its warehouse's
default bin), and opening stock, adjustments, counts and PO receipts post to that bucket. That
compatibility layer refuses — rather than guesses — whenever stock has moved somewhere it cannot
see (a count of an item with stock in two places would otherwise overwrite one of them).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import domain
from app.inventory import ledger, quantity
from app.inventory import selectors as inv_selectors
from app.inventory.ledger import MoveLine
from app.inventory.quantity import QTY_SCALE, format_qty, whole_units
from app.inventory.reconcile import ReconcileReport, reconcile_organization
from app.models import (
    STOCK_AGGREGATES,
    AuditLog,
    InventoryBalance,
    InventoryDiscrepancy,
    InventoryItem,
    ItemAttribute,
    ItemCategory,
    ItemVariant,
    Location,
    OutboxEvent,
    PurchaseOrder,
    Uom,
    Warehouse,
)
from app.receiving import domain as receiving_domain
from app.schemas import (
    InventoryItemPatch,
    ItemAttributeIn,
    ItemCategoryPatch,
    ItemVariantPatch,
    LocationPatch,
    WarehousePatch,
)
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import (
    ConflictProblem,
    ForbiddenProblem,
    NotFoundProblem,
    ValidationProblem,
)
from platform_core.events import publish_event

EVT_WAREHOUSE_CREATED = "operations.inventory.warehouse.created"
EVT_WAREHOUSE_UPDATED = "operations.inventory.warehouse.updated"
EVT_WAREHOUSE_DELETED = "operations.inventory.warehouse.deleted"
EVT_LOCATION_CREATED = "operations.inventory.location.created"
EVT_LOCATION_UPDATED = "operations.inventory.location.updated"
EVT_LOCATION_DELETED = "operations.inventory.location.deleted"
#: Kind and the capacity columns (ADR-0024 §5's "capacity columns" — max weight, max volume,
#: storage type). Changing any of these on a location that already has ledger history needs
#: `layout.publish`, not plain `location.write`.
LAYOUT_GUARDED_FIELDS = ("kind", "max_weight_grams", "max_volume_milli", "storage_type")
EVT_INVENTORY_CREATED = "operations.inventory.item.created"
EVT_INVENTORY_UPDATED = "operations.inventory.item.updated"
EVT_INVENTORY_DELETED = "operations.inventory.item.deleted"
EVT_ITEM_CATEGORY_CREATED = "operations.inventory.item_category.created"
EVT_ITEM_CATEGORY_UPDATED = "operations.inventory.item_category.updated"
EVT_ITEM_CATEGORY_DELETED = "operations.inventory.item_category.deleted"
EVT_ITEM_ATTRIBUTE_SET = "operations.inventory.item_attribute.set"
EVT_ITEM_ATTRIBUTE_DELETED = "operations.inventory.item_attribute.deleted"
EVT_ITEM_VARIANT_CREATED = "operations.inventory.item_variant.created"
EVT_ITEM_VARIANT_UPDATED = "operations.inventory.item_variant.updated"
EVT_ITEM_VARIANT_DELETED = "operations.inventory.item_variant.deleted"
EVT_STOCK_ADJUSTED = "operations.inventory.stock.adjusted"
EVT_DISCREPANCY_RESOLVED = "operations.inventory.discrepancy.resolved"


def _actor(ctx: ScopeContext) -> dict[str, Any]:
    return {
        "type": "user",
        "id": str(ctx.principal.sub),
        "membership_id": str(ctx.principal.membership_id) if ctx.principal.membership_id else None,
    }


def _duplicate(field: str, message: str) -> ConflictProblem:
    return ConflictProblem(
        message, errors=[{"field": field, "code": "duplicate", "message": message}]
    )


def _check_site_coordinates(lat_micro: int | None, lng_micro: int | None) -> None:
    """A warehouse's coordinates, as they will be stored: both set or both null, each in range. The
    HTTP schema checks a create body; a PATCH is checked here against the *merged* state, and so is
    any caller that does not come through the schema."""
    if errors := domain.site_coordinate_errors(lat_micro, lng_micro):
        raise ValidationProblem(
            errors[0][1],
            errors=[{"field": f, "code": "invalid_coordinates", "message": m} for f, m in errors],
        )


async def _home_for(session: AsyncSession, org_id: uuid.UUID, warehouse_name: str) -> Location:
    warehouse = await inv_selectors.get_warehouse_by_name(session, org_id, warehouse_name)
    if warehouse is None:
        raise ValidationProblem(f"{warehouse_name!r} does not match an existing warehouse")
    home = await inv_selectors.default_location(session, org_id, warehouse.id)
    if home is None:  # every warehouse gets one at creation; a missing one is a data bug
        raise ConflictProblem(f"{warehouse_name} has no default bin to hold stock")
    return home


async def _resolve_location_parent(
    session: AsyncSession, org_id: uuid.UUID, warehouse_id: uuid.UUID, value: str | None
) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        parent_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationProblem("parent_id must be a UUID") from exc
    parent = await inv_selectors.get_location(session, org_id, parent_id)
    if parent is None or parent.warehouse_id != warehouse_id:
        raise ValidationProblem("parent_id does not match an existing location in this warehouse")
    return parent_id


def _tolerance_bp(pct: str) -> int:
    """A wire tolerance percentage -> integer basis points, a 422 if it is not 0-100 with at most
    two decimals."""
    try:
        return receiving_domain.parse_tolerance_pct(pct)
    except ValueError as exc:
        raise ValidationProblem(
            str(exc),
            errors=[
                {"field": "receipt_tolerance_pct", "code": "invalid_tolerance", "message": str(exc)}
            ],
        ) from exc


async def _flush_item(session: AsyncSession, item: InventoryItem) -> None:
    """Flush a changed item and re-read its stock aggregates. A flush that UPDATEs the row
    expires SQL-expression column properties (`on_hand_milli`), and under asyncio an expired
    attribute cannot lazy-load — so every writer that touches the row reloads them here, once."""
    await session.flush()
    await session.refresh(item, list(STOCK_AGGREGATES))


def _home(item: InventoryItem) -> uuid.UUID:
    if item.home_location_id is None:
        raise ConflictProblem(f"{item.sku} has no warehouse to hold stock; set one first")
    return item.home_location_id


# ------------------------------------------------------------------ units of measure


async def ensure_seed_uoms(session: AsyncSession, org_id: uuid.UUID, actor: str) -> None:
    """Make sure the organization's UOM directory holds every fixed-registry unit
    (ADR-0014 Phase 1, plan §2). Idempotent — `ON CONFLICT DO NOTHING` against the same partial
    unique index `resolve_or_create_uom` relies on — so calling it on every read of the directory
    costs one no-op statement once it already exists, and a fresh organization's very first read
    still sees the full registry rather than only the units its items happen to use."""
    rows = [
        {
            "id": uuid.uuid4(),
            "organization_id": org_id,
            "code": code,
            "name": quantity.UNIT_NAMES[code],
            "dimension": u.dimension,
            "is_discrete": u.discrete,
            "is_system": True,
            "created_by": actor,
        }
        for code, u in quantity.UNITS.items()
    ]
    await session.execute(
        pg_insert(Uom)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["organization_id", "code"], index_where=Uom.deleted_at.is_(None)
        )
    )


async def resolve_or_create_uom(
    session: AsyncSession, org_id: uuid.UUID, code: str, actor: str
) -> Uom:
    """Resolve a wire `uom` code to its `uoms` row, creating one the first time this organization
    names it. A code the fixed registry knows becomes (or already is) a real `is_system` row with
    the registry's own dimension and discreteness; anything else becomes a custom, continuous
    one, keyed case-insensitively so `"Meter"`/`"meter"` do not mint two rows for the same unit.
    No unit a caller names is ever refused for being unknown — only `create_inventory_item` /
    `update_inventory_item` decide whether a blank one is acceptable.
    """
    stripped = code.strip()
    if not stripped:
        raise ValidationProblem("A unit of measure cannot be blank")
    known = quantity.unit(stripped)
    canonical = quantity.canonical_uom_code(stripped)
    existing = await inv_selectors.get_uom_by_code(session, org_id, canonical)
    if existing is not None:
        return existing
    # Two requests naming the same brand-new unit for the first time race on the unique
    # (organization_id, code) index; `ON CONFLICT DO NOTHING` + a reselect makes the loser return
    # the winner's row instead of raising an `IntegrityError` the caller never asked to handle.
    inserted = (
        await session.execute(
            pg_insert(Uom)
            .values(
                organization_id=org_id,
                created_by=actor,
                code=canonical,
                name=quantity.UNIT_NAMES[canonical] if known else stripped,
                dimension=known.dimension if known else "other",
                is_discrete=known.discrete if known else False,
                is_system=known is not None,
            )
            .on_conflict_do_nothing(
                index_elements=["organization_id", "code"], index_where=Uom.deleted_at.is_(None)
            )
            .returning(Uom)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return inserted
    winner = await inv_selectors.get_uom_by_code(session, org_id, canonical)
    if winner is None:  # pragma: no cover - the conflict target guarantees a row exists
        raise ConflictProblem(f"{canonical!r} could not be resolved to a unit of measure")
    return winner


# ------------------------------------------------------------------ warehouses


async def create_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    location: str = "",
    lat_micro: int | None = None,
    lng_micro: int | None = None,
    request: Request | None = None,
) -> Warehouse:
    """Create a warehouse (stocking location) and its default bin. `name` is a unique join key too
    — it is what `inventory_items.warehouse` and `purchase_orders.warehouse` carry — so a
    duplicate name is refused exactly like a duplicate code. `lat_micro`/`lng_micro` place the
    site on a map (micro-degrees, both or neither)."""
    _check_site_coordinates(lat_micro, lng_micro)
    if await inv_selectors.get_warehouse_by_code(session, ctx.organization_id, code):
        raise _duplicate("code", f"A warehouse coded {code!r} already exists")
    if await inv_selectors.get_warehouse_by_name(session, ctx.organization_id, name):
        raise _duplicate("name", f"A warehouse named {name!r} already exists")

    warehouse = Warehouse(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        location=location,
        lat_micro=lat_micro,
        lng_micro=lng_micro,
    )
    session.add(warehouse)
    await session.flush()
    session.add(
        Location(
            organization_id=ctx.organization_id,
            created_by=str(ctx.principal.sub),
            warehouse_id=warehouse.id,
            kind="bin",
            code="DEFAULT",
            name="Default bin",
            is_default=True,
        )
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="warehouse.created",
        entity="warehouse",
        entity_id=warehouse.id,
        after={"code": code, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WAREHOUSE_CREATED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "warehouse_id": str(warehouse.id),
            "code": code,
        },
        actor=_actor(ctx),
    )
    return warehouse


async def update_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    payload: WarehousePatch,
    *,
    request: Request | None = None,
) -> Warehouse:
    """Patch `name`/`location`/the site's coordinates. `code` is immutable — `WarehousePatch`
    carries no such field. A rename cascades to the denormalised `warehouse` string on every live
    purchase order and inventory item in the same transaction, or every existing row keeps the
    stale name. Stock is unaffected: it sits at locations, which a rename does not touch.

    Coordinates are validated against the **merged** state: a patch that sets only the latitude is
    checked against the longitude already stored, so a lone half can never be persisted; an
    explicit `null` for both clears the site's position."""
    changes = payload.model_dump(exclude_unset=True)
    if "lat_micro" in changes or "lng_micro" in changes:
        _check_site_coordinates(
            changes.get("lat_micro", warehouse.lat_micro),
            changes.get("lng_micro", warehouse.lng_micro),
        )
    if "name" in changes and changes["name"] != warehouse.name:
        existing = await inv_selectors.get_warehouse_by_name(
            session, ctx.organization_id, changes["name"]
        )
        if existing is not None and existing.id != warehouse.id:
            raise _duplicate("name", f"A warehouse named {changes['name']!r} already exists")

    old_name = warehouse.name
    before = {field: getattr(warehouse, field) for field in changes}
    for field, value in changes.items():
        setattr(warehouse, field, value)
    warehouse.updated_by = str(ctx.principal.sub)
    await session.flush()

    if "name" in changes and changes["name"] != old_name:
        new_name = changes["name"]
        await session.execute(
            update(PurchaseOrder)
            .where(
                PurchaseOrder.organization_id == ctx.organization_id,
                PurchaseOrder.warehouse == old_name,
            )
            .values(warehouse=new_name, updated_by=str(ctx.principal.sub))
        )
        await session.execute(
            update(InventoryItem)
            .where(
                InventoryItem.organization_id == ctx.organization_id,
                InventoryItem.warehouse == old_name,
            )
            .values(warehouse=new_name, updated_by=str(ctx.principal.sub))
        )
        await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="warehouse.updated",
        entity="warehouse",
        entity_id=warehouse.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_WAREHOUSE_UPDATED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "warehouse_id": str(warehouse.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return warehouse


async def delete_warehouse(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a warehouse and its locations. Refused (409) while it still holds any live
    inventory item, any stock or reservation at any of its locations, or names a
    `draft`/`approved` purchase order's destination."""
    org_id = ctx.organization_id
    item_count = await inv_selectors.count_inventory_for_warehouse(session, org_id, warehouse.name)
    po_count = await inv_selectors.count_open_purchase_orders_for_warehouse(
        session, org_id, warehouse.name
    )
    if item_count or po_count:
        raise ConflictProblem(
            f"{warehouse.name} still holds {item_count} inventory item(s) and {po_count} open "
            "purchase order(s); move or clear them first"
        )
    if await inv_selectors.stock_at_warehouse(session, org_id, warehouse.id):
        raise ConflictProblem(f"{warehouse.name} still holds stock; move or clear it first")

    before_deleted_at = warehouse.deleted_at
    now = datetime.now(UTC)
    warehouse.deleted_at = now
    warehouse.updated_by = str(ctx.principal.sub)
    await session.execute(
        update(Location)
        .where(
            Location.organization_id == org_id,
            Location.warehouse_id == warehouse.id,
            Location.deleted_at.is_(None),
        )
        .values(deleted_at=now, updated_by=str(ctx.principal.sub))
    )
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="warehouse.deleted",
        entity="warehouse",
        entity_id=warehouse.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": warehouse.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_WAREHOUSE_DELETED,
        aggregate_type="warehouse",
        aggregate_id=warehouse.id,
        payload={"organization_id": str(org_id), "warehouse_id": str(warehouse.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ locations (ADR-0024 §5)


async def create_location(
    session: AsyncSession,
    ctx: ScopeContext,
    warehouse: Warehouse,
    *,
    kind: str,
    code: str,
    name: str,
    parent_id: str | None = None,
    barcode: str | None = None,
    pick_sequence: int | None = None,
    max_weight_grams: int | None = None,
    max_volume_milli: int | None = None,
    storage_type: str | None = None,
    request: Request | None = None,
) -> Location:
    """Add a location to a warehouse's tree. `code` is unique within the warehouse (like
    `uq_locations_org_warehouse_code`); `barcode`, when given, is unique across every live
    location in the organization."""
    org_id = ctx.organization_id
    if await inv_selectors.get_location_by_code(session, org_id, warehouse.id, code):
        raise _duplicate("code", f"A location coded {code!r} already exists in this warehouse")
    resolved_parent = await _resolve_location_parent(session, org_id, warehouse.id, parent_id)
    if barcode is not None:
        existing = await inv_selectors.get_location_by_barcode(session, org_id, barcode)
        if existing is not None:
            raise _duplicate("barcode", f"Barcode {barcode!r} is already in use")

    location = Location(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        warehouse_id=warehouse.id,
        parent_id=resolved_parent,
        kind=kind,
        code=code,
        name=name,
        barcode=barcode,
        pick_sequence=pick_sequence,
        max_weight_grams=max_weight_grams,
        max_volume_milli=max_volume_milli,
        storage_type=storage_type,
    )
    session.add(location)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="location.created",
        entity="location",
        entity_id=location.id,
        after={"warehouse_id": str(warehouse.id), "kind": kind, "code": code, "name": name},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LOCATION_CREATED,
        aggregate_type="location",
        aggregate_id=location.id,
        payload={
            "organization_id": str(org_id),
            "location_id": str(location.id),
            "warehouse_id": str(warehouse.id),
            "kind": kind,
        },
        actor=_actor(ctx),
    )
    return location


async def update_location(
    session: AsyncSession,
    ctx: ScopeContext,
    location: Location,
    payload: LocationPatch,
    *,
    request: Request | None = None,
) -> Location:
    """Patch a location. `code`, `name`, `barcode` and `pick_sequence` are a plain
    `location.write`; changing `kind` or a capacity column (`max_weight_grams`,
    `max_volume_milli`, `storage_type`) on a location that already has ledger history is a
    layout decision, not a labelling one, and needs `layout.publish` instead (ADR-0024 §5) —
    checked here, against this location's own history, since the same field change is harmless
    on a location nothing has ever stocked."""
    org_id = ctx.organization_id
    changes = payload.model_dump(exclude_unset=True)

    guarded = {
        field
        for field in LAYOUT_GUARDED_FIELDS
        if field in changes and changes[field] != getattr(location, field)
    }
    # Short-circuits on the permission first: a caller who already holds `layout.publish` never
    # pays for the history lookup, and one who does not is refused without it ever mattering
    # whether this particular location has history yet.
    needs_publish = guarded and not ctx.principal.has("operations.inventory.layout.publish")
    if needs_publish and await inv_selectors.location_has_ledger_history(
        session, org_id, location.id
    ):
        raise ForbiddenProblem(
            "This location already has stock history; changing "
            f"{', '.join(sorted(guarded))} needs operations.inventory.layout.publish, "
            "not just location.write"
        )

    if "code" in changes and changes["code"] != location.code:
        existing = await inv_selectors.get_location_by_code(
            session, org_id, location.warehouse_id, changes["code"]
        )
        if existing is not None and existing.id != location.id:
            raise _duplicate(
                "code", f"A location coded {changes['code']!r} already exists in this warehouse"
            )
    if (
        "barcode" in changes
        and changes["barcode"] is not None
        and changes["barcode"] != location.barcode
    ):
        existing = await inv_selectors.get_location_by_barcode(session, org_id, changes["barcode"])
        if existing is not None and existing.id != location.id:
            raise _duplicate("barcode", f"Barcode {changes['barcode']!r} is already in use")

    before = {field: getattr(location, field) for field in changes}
    for field, value in changes.items():
        setattr(location, field, value)
    location.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="location.updated",
        entity="location",
        entity_id=location.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LOCATION_UPDATED,
        aggregate_type="location",
        aggregate_id=location.id,
        payload={
            "organization_id": str(org_id),
            "location_id": str(location.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return location


async def delete_location(
    session: AsyncSession,
    ctx: ScopeContext,
    location_id: uuid.UUID,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a location. Refused (409) while it is its warehouse's default bin, still has
    child locations, is an item's home bin, or holds — or has *ever* held — stock: a location
    with ledger history is part of the record of where stock was, and deleting it would leave
    history naming a place that no longer exists. Such a location can be renamed or left unused.

    The location row is taken `FOR UPDATE` before the history check, and `post_move` takes the
    rows it posts into `FOR SHARE`, so a deletion and a concurrent first post into the same
    location serialise: the deletion sees the stock, or the post finds the location gone.
    """
    org_id = ctx.organization_id
    location = (
        await session.execute(
            select(Location)
            .where(
                Location.organization_id == org_id,
                Location.id == location_id,
                Location.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if location is None:
        raise NotFoundProblem("That location is not part of this organization")
    if location.is_default:
        raise ConflictProblem(
            f"{location.code} is its warehouse's default bin, which holds stock for items that "
            "have no other place; it cannot be deleted"
        )
    children = (
        await session.execute(
            select(func.count())
            .select_from(Location)
            .where(
                Location.organization_id == org_id,
                Location.parent_id == location.id,
                Location.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    if children:
        raise ConflictProblem(
            f"{location.code} still has {children} location(s) inside it; delete or move them first"
        )
    homed = (
        await session.execute(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.organization_id == org_id,
                InventoryItem.home_location_id == location.id,
                InventoryItem.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    if homed:
        raise ConflictProblem(
            f"{location.code} is the home bin of {homed} inventory item(s); give them another "
            "warehouse first"
        )
    if await inv_selectors.location_has_ledger_history(session, org_id, location.id):
        raise ConflictProblem(
            f"{location.code} holds or has held stock, so its history is kept; "
            "a location with ledger history cannot be deleted"
        )

    location.deleted_at = datetime.now(UTC)
    location.updated_by = str(ctx.principal.sub)
    await session.flush()
    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="location.deleted",
        entity="location",
        entity_id=location.id,
        before={"code": location.code, "warehouse_id": str(location.warehouse_id)},
        after={"deleted_at": location.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_LOCATION_DELETED,
        aggregate_type="location",
        aggregate_id=location.id,
        payload={
            "organization_id": str(org_id),
            "location_id": str(location.id),
            "warehouse_id": str(location.warehouse_id),
        },
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ item categories


async def _lock_category_tree(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Serialise every writer of this organization's category tree for the rest of the
    transaction (H2). There is no single row that names "the tree", so `SELECT … FOR UPDATE` has
    nothing to lock: two concurrent re-parents, or a re-parent racing a create-with-a-parent or a
    delete, can each read a cycle-free snapshot and still land on a cycle or a dangling reference
    once both commit. `pg_advisory_xact_lock` is released automatically at commit or rollback, so
    a bug here cannot leak a lock across requests the way a session-level advisory lock could.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"item_categories:{org_id}"},
    )


async def _reread_category_locked(
    session: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID
) -> ItemCategory | None:
    """Re-read a category under `_lock_category_tree`'s lock (LOW #6). The route loads `category`
    *before* taking the lock, so a concurrent delete or re-parent that reached the lock first is
    invisible to that copy; `populate_existing` forces a fresh read instead of the stale
    identity-mapped one, and `deleted_at IS NULL` means a category a concurrent request just
    deleted comes back `None` here rather than as a row that looks alive. Without this, two
    concurrent deletes of the same category each pass their own pre-lock "does it still have
    children?" check and both write a soft-delete, an audit row and an event for the same delete."""
    return (
        await session.execute(
            select(ItemCategory)
            .where(
                ItemCategory.organization_id == org_id,
                ItemCategory.id == category_id,
                ItemCategory.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _lock_sku(session: AsyncSession, org_id: uuid.UUID, sku: str) -> None:
    """Serialise every writer of this exact SKU spelling for the rest of the transaction (LOW
    #4). An item's SKU and a variant's SKU share one namespace but live in two different tables,
    each checked with its own `SELECT` — two concurrent creates, one an item and one a variant,
    naming the same SKU, can each see the other's table as clear and both commit. Locked
    case-insensitively, the same way the uniqueness itself is judged."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"sku:{org_id}:{sku.lower()}"},
    )


async def _would_cycle(
    session: AsyncSession, org_id: uuid.UUID, category_id: uuid.UUID, new_parent_id: uuid.UUID
) -> bool:
    """True if giving `category_id` the parent `new_parent_id` would make the tree circular —
    walks the proposed parent's own ancestor chain looking for `category_id`."""
    seen: set[uuid.UUID] = set()
    current: uuid.UUID | None = new_parent_id
    while current is not None:
        if current == category_id or current in seen:
            return True
        seen.add(current)
        parent = await inv_selectors.get_item_category(session, org_id, current)
        current = parent.parent_id if parent else None
    return False


async def _resolve_category_parent(
    session: AsyncSession, org_id: uuid.UUID, value: str | None, *, own_id: uuid.UUID | None = None
) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        parent_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationProblem("parent_id must be a UUID") from exc
    if own_id is not None and parent_id == own_id:
        raise ValidationProblem("A category cannot be its own parent")
    if await inv_selectors.get_item_category(session, org_id, parent_id) is None:
        raise ValidationProblem("parent_id does not match an existing category")
    if own_id is not None and await _would_cycle(session, org_id, own_id, parent_id):
        raise ValidationProblem("That would make the category tree circular")
    return parent_id


async def create_item_category(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    code: str,
    name: str,
    parent_id: str | None = None,
    request: Request | None = None,
) -> ItemCategory:
    await _lock_category_tree(session, ctx.organization_id)
    if await inv_selectors.get_item_category_by_code(session, ctx.organization_id, code):
        raise _duplicate("code", f"A category coded {code!r} already exists")
    resolved_parent = await _resolve_category_parent(session, ctx.organization_id, parent_id)

    category = ItemCategory(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        code=code,
        name=name,
        parent_id=resolved_parent,
    )
    session.add(category)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="item_category.created",
        entity="item_category",
        entity_id=category.id,
        after={"code": code, "name": name, "parent_id": parent_id},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ITEM_CATEGORY_CREATED,
        aggregate_type="item_category",
        aggregate_id=category.id,
        payload={"organization_id": str(ctx.organization_id), "item_category_id": str(category.id)},
        actor=_actor(ctx),
    )
    return category


async def update_item_category(
    session: AsyncSession,
    ctx: ScopeContext,
    category: ItemCategory,
    payload: ItemCategoryPatch,
    *,
    request: Request | None = None,
) -> ItemCategory:
    changes = payload.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        # Only a re-parent needs the tree-wide lock — a plain rename cannot create a cycle.
        await _lock_category_tree(session, ctx.organization_id)
        current = await _reread_category_locked(session, ctx.organization_id, category.id)
        if current is None:
            raise NotFoundProblem("That category is not part of this organization")
        category = current
        changes["parent_id"] = await _resolve_category_parent(
            session, ctx.organization_id, changes["parent_id"], own_id=category.id
        )

    before = {field: getattr(category, field) for field in changes}
    for field, value in changes.items():
        setattr(category, field, value)
    category.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="item_category.updated",
        entity="item_category",
        entity_id=category.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ITEM_CATEGORY_UPDATED,
        aggregate_type="item_category",
        aggregate_id=category.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "item_category_id": str(category.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return category


async def delete_item_category(
    session: AsyncSession,
    ctx: ScopeContext,
    category: ItemCategory,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete a category. Refused (409) while it still has sub-categories or classifies any
    live item — move or clear them first."""
    org_id = ctx.organization_id
    await _lock_category_tree(session, org_id)
    current = await _reread_category_locked(session, org_id, category.id)
    if current is None:
        # Already deleted by a concurrent request that reached the lock first (LOW #6) — deleting
        # a category is idempotent, so this is a no-op rather than a second audit row and event.
        return
    category = current
    children = await inv_selectors.count_item_category_children(session, org_id, category.id)
    items = await inv_selectors.count_items_in_category(session, org_id, category.id)
    if children or items:
        raise ConflictProblem(
            f"{category.name} still has {children} sub-categor{'y' if children == 1 else 'ies'} "
            f"and {items} item(s) classified under it; move or clear them first"
        )

    before_deleted_at = category.deleted_at
    category.deleted_at = datetime.now(UTC)
    category.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="item_category.deleted",
        entity="item_category",
        entity_id=category.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": category.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_ITEM_CATEGORY_DELETED,
        aggregate_type="item_category",
        aggregate_id=category.id,
        payload={"organization_id": str(org_id), "item_category_id": str(category.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ inventory items


async def _resolve_item_category(
    session: AsyncSession, org_id: uuid.UUID, value: str | None
) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        category_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationProblem("item_category_id must be a UUID") from exc
    # Same lock `delete_item_category` takes (H2): without it, an item can be assigned to a
    # category between that delete's "any items still classified here?" check and its commit,
    # leaving the item pointing at a category that is (or is about to be) gone.
    await _lock_category_tree(session, org_id)
    if await inv_selectors.get_item_category(session, org_id, category_id) is None:
        raise ValidationProblem("item_category_id does not match an existing category")
    return category_id


async def _resolve_secondary_uom(
    session: AsyncSession, org_id: uuid.UUID, value: str | None
) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        uom_id = uuid.UUID(value)
    except ValueError as exc:
        raise ValidationProblem("secondary_uom_id must be a UUID") from exc
    if await inv_selectors.get_uom(session, org_id, uom_id) is None:
        raise ValidationProblem("secondary_uom_id does not match an existing unit")
    return uom_id


async def create_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    *,
    sku: str,
    name: str,
    category: str,
    warehouse: str = "",
    on_hand: int = 0,
    reorder_point: int = 0,
    uom: str = "",
    unit_value_minor: int = 0,
    currency: str = "INR",
    stocked_at: date | None = None,
    item_category_id: str | None = None,
    secondary_uom_id: str | None = None,
    hsn_code: str = "",
    track_lot: bool = False,
    track_roll: bool = False,
    track_expiry: bool = False,
    track_quality: bool = False,
    is_active: bool = True,
    receipt_tolerance_pct: str = "0",
    selling_price_minor: int | None = None,
    selling_price_currency: str | None = None,
    barcode: str | None = None,
    request: Request | None = None,
) -> InventoryItem:
    """Create an inventory item at its warehouse's default bin. `unit_value_minor` is per-unit
    stock value in integer minor units of `currency`; low-stock is derived, never stored.
    `stocked_at` defaults to today (`InventoryItemOut.updated_at` is a required field).

    `selling_price_minor`/`selling_price_currency` (ADR-0024 §3) are what a customer order line
    prices from — distinct from `unit_value_minor`, which is cost — both null or both set.

    `on_hand` — the opening quantity, in whole units — is never written anywhere directly: a
    non-zero one becomes an `opening` move through the ledger, so the item's stock is the sum of
    its ledger from the moment the row exists.

    `item_category_id` and `secondary_uom_id`, when given, must each name an existing row in this
    organization (ADR-0014 Phase 1) — validated here, not left to the foreign key to refuse with
    a bare `IntegrityError`.
    """
    await _lock_sku(session, ctx.organization_id, sku)
    if await inv_selectors.get_inventory_by_sku(session, ctx.organization_id, sku):
        raise ConflictProblem(f"An inventory item with SKU {sku!r} already exists")
    if await inv_selectors.get_item_variant_by_sku(session, ctx.organization_id, sku):
        raise ConflictProblem(f"A variant with SKU {sku!r} already exists")
    if barcode is not None and await inv_selectors.item_barcode_taken(
        session, ctx.organization_id, barcode
    ):
        raise _duplicate("barcode", f"Barcode {barcode!r} is already in use")
    home = await _home_for(session, ctx.organization_id, warehouse) if warehouse else None
    resolved_uom = (
        await resolve_or_create_uom(session, ctx.organization_id, uom, str(ctx.principal.sub))
        if uom
        else None
    )
    resolved_category = await _resolve_item_category(session, ctx.organization_id, item_category_id)
    resolved_secondary_uom = await _resolve_secondary_uom(
        session, ctx.organization_id, secondary_uom_id
    )

    opening_date = stocked_at or date.today()
    item = InventoryItem(
        organization_id=ctx.organization_id,
        created_by=str(ctx.principal.sub),
        sku=sku,
        name=name,
        category=category,
        warehouse=warehouse,
        home_location_id=home.id if home else None,
        reorder_point=reorder_point,
        uom=uom,
        uom_id=resolved_uom.id if resolved_uom else None,
        unit_value_minor=unit_value_minor,
        currency=currency.upper(),
        stocked_at=opening_date,
        item_category_id=resolved_category,
        secondary_uom_id=resolved_secondary_uom,
        hsn_code=hsn_code,
        track_lot=track_lot,
        track_roll=track_roll,
        track_expiry=track_expiry,
        track_quality=track_quality,
        is_active=is_active,
        receipt_tolerance_bp=_tolerance_bp(receipt_tolerance_pct),
        barcode=barcode,
        selling_price_minor=selling_price_minor,
        selling_price_currency=(
            selling_price_currency.upper() if selling_price_currency else selling_price_currency
        ),
    )
    session.add(item)
    await _flush_item(session, item)

    if on_hand:
        await ledger.post_move(
            session,
            ctx,
            kind="opening",
            lines=[
                MoveLine(
                    item_id=item.id,
                    location_id=_home(item),
                    quantity_milli=on_hand * QTY_SCALE,
                    unit_cost_minor=unit_value_minor,
                    unit_cost_currency=item.currency,
                )
            ],
            occurred_on=opening_date,
            request=request,
        )

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.created",
        entity="inventory_item",
        entity_id=item.id,
        after={"sku": sku, "warehouse": warehouse, "on_hand": item.on_hand},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_CREATED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "sku": sku,
        },
        actor=_actor(ctx),
    )
    return item


async def update_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    payload: InventoryItemPatch,
    *,
    request: Request | None = None,
) -> InventoryItem:
    """Patch the item's card. `sku`, `currency` and `on_hand` never appear on
    `InventoryItemPatch` — `sku` is the join key a PO line matches on, `currency` is the unit
    the stock value accumulated in, and stock only ever moves through the ledger.

    Changing `warehouse` moves the item's home. Stock in the old home bucket moves with it, as a
    `relocation` move (out of the old bin, into the new) — before ADR-0014 the stock was just a
    number with a warehouse label, and relabelling it moved it. `item` must be locked by the
    caller. Refused while any of that stock is reserved: a reservation is a promise about stock
    at a place.
    """
    changes = payload.model_dump(exclude_unset=True)
    if "receipt_tolerance_pct" in changes:
        # Stored as integer basis points; the wire carries the percentage string.
        changes["receipt_tolerance_bp"] = _tolerance_bp(changes.pop("receipt_tolerance_pct"))
    new_home: Location | None = None
    if "warehouse" in changes and changes["warehouse"] != item.warehouse:
        new_home = await _home_for(session, ctx.organization_id, changes["warehouse"])
    elif "warehouse" in changes:
        await _home_for(session, ctx.organization_id, changes["warehouse"])
    new_uom = None
    uom_actually_changes = "uom" in changes and quantity.canonical_uom_code(
        changes["uom"]
    ) != quantity.canonical_uom_code(item.uom)
    if uom_actually_changes:
        # Compared canonically (LOW #3), the same way `resolve_or_create_uom` would resolve each
        # string — "PCS", "pc" and "piece" on an item already stocked in "pcs" are the same unit,
        # not a change, so they neither trip the M5 guard below nor mint a redundant custom row.
        # The unit is the scale every existing milli-quantity for this item was recorded in
        # (M5) — changing it once any balance or ledger row exists would silently reinterpret
        # that history in a different unit, not just "how much is on hand right now".
        if await inv_selectors.has_stock_history(session, ctx.organization_id, item.id):
            raise ConflictProblem(
                f"{item.sku} already has stock history in {item.uom!r}; its unit of measure "
                "cannot change once any stock has moved"
            )
        new_uom = await resolve_or_create_uom(
            session, ctx.organization_id, changes["uom"], str(ctx.principal.sub)
        )
    # Resolved ahead of the generic loop so a bad id is refused before anything is written.
    if "item_category_id" in changes:
        changes["item_category_id"] = await _resolve_item_category(
            session, ctx.organization_id, changes["item_category_id"]
        )
    if "secondary_uom_id" in changes:
        changes["secondary_uom_id"] = await _resolve_secondary_uom(
            session, ctx.organization_id, changes["secondary_uom_id"]
        )
    if changes.get("selling_price_currency"):
        changes["selling_price_currency"] = changes["selling_price_currency"].upper()
    # `exclude_unset` already dropped an omitted barcode, so an explicit null here clears it.
    if (
        changes.get("barcode") is not None
        and changes["barcode"] != item.barcode
        and await inv_selectors.item_barcode_taken(
            session, ctx.organization_id, changes["barcode"], exclude=item.id
        )
    ):
        raise _duplicate("barcode", f"Barcode {changes['barcode']!r} is already in use")

    before = {field: getattr(item, field) for field in changes}
    old_home = item.home_location_id
    for field, value in changes.items():
        setattr(item, field, value)
    if new_uom is not None:
        item.uom_id = new_uom.id
    if new_home is not None and new_home.id != old_home:
        item.home_location_id = new_home.id
        if old_home is not None:
            await _relocate_home_stock(session, ctx, item, old_home, new_home.id, request)
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await _flush_item(session, item)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.updated",
        entity="inventory_item",
        entity_id=item.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_UPDATED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return item


async def _relocate_home_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    old_home: uuid.UUID,
    new_home: uuid.UUID,
    request: Request | None,
) -> None:
    """Move whatever stock sits in the item's old home bucket into its new one, as a paired
    `relocation` move. Refused while any of it is reserved — a reservation is a promise about
    stock at a place — by `post_move`'s own outbound check (ADR-0014 §11): one refusal, proven
    once in the kernel, reused here rather than copied, so the error names the reservations
    blocking it exactly as a count's would.

    Locks both buckets, in the same order `post_move` would sort its own two lines by (ADR-0014
    §3: item, location, status, lot, roll — location is what varies here), before reading either
    one. Locking the old bucket alone first — regardless of where it falls in that order — is the
    one lock order this module does not otherwise use, and is a deadlock waiting for a concurrent
    move over the same two buckets taken the other way round. `post_move` re-takes the same locks
    a moment later; re-acquiring a row lock this transaction already holds is a no-op.
    """
    org_id = ctx.organization_id
    first_loc, second_loc = sorted((old_home, new_home), key=str)
    locked = {
        first_loc: await ledger.lock_bucket(
            session, org_id, item_id=item.id, location_id=first_loc
        ),
        second_loc: await ledger.lock_bucket(
            session, org_id, item_id=item.id, location_id=second_loc
        ),
    }
    balance = locked[old_home]
    assert balance is not None  # lock_bucket creates the row (at zero) when it does not exist
    if balance.quantity_milli == 0:
        return
    moving = balance.quantity_milli
    await ledger.post_move(
        session,
        ctx,
        kind="relocation",
        lines=[
            MoveLine(item_id=item.id, location_id=old_home, quantity_milli=-moving),
            MoveLine(item_id=item.id, location_id=new_home, quantity_milli=moving),
        ],
        occurred_on=date.today(),
        note=f"Warehouse changed to {item.warehouse}",
        request=request,
    )


async def delete_inventory_item(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    request: Request | None = None,
) -> None:
    """Soft-delete an inventory item. Refused (409) while it holds any stock or reservation
    anywhere — value must never vanish without a move; adjust stock to zero first. Its ledger
    stays, unaffected."""
    held = (
        await session.execute(
            select(
                cast(func.coalesce(func.sum(InventoryBalance.quantity_milli), 0), BigInteger),
                cast(func.coalesce(func.sum(InventoryBalance.reserved_milli), 0), BigInteger),
            ).where(
                InventoryBalance.organization_id == ctx.organization_id,
                InventoryBalance.inventory_item_id == item.id,
            )
        )
    ).one()
    held_milli, reserved_milli = held
    if reserved_milli:
        raise ConflictProblem(
            f"{item.sku} still has {format_qty(reserved_milli)} reserved; release it first"
        )
    if held_milli:
        raise ConflictProblem(
            f"{item.sku} still has {format_qty(held_milli)} on hand; adjust stock to zero first"
        )

    before_deleted_at = item.deleted_at
    item.deleted_at = datetime.now(UTC)
    item.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.deleted",
        entity="inventory_item",
        entity_id=item.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": item.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_INVENTORY_DELETED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={"organization_id": str(ctx.organization_id), "inventory_item_id": str(item.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ item attributes


async def set_item_attribute(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    key: str,
    payload: ItemAttributeIn,
    *,
    request: Request | None = None,
) -> ItemAttribute:
    """Create or overwrite one typed attribute (`PUT` semantics — idempotent by key). Typed
    values only (ADR-0014 Phase 1): `payload` already refused anything but exactly the one
    `value_*` field `value_type` names (`ItemAttributeIn`'s own validator) before this is called,
    so there is nothing here to mass-assign into."""
    org_id = ctx.organization_id
    existing = await inv_selectors.get_item_attribute(session, org_id, item.id, key)
    before = (
        {
            "value_type": existing.value_type,
            "value_text": existing.value_text,
            "value_number": existing.value_number,
            "value_boolean": existing.value_boolean,
            "value_date": existing.value_date,
        }
        if existing is not None
        else None
    )
    after = {
        "value_type": payload.value_type,
        "value_text": payload.value_text,
        "value_number": payload.value_number,
        "value_boolean": payload.value_boolean,
        "value_date": payload.value_date,
    }

    if existing is not None:
        for field, value in after.items():
            setattr(existing, field, value)
        existing.updated_by = str(ctx.principal.sub)
        attribute = existing
    else:
        attribute = ItemAttribute(
            organization_id=org_id,
            created_by=str(ctx.principal.sub),
            inventory_item_id=item.id,
            key=key,
            **after,
        )
        session.add(attribute)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="item_attribute.set",
        entity="item_attribute",
        entity_id=attribute.id,
        before=before,
        after={"key": key, **after},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_ITEM_ATTRIBUTE_SET,
        aggregate_type="item_attribute",
        aggregate_id=attribute.id,
        payload={
            "organization_id": str(org_id),
            "inventory_item_id": str(item.id),
            "key": key,
            **{k: (v.isoformat() if isinstance(v, date) else v) for k, v in after.items()},
        },
        actor=_actor(ctx),
    )
    return attribute


async def delete_item_attribute(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    key: str,
    *,
    request: Request | None = None,
) -> None:
    org_id = ctx.organization_id
    attribute = await inv_selectors.get_item_attribute(session, org_id, item.id, key)
    if attribute is None:
        raise NotFoundProblem(f"{item.sku} has no {key!r} attribute")
    attribute_id = attribute.id
    await session.delete(attribute)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="item_attribute.deleted",
        entity="item_attribute",
        entity_id=attribute_id,
        before={"key": key},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_ITEM_ATTRIBUTE_DELETED,
        aggregate_type="item_attribute",
        aggregate_id=attribute_id,
        payload={"organization_id": str(org_id), "inventory_item_id": str(item.id), "key": key},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ item variants


async def create_item_variant(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    sku: str,
    name: str,
    request: Request | None = None,
) -> ItemVariant:
    org_id = ctx.organization_id
    await _lock_sku(session, org_id, sku)
    if await inv_selectors.get_item_variant_by_sku(session, org_id, sku):
        raise ConflictProblem(f"A variant with SKU {sku!r} already exists")
    # A variant's SKU shares one namespace with the item SKU it decorates — nothing else in this
    # org distinguishes a receipt/move line's SKU as "the item" vs. "one of its variants" (LOW).
    if await inv_selectors.get_inventory_by_sku(session, org_id, sku):
        raise ConflictProblem(f"An inventory item with SKU {sku!r} already exists")

    variant = ItemVariant(
        organization_id=org_id,
        created_by=str(ctx.principal.sub),
        inventory_item_id=item.id,
        sku=sku,
        name=name,
    )
    session.add(variant)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=org_id,
        actor_id=ctx.principal.sub,
        action="item_variant.created",
        entity="item_variant",
        entity_id=variant.id,
        after={"sku": sku, "name": name, "inventory_item_id": str(item.id)},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=org_id,
        event_type=EVT_ITEM_VARIANT_CREATED,
        aggregate_type="item_variant",
        aggregate_id=variant.id,
        payload={
            "organization_id": str(org_id),
            "item_variant_id": str(variant.id),
            "inventory_item_id": str(item.id),
            "sku": sku,
        },
        actor=_actor(ctx),
    )
    return variant


async def update_item_variant(
    session: AsyncSession,
    ctx: ScopeContext,
    variant: ItemVariant,
    payload: ItemVariantPatch,
    *,
    request: Request | None = None,
) -> ItemVariant:
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(variant, field) for field in changes}
    for field, value in changes.items():
        setattr(variant, field, value)
    variant.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="item_variant.updated",
        entity="item_variant",
        entity_id=variant.id,
        before=before,
        after=changes,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ITEM_VARIANT_UPDATED,
        aggregate_type="item_variant",
        aggregate_id=variant.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "item_variant_id": str(variant.id),
            "changed": sorted(changes),
        },
        actor=_actor(ctx),
    )
    return variant


async def delete_item_variant(
    session: AsyncSession,
    ctx: ScopeContext,
    variant: ItemVariant,
    *,
    request: Request | None = None,
) -> None:
    before_deleted_at = variant.deleted_at
    variant.deleted_at = datetime.now(UTC)
    variant.updated_by = str(ctx.principal.sub)
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="item_variant.deleted",
        entity="item_variant",
        entity_id=variant.id,
        before={"deleted_at": before_deleted_at},
        after={"deleted_at": variant.deleted_at},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_ITEM_VARIANT_DELETED,
        aggregate_type="item_variant",
        aggregate_id=variant.id,
        payload={"organization_id": str(ctx.organization_id), "item_variant_id": str(variant.id)},
        actor=_actor(ctx),
    )


# ------------------------------------------------------------------ stock (item-level API)


async def adjust_stock(
    session: AsyncSession,
    ctx: ScopeContext,
    item: InventoryItem,
    *,
    quantity_delta: int | None = None,
    counted_on_hand: int | None = None,
    reason: str | None = None,
    note: str = "",
    occurred_on: date | None = None,
    occurred_on_explicit: bool = True,
    idempotency_key: str | None = None,
    request: Request | None = None,
) -> InventoryItem:
    """Adjust (`quantity_delta`) or count (`counted_on_hand`) one item's stock, in whole units, at
    its home bucket. A count's change is computed under the bucket's lock and recorded even when
    it is zero — a zero-change count is still a fact, and is what moves `stocked_at`.

    `item` must already be locked by the caller. A count is refused while the item holds stock
    anywhere but its home bucket: the whole-item figure it asserts would silently overwrite
    stock it cannot see. A retried request with the same `idempotency_key` changes nothing and
    answers with the item as it stands.

    `occurred_on_explicit=False` — the caller's `occurred_on` came from a default rather than
    something a client sent — keeps it out of the idempotency hash, so a retry that crosses
    midnight is still recognised as the same request rather than a 409 (see `ledger.post_move`).
    """
    effective_date = occurred_on or date.today()
    home = _home(item)
    before_on_hand = item.on_hand
    if counted_on_hand is not None:
        if await inv_selectors.stock_outside_home(session, item):
            raise ConflictProblem(
                f"{item.sku} holds stock outside {item.warehouse}'s default bin, so a single "
                "count cannot describe it; count each location instead"
            )
        kind = "count"
        line = MoveLine(
            item_id=item.id, location_id=home, counted_milli=counted_on_hand * QTY_SCALE
        )
    else:
        kind = "adjustment"
        line = MoveLine(
            item_id=item.id, location_id=home, quantity_milli=(quantity_delta or 0) * QTY_SCALE
        )

    posted = await ledger.post_move(
        session,
        ctx,
        kind=kind,
        lines=[line],
        reason=reason,
        note=note,
        occurred_on=effective_date,
        idempotency_key=idempotency_key,
        occurred_on_explicit=occurred_on_explicit,
        request=request,
    )
    if posted.replayed:
        return item

    entry = posted.entries[0]
    delta = whole_units(entry.quantity_milli)
    item.stocked_at = effective_date
    item.updated_by = str(ctx.principal.sub)
    item.updated_at = datetime.now(UTC)
    await _flush_item(session, item)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_item.stock_adjusted",
        entity="inventory_item",
        entity_id=item.id,
        before={"on_hand": before_on_hand},
        after={"on_hand": item.on_hand, "delta": delta, "kind": kind, "reason": reason},
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_STOCK_ADJUSTED,
        aggregate_type="inventory_item",
        aggregate_id=item.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "inventory_item_id": str(item.id),
            "sku": item.sku,
            "movement_id": str(entry.id),
            "quantity_delta": delta,
            "on_hand_after": item.on_hand,
            "kind": kind,
        },
        actor=_actor(ctx),
    )
    return item


# ------------------------------------------------------------------ reconciliation


async def reconcile_now(
    session: AsyncSession, ctx: ScopeContext, *, request: Request | None = None
) -> ReconcileReport:
    """Run the nightly check for the caller's organization, now. Records findings; repairs
    nothing. Audited, because who asked and what they were told is part of the story."""
    report = await reconcile_organization(session, ctx.organization_id)
    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory.reconciled",
        entity="inventory_reconcile_run",
        entity_id=report.run_id,
        after={
            "buckets_checked": report.buckets_checked,
            "findings": len(report.findings),
            "new_findings": len(report.new),
        },
        request=request,
    )
    return report


async def resolve_discrepancy(
    session: AsyncSession,
    ctx: ScopeContext,
    discrepancy: InventoryDiscrepancy,
    *,
    note: str,
    request: Request | None = None,
) -> InventoryDiscrepancy:
    """Close a discrepancy once a person has dealt with its cause. This records the decision; it
    changes no stock. If the drift is still there, the next reconciliation opens a new one."""
    if discrepancy.resolved_at is not None:
        raise ConflictProblem("That discrepancy is already resolved")
    discrepancy.resolved_at = datetime.now(UTC)
    discrepancy.resolved_by = str(ctx.principal.sub)
    discrepancy.resolution_note = note
    await session.flush()

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="inventory_discrepancy.resolved",
        entity="inventory_discrepancy",
        entity_id=discrepancy.id,
        before={"resolved_at": None},
        after={"resolved_at": discrepancy.resolved_at, "note": note},
        reason=note,
        request=request,
    )
    publish_event(
        session,
        OutboxEvent,
        organization_id=ctx.organization_id,
        event_type=EVT_DISCREPANCY_RESOLVED,
        aggregate_type="inventory_discrepancy",
        aggregate_id=discrepancy.id,
        payload={
            "organization_id": str(ctx.organization_id),
            "discrepancy_id": str(discrepancy.id),
            "kind": discrepancy.kind,
            "note": note,
        },
        actor=_actor(ctx),
    )
    return discrepancy

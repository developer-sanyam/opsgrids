"""Shared set-up for the storage put-away / find-stock / published-layout tests.

Built on `tracking_fixtures.build_world` (the textile organization with a lot, a roll and a second
bin) and adds exactly what a put-away needs, all through the real service paths: a **receiving**
bin and a **staging** bin holding stock to store, barcoded rack bins to store it into, a shelf, a
barcoded bin in the *other* warehouse, and the fabric roll opened into the receiving bin so a
tracked item can be put away too.

Also the three things every module here needs and would otherwise copy: a way to call the routes as
a principal holding exactly the permissions a test names, the distinctive coordinates a worker's fix
carries in these tests, and a recursive scan that proves those coordinates appear nowhere they are
not allowed to.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import services as inv_services
from app.inventory import stock_ops
from app.inventory.tracking_schemas import OpeningLineIn, OpeningStockIn
from app.models import (
    AuditLog,
    InventoryMove,
    LayoutVersion,
    LocationPlacement,
    OutboxEvent,
    PutawayCapture,
    Warehouse,
    WarehouseFloor,
)
from platform_core.auth import Principal, Scope
from tests.helpers import as_principal, ctx_for, principal_for
from tests.inventory_fixtures import TODAY
from tests.tracking_fixtures import (
    API,
    STOCK_READ,
    STOCK_TRANSFER,
    World,
    build_world,
)

__all__ = [
    "ACC",
    "API",
    "FLOOR_WRITE",
    "LAT",
    "LAYOUT_READ",
    "LNG",
    "PUTAWAY",
    "REVEAL_PERM",
    "STOCK_READ",
    "STOCK_TRANSFER",
    "WAREHOUSE_READ",
    "WAREHOUSE_WRITE",
    "PutawayWorld",
    "audit_rows",
    "build_putaway_world",
    "call",
    "captures",
    "coordinate_leaks",
    "count",
    "foreign_barcoded_bin",
    "line",
    "make_location",
    "move_ids",
    "opening",
    "outbox_rows",
    "place",
    "put",
    "putaway_body",
    "reveal_url",
    "tracked_body",
]

PUTAWAY = "/operations/inventory/putaway"
REVEAL_PERM = "operations.inventory.putaway.location_read:ORG"
LAYOUT_READ = "operations.warehouse.layout.read:ORG"
WAREHOUSE_READ = "operations.inventory.warehouse.read:ORG"
WAREHOUSE_WRITE = "operations.inventory.warehouse.write:ORG"
FLOOR_WRITE = "operations.warehouse.floor.write:ORG"

#: A worker's fix in these tests. Distinctive on purpose: `coordinate_leaks` looks for these digits
#: anywhere a response, audit row, event payload or log line could carry them, and nothing else in
#: the suite uses them. (Bengaluru, to six places, with a 9 m accuracy.)
LAT = 12_971_601
LNG = 77_594_602
ACC = 9


def reveal_url(move_id: object) -> str:
    return f"{API}{PUTAWAY}/{move_id}/location:reveal"


@dataclass
class PutawayWorld:
    """The textile world plus the bins a put-away moves between. Ids only — never ORM objects, which
    a refused request's rollback would expire under the test's feet."""

    w: World
    warehouse: uuid.UUID  # WH-WEAVE
    receiving: uuid.UUID  # kind receiving, barcode RCV-1-BC — 20 kg of dye, AVAILABLE
    staging: uuid.UUID  # kind staging, barcode STG-1-BC — 10 kg of dye, AVAILABLE
    rack: uuid.UUID  # kind rack RACK-1, no barcode — the parent of both rack bins
    rack_bin: uuid.UUID  # bin R1-B01, barcode RACK-1-B01, pick_sequence 1
    rack_bin2: uuid.UUID  # bin R1-B02, barcode RACK-1-B02, pick_sequence 2
    shelf: uuid.UUID  # shelf SHELF-1, barcode SHELF-1-BC
    other_warehouse_bin: uuid.UUID  # bin FIN-B01 in WH-FIN, barcode FIN-B01-BC

    @property
    def org_id(self) -> uuid.UUID:
        return self.w.org_id

    @property
    def dye(self) -> uuid.UUID:
        return self.w.t.dye

    @property
    def fabric(self) -> uuid.UUID:
        return self.w.t.fabric


async def build_putaway_world(session: AsyncSession, org_id: uuid.UUID) -> PutawayWorld:
    """The stocked organization a put-away test starts from. The fabric's lot and roll exist but
    their stock is opened into the *receiving* bin (82.5 m), not the weaving store's default bin."""
    w = await build_world(session, org_id, open_roll=False)
    async with session.begin():
        warehouse = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == org_id, Warehouse.code == "WH-WEAVE"
                )
            )
        ).scalar_one()
        other = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == org_id, Warehouse.code == "WH-FIN"
                )
            )
        ).scalar_one()
        ctx = w.t.ctx
        receiving = await inv_services.create_location(
            session,
            ctx,
            warehouse,
            kind="receiving",
            code="RCV-1",
            name="Receiving",
            barcode="RCV-1-BC",
        )
        staging = await inv_services.create_location(
            session,
            ctx,
            warehouse,
            kind="staging",
            code="STG-1",
            name="Staging",
            barcode="STG-1-BC",
        )
        rack = await inv_services.create_location(
            session, ctx, warehouse, kind="rack", code="RACK-1", name="Rack 1"
        )
        rack_bin = await inv_services.create_location(
            session,
            ctx,
            warehouse,
            kind="bin",
            code="R1-B01",
            name="Rack 1 bin 1",
            parent_id=str(rack.id),
            barcode="RACK-1-B01",
            pick_sequence=1,
        )
        rack_bin2 = await inv_services.create_location(
            session,
            ctx,
            warehouse,
            kind="bin",
            code="R1-B02",
            name="Rack 1 bin 2",
            parent_id=str(rack.id),
            barcode="RACK-1-B02",
            pick_sequence=2,
        )
        shelf = await inv_services.create_location(
            session,
            ctx,
            warehouse,
            kind="shelf",
            code="SHELF-1",
            name="Shelf 1",
            barcode="SHELF-1-BC",
        )
        other_bin = await inv_services.create_location(
            session,
            ctx,
            other,
            kind="bin",
            code="FIN-B01",
            name="Finishing bin",
            barcode="FIN-B01-BC",
        )
        lines = [
            OpeningLineIn(inventory_item_id=w.t.dye, location_id=receiving.id, quantity="20"),
            OpeningLineIn(inventory_item_id=w.t.dye, location_id=staging.id, quantity="10"),
            OpeningLineIn(
                inventory_item_id=w.t.fabric,
                location_id=receiving.id,
                quantity="82.5",
                lot_id=w.lot,
                roll_id=w.roll,
            ),
        ]
        await stock_ops.post_opening_stock(
            session,
            ctx,
            OpeningStockIn(lines=lines, occurred_on=TODAY),
            idempotency_key=None,
            occurred_on_explicit=False,
        )
        ids = PutawayWorld(
            w=w,
            warehouse=warehouse.id,
            receiving=receiving.id,
            staging=staging.id,
            rack=rack.id,
            rack_bin=rack_bin.id,
            rack_bin2=rack_bin2.id,
            shelf=shelf.id,
            other_warehouse_bin=other_bin.id,
        )
    return ids


def putaway_body(pw: PutawayWorld, **extra: Any) -> dict[str, Any]:
    """5 kg of dye from the receiving bin into the first rack bin, by barcode. Keyword arguments
    override or add fields (`quantity="6"`, `lat_micro=…`); pass `None` to drop one."""
    body: dict[str, Any] = {
        "inventory_item_id": str(pw.dye),
        "from_location_id": str(pw.receiving),
        "to_barcode": "RACK-1-B01",
        "quantity": "5",
    }
    body.update(extra)
    return {k: v for k, v in body.items() if v is not None}


def tracked_body(pw: PutawayWorld, **extra: Any) -> dict[str, Any]:
    """The whole 82.5 m fabric roll from the receiving bin into the first rack bin. Keyword
    arguments override or add fields; pass `None` to drop one."""
    defaults: dict[str, Any] = {
        "inventory_item_id": str(pw.fabric),
        "quantity": "82.5",
        "lot_id": str(pw.w.lot),
        "roll_id": str(pw.w.roll),
    }
    return putaway_body(pw, **{**defaults, **extra})


async def call(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    perms: list[str],
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
    key: str | None = None,
    principal: Principal | None = None,
    **principal_kwargs: Any,
):
    """One request as a principal holding exactly `perms` (scope-qualified, e.g. `…stock.read:ORG`),
    or as `principal` when the test built its own (to read its `sub` back). `key` sets the
    `Idempotency-Key` header."""
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal or principal_for(org_id, perms, **principal_kwargs)):
        return await client.request(method, API + path, json=json, params=params, headers=headers)


# ------------------------------------------------------------------ small builders


def line(
    item: uuid.UUID,
    location: uuid.UUID,
    quantity: str,
    *,
    status: str = "AVAILABLE",
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
) -> OpeningLineIn:
    return OpeningLineIn(
        inventory_item_id=item,
        location_id=location,
        quantity=quantity,
        status=status,
        lot_id=lot_id,
        roll_id=roll_id,
    )


def opening(lines: list[OpeningLineIn]) -> OpeningStockIn:
    return OpeningStockIn(lines=lines, occurred_on=TODAY)


async def make_location(
    session: AsyncSession,
    pw: PutawayWorld,
    *,
    kind: str,
    code: str,
    barcode: str | None = None,
    warehouse: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Another location through the real service, in WH-WEAVE unless `warehouse` says otherwise."""
    async with session.begin():
        wh = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == pw.org_id,
                    Warehouse.id == (warehouse or pw.warehouse),
                )
            )
        ).scalar_one()
        loc = await inv_services.create_location(
            session,
            pw.w.t.ctx,
            wh,
            kind=kind,
            code=code,
            name=code.title(),
            barcode=barcode,
            parent_id=str(parent_id) if parent_id else None,
        )
        return loc.id


async def foreign_barcoded_bin(admin_session: AsyncSession, barcode: str) -> uuid.UUID:
    """A second tenant with one bin carrying `barcode`, built on the BYPASSRLS session (which the
    caller must not have a transaction open on)."""
    org = uuid.uuid4()
    ctx = ctx_for(org, "operations.inventory.stock.adjust", Scope.ORG)
    async with admin_session.begin():
        warehouse = await inv_services.create_warehouse(
            admin_session, ctx, code="WH-X", name="Elsewhere"
        )
        loc = await inv_services.create_location(
            admin_session, ctx, warehouse, kind="bin", code="X-1", name="X", barcode=barcode
        )
        return loc.id


async def put(
    client: Any,
    pw: PutawayWorld,
    body: dict[str, Any],
    key: str | None = "pa-1",
    **kwargs: Any,
) -> Any:
    """A put-away as a caller holding exactly `stock.transfer` — the permission a worker has."""
    return await call(
        client, pw.org_id, [STOCK_TRANSFER], "POST", PUTAWAY, json=body, key=key, **kwargs
    )


async def count(session: AsyncSession, model: type, org_id: uuid.UUID) -> int:
    async with session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(model).where(model.organization_id == org_id)  # type: ignore[attr-defined]
                )
            ).scalar_one()
        )


async def audit_rows(
    session: AsyncSession, org_id: uuid.UUID, *, action: str | None = None
) -> list[AuditLog]:
    async with session.begin():
        stmt = (
            select(AuditLog)
            .where(AuditLog.organization_id == org_id)
            .order_by(AuditLog.at, AuditLog.id)
        )
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        return list((await session.execute(stmt)).scalars().all())


async def outbox_rows(
    session: AsyncSession, org_id: uuid.UUID, *, event_type: str | None = None
) -> list[OutboxEvent]:
    async with session.begin():
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.organization_id == org_id)
            .order_by(OutboxEvent.id)
        )
        if event_type is not None:
            stmt = stmt.where(OutboxEvent.event_type == event_type)
        return list((await session.execute(stmt)).scalars().all())


async def move_ids(
    session: AsyncSession, org_id: uuid.UUID, *, reference_type: str
) -> list[uuid.UUID]:
    async with session.begin():
        rows = await session.execute(
            select(InventoryMove.id).where(
                InventoryMove.organization_id == org_id,
                InventoryMove.reference_type == reference_type,
            )
        )
        return [r[0] for r in rows.all()]


async def captures(session: AsyncSession, org_id: uuid.UUID) -> list[PutawayCapture]:
    async with session.begin():
        stmt = select(PutawayCapture).where(PutawayCapture.organization_id == org_id)
        return list((await session.execute(stmt)).scalars().all())


# ------------------------------------------------------------------ the coordinate scan

#: A key that names a position or its accuracy — anywhere in a document that is not the audited
#: reveal or the inbound body, one of these is a leak.
_COORDINATE_KEY = re.compile(
    r"^(lat|lng|lon|long|latitude|longitude)(_micro|_deg|_degrees)?$|^accuracy(_m|_metres|_meters)?$",
    re.IGNORECASE,
)
#: Digits that only ever come from the fixes these tests send.
_COORDINATE_DIGITS = (str(LAT), str(LNG), f"{LAT / 1e6:.6f}", f"{LNG / 1e6:.6f}")


def coordinate_leaks(document: Any, *, path: str = "$") -> list[str]:
    """Where, in a JSON-like document, a coordinate (or its accuracy) shows up: a key that names
    one, or a value carrying the digits of the fixes these tests send (as a number, a string, or
    inside one). Recursive through dicts and lists. An empty list means clean.

    The *names* of what a reveal discloses (`["lat_micro", "lng_micro", "accuracy_m"]` in an audit
    row's `fields`) are values, not keys, and are not a leak — they are the point of that row."""
    found: list[str] = []
    if isinstance(document, dict):
        for key, value in document.items():
            here = f"{path}.{key}"
            if _COORDINATE_KEY.match(str(key)):
                found.append(here)
            found.extend(coordinate_leaks(value, path=here))
    elif isinstance(document, list | tuple):
        for i, value in enumerate(document):
            found.extend(coordinate_leaks(value, path=f"{path}[{i}]"))
    elif document is not None and not isinstance(document, bool):
        text = str(document)
        if any(digits in text for digits in _COORDINATE_DIGITS):
            found.append(f"{path} = {text[:60]}")
    return found


# ------------------------------------------------------------------ published layouts


async def place(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID,
    floor_number: int = 1,
    floor_name: str = "Ground",
    placements: dict[uuid.UUID, tuple[int, int, int, int]] | None = None,
    status: str = "published",
    version_number: int = 1,
    floor: WarehouseFloor | None = None,
    published_at: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A floor with one layout version in `status` placing each `location -> (x, y, w, d)` (mm).
    Returns `(floor_id, layout_version_id)`. Pass `floor` to add a version to an existing floor.
    Inside a transaction the caller opened."""
    if floor is None:
        floor = WarehouseFloor(
            organization_id=org_id,
            warehouse_id=warehouse_id,
            floor_number=floor_number,
            name=floor_name,
        )
        session.add(floor)
        await session.flush()
    version = LayoutVersion(
        organization_id=org_id,
        warehouse_floor_id=floor.id,
        version_number=version_number,
        status=status,
        published_at=published_at,
    )
    session.add(version)
    await session.flush()
    for location_id, (x, y, width, depth) in (placements or {}).items():
        session.add(
            LocationPlacement(
                organization_id=org_id,
                layout_version_id=version.id,
                location_id=location_id,
                x_mm=x,
                y_mm=y,
                width_mm=width,
                depth_mm=depth,
            )
        )
    await session.flush()
    return floor.id, version.id

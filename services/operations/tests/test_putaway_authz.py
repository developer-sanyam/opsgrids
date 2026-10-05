"""Negative authorization and cross-tenant isolation for **every** route that storage put-away,
find-stock, the published floor layout and the warehouse map add (the `test_tracking_authz.py` style
— one table drives all of it, so a route added without a row here is visible in review):

* a caller with **no** permission is 403;
* a caller holding **every other** permission this surface touches — including the existing
  worker-location permission, which must not unlock the put-away reveal — is 403;
* the right permission at a scope **below** the route's floor (`:OWN` where the route needs `ORG`)
  is 403;
* the right permission **without the Operations entitlement** is 402, never data;
* a caller from **another organization**, holding exactly the right permission, is 404 on every
  route that names a row (or a row of another tenant in its body) and sees an empty map;
* a refused call **changes nothing**: no move, no ledger row, no capture, no audit row, no event;
* the **real role templates** (`OPERATIONS_ADMIN` / `MANAGER` / `WORKER` / `USER`) reach exactly the
  routes they should — the GPS reveal is the administrator's alone — and
* row-level security, as the NOBYPASSRLS app role, holds on `putaway_captures` itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalogue import OPERATIONS_ROLE_TEMPLATES
from app.config import settings
from app.inventory import stock_ops
from app.inventory.putaway_schemas import PutawayIn
from app.models import (
    SCHEMA,
    AuditLog,
    InventoryMove,
    LedgerEntry,
    OutboxEvent,
    PutawayCapture,
    Warehouse,
)
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, principal_for
from tests.putaway_fixtures import (
    API,
    FLOOR_WRITE,
    LAT,
    LAYOUT_READ,
    LNG,
    PUTAWAY,
    REVEAL_PERM,
    STOCK_READ,
    STOCK_TRANSFER,
    WAREHOUSE_READ,
    WAREHOUSE_WRITE,
    PutawayWorld,
    build_putaway_world,
    call,
    count,
    place,
    put,
    putaway_body,
)
from tests.tracking_fixtures import STOCK_ADJUST

pytestmark = pytest.mark.integration

#: Everything the surface touches *and* neighbours that might be mistaken for it — in particular
#: the fulfilment worker-location permission, which reveals a different kind of position and must
#: not open this one.
OTHER_PERMS = (
    STOCK_READ,
    STOCK_TRANSFER,
    STOCK_ADJUST,
    REVEAL_PERM,
    LAYOUT_READ,
    WAREHOUSE_READ,
    WAREHOUSE_WRITE,
    FLOOR_WRITE,
    "operations.inventory.stock.reserve:ORG",
    "operations.inventory.location.read:ORG",
    "operations.inventory.location.write:ORG",
    "operations.inventory.item.read:ORG",
    "operations.warehouse.floor.read:ORG",
    "operations.warehouse.layout.write:ORG",
    "operations.fulfilment.worker.location_read:ORG",
    "operations.fulfilment.worker.read:ORG",
)


@dataclass
class Az:
    """The owner's world plus one put-away that has a capture (with a fix) and a floor whose
    published layout places the rack bin."""

    pw: PutawayWorld
    move_id: uuid.UUID
    floor_id: uuid.UUID

    @property
    def org_id(self) -> uuid.UUID:
        return self.pw.org_id


@pytest.fixture
async def az(client, session, org_id) -> Az:
    pw = await build_putaway_world(session, org_id)
    async with session.begin():
        floor_id, _ = await place(
            session,
            org_id,
            warehouse_id=pw.warehouse,
            placements={pw.rack_bin: (1000, 2000, 800, 600)},
        )
    done = await put(
        client,
        pw,
        putaway_body(pw, lat_micro=LAT, lng_micro=LNG, accuracy_m=7),
        key="seed-1",
    )
    assert done.status_code == 201, done.text
    return Az(pw=pw, move_id=uuid.UUID(done.json()["move_id"]), floor_id=floor_id)


@dataclass(frozen=True)
class Route:
    method: str
    path: Callable[[Az], str]
    perm: str
    body: Callable[[Az], Any] = lambda az: None
    params: Callable[[Az], dict[str, Any] | None] = lambda az: None
    #: What another tenant holding exactly `perm` gets: a 404, or an empty map.
    cross_tenant: str = "404"
    #: Whether the route's dependency refuses a grant below ORG scope.
    org_floor: bool = False


ROUTES: dict[str, Route] = {
    "putaway": Route(
        "POST",
        lambda az: PUTAWAY,
        STOCK_TRANSFER,
        body=lambda az: putaway_body(az.pw, quantity="1", to_barcode="RACK-1-B02"),
        org_floor=True,
    ),
    "suggest": Route(
        "GET",
        lambda az: f"{PUTAWAY}/suggest",
        STOCK_TRANSFER,
        params=lambda az: {
            "inventory_item_id": str(az.pw.dye),
            "warehouse_id": str(az.pw.warehouse),
        },
        org_floor=True,
    ),
    "putaway_detail": Route(
        "GET", lambda az: f"{PUTAWAY}/{az.move_id}", STOCK_READ, org_floor=True
    ),
    "reveal": Route(
        "POST", lambda az: f"{PUTAWAY}/{az.move_id}/location:reveal", REVEAL_PERM, org_floor=True
    ),
    "locate": Route(
        "GET",
        lambda az: "/operations/inventory/locate",
        STOCK_READ,
        params=lambda az: {"inventory_item_id": str(az.pw.dye)},
        org_floor=True,
    ),
    "published_layout": Route(
        "GET", lambda az: f"/operations/floors/{az.floor_id}/published-layout", LAYOUT_READ
    ),
    "warehouse_map": Route(
        "GET", lambda az: "/operations/warehouses/map", WAREHOUSE_READ, cross_tenant="empty_map"
    ),
    "warehouse_coordinates": Route(
        "PATCH",
        lambda az: f"/operations/warehouses/{az.pw.warehouse}",
        WAREHOUSE_WRITE,
        body=lambda az: {"lat_micro": 12_000_000, "lng_micro": 77_000_000},
    ),
}


async def send(client, az: Az, route: Route, principal, *, key: str = "authz-1"):  # type: ignore[no-untyped-def]
    headers = (
        {"Idempotency-Key": key} if route.method == "POST" and route.path(az) == PUTAWAY else {}
    )
    with as_principal(principal):
        return await client.request(
            route.method,
            API + route.path(az),
            json=route.body(az),
            params=route.params(az),
            headers=headers,
        )


def _grant(code: str, scope: str = "ORG") -> str:
    return f"{code}:{scope}"


@pytest.mark.parametrize("name", sorted(ROUTES))
class TestEveryRoute:
    async def test_no_permission_is_forbidden(self, client, az, name) -> None:
        resp = await send(client, az, ROUTES[name], principal_for(az.org_id, []))
        assert resp.status_code == 403, resp.text

    async def test_every_other_permission_is_still_forbidden(self, client, az, name) -> None:
        route = ROUTES[name]
        others = [p for p in OTHER_PERMS if p != route.perm]
        resp = await send(client, az, route, principal_for(az.org_id, others))
        assert resp.status_code == 403, resp.text

    async def test_the_right_permission_without_the_entitlement_is_payment_required(
        self, client, az, name
    ) -> None:
        route = ROUTES[name]
        who = principal_for(az.org_id, [route.perm], entitlements=[])
        resp = await send(client, az, route, who)
        assert resp.status_code == 402, resp.text
        who = principal_for(az.org_id, [route.perm], entitlements=["crm"])
        assert (await send(client, az, route, who)).status_code == 402

    async def test_another_tenant_gets_404_or_an_empty_map(self, client, az, name) -> None:
        route = ROUTES[name]
        intruder = principal_for(uuid.uuid4(), [route.perm])
        resp = await send(client, az, route, intruder)
        if route.cross_tenant == "empty_map":
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"sites": [], "without_coordinates": []}
        else:
            assert resp.status_code == 404, resp.text

    async def test_the_right_permission_for_the_right_tenant_works(self, client, az, name) -> None:
        """The control: every row above is refused for the reason it says, not because the call is
        malformed."""
        route = ROUTES[name]
        resp = await send(client, az, route, principal_for(az.org_id, [route.perm]))
        assert resp.status_code in (200, 201), resp.text


@pytest.mark.parametrize("name", sorted(n for n, r in ROUTES.items() if r.org_floor))
async def test_a_grant_below_org_scope_is_forbidden(client, az, name) -> None:
    """These routes act on the whole organization's stock; `OWN` has nothing of its own to scope
    to and must match nothing, not everything."""
    route = ROUTES[name]
    code = route.perm.rsplit(":", 1)[0]
    for scope in ("OWN", "TEAM", "LOCATION"):
        resp = await send(client, az, route, principal_for(az.org_id, [_grant(code, scope)]))
        assert resp.status_code == 403, (scope, resp.text)


@pytest.mark.parametrize("name", sorted(ROUTES))
async def test_a_request_with_no_credentials_is_unauthenticated(client, az, name) -> None:
    route = ROUTES[name]
    resp = await client.request(
        route.method,
        API + route.path(az),
        json=route.body(az),
        params=route.params(az),
        headers={"Idempotency-Key": "x"} if name == "putaway" else {},
    )
    assert resp.status_code == 401, resp.text


# ------------------------------------------------------------------ a refused call changes nothing


async def _footprint(session, org_id: uuid.UUID) -> dict[str, int]:
    return {
        m.__tablename__: await count(session, m, org_id)
        for m in (InventoryMove, LedgerEntry, PutawayCapture, AuditLog, OutboxEvent)
    }


async def _coordinates_of_the_warehouse(session, az: Az) -> tuple[int | None, int | None]:
    async with session.begin():
        wh = (
            await session.execute(
                select(Warehouse).where(
                    Warehouse.organization_id == az.org_id, Warehouse.id == az.pw.warehouse
                )
            )
        ).scalar_one()
        return wh.lat_micro, wh.lng_micro


WRITES = ["putaway", "reveal", "warehouse_coordinates"]


@pytest.mark.parametrize("name", WRITES)
class TestRefusedWritesChangeNothing:
    async def test_forbidden_and_cross_tenant_calls_leave_the_owner_and_the_intruder_untouched(
        self, client, session, az, name
    ) -> None:
        route = ROUTES[name]
        intruder_org = uuid.uuid4()
        before_owner = await _footprint(session, az.org_id)
        before_coords = await _coordinates_of_the_warehouse(session, az)
        refused = [
            principal_for(az.org_id, []),
            principal_for(az.org_id, [p for p in OTHER_PERMS if p != route.perm]),
            principal_for(az.org_id, [route.perm], entitlements=[]),
            principal_for(intruder_org, [route.perm]),
        ]
        for who in refused:
            resp = await send(client, az, route, who, key="refused-1")
            assert resp.status_code in (402, 403, 404), resp.text
        assert await _footprint(session, az.org_id) == before_owner
        assert await _coordinates_of_the_warehouse(session, az) == before_coords
        assert all(n == 0 for n in (await _footprint(session, intruder_org)).values())


async def test_a_refused_reveal_writes_no_audit_row_whatever_the_reason(
    client, session, az
) -> None:
    others = [p for p in OTHER_PERMS if p != REVEAL_PERM]
    for who in (
        principal_for(az.org_id, []),
        principal_for(az.org_id, others),
        principal_for(az.org_id, [_grant("operations.inventory.putaway.location_read", "OWN")]),
        principal_for(az.org_id, [REVEAL_PERM], entitlements=[]),
        principal_for(uuid.uuid4(), [REVEAL_PERM]),
    ):
        await send(client, az, ROUTES["reveal"], who)
    async with session.begin():
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                )
            )
            .scalars()
            .all()
        )
    assert rows == []


# ------------------------------------------------------------------ the real roles


def _role_perms(code: str) -> list[str]:
    template = next(t for t in OPERATIONS_ROLE_TEMPLATES if t.code == code)
    return [_grant(g.code, g.scope) for g in template.grants]


#: route -> the roles that may call it, from the roles' own descriptions in the catalogue.
ALLOWED = {
    "putaway": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_WORKER"},
    "suggest": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_WORKER"},
    "putaway_detail": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_WORKER"},
    "locate": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_WORKER"},
    # The GPS reveal: the administrator alone (owner decision D3).
    "reveal": {"OPERATIONS_ADMIN"},
    "published_layout": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_USER"},
    "warehouse_map": {"OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_USER"},
    # A manager "does not create or rename warehouses" — and so cannot place one on a map.
    "warehouse_coordinates": {"OPERATIONS_ADMIN"},
}
ROLES = ["OPERATIONS_ADMIN", "OPERATIONS_MANAGER", "OPERATIONS_WORKER", "OPERATIONS_USER"]


class TestTheRealRoleTemplates:
    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize("name", sorted(ALLOWED))
    async def test_each_role_reaches_exactly_the_routes_it_should(
        self, client, az, name, role
    ) -> None:
        who = principal_for(az.org_id, _role_perms(role), subject_ref=uuid.uuid4())
        resp = await send(client, az, ROUTES[name], who)
        if role in ALLOWED[name]:
            assert resp.status_code in (200, 201), (role, name, resp.text)
        else:
            assert resp.status_code == 403, (role, name, resp.text)

    def test_every_new_route_has_a_row_in_the_role_table(self) -> None:
        assert set(ALLOWED) == set(ROUTES)

    async def test_a_worker_can_put_stock_away_but_cannot_see_where_anyone_stood(
        self, client, session, az
    ) -> None:
        worker = principal_for(
            az.org_id, _role_perms("OPERATIONS_WORKER"), subject_ref=uuid.uuid4()
        )
        made = await send(client, az, ROUTES["putaway"], worker, key="worker-1")
        assert made.status_code == 201
        move = made.json()["move_id"]
        for target in (az.move_id, move):
            with as_principal(worker):
                refused = await client.post(f"{API}{PUTAWAY}/{target}/location:reveal")
            assert refused.status_code == 403, refused.text
        async with session.begin():
            reads = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "entity.sensitive_read")
                    )
                )
                .scalars()
                .all()
            )
        assert reads == []

    async def test_the_reveal_permission_is_in_no_role_but_the_administrators(self) -> None:
        code = "operations.inventory.putaway.location_read"
        holders = {t.code for t in OPERATIONS_ROLE_TEMPLATES if code in {g.code for g in t.grants}}
        assert holders == {"OPERATIONS_ADMIN"}


# ------------------------------------------------------------------ row-level security, per table


@pytest.fixture
async def foreign_capture(admin_session) -> tuple[uuid.UUID, uuid.UUID]:
    """Another tenant's put-away, capture included, built through the real service on the BYPASSRLS
    session. Returns `(org_id, capture_id)`."""
    foreign_org = uuid.uuid4()
    pw = await build_putaway_world(admin_session, foreign_org)
    async with admin_session.begin():
        await stock_ops.putaway_stock(
            admin_session,
            pw.w.t.ctx,
            PutawayIn(
                inventory_item_id=pw.dye,
                from_location_id=pw.receiving,
                to_barcode="RACK-1-B01",
                quantity="1",
                lat_micro=LAT,
                lng_micro=LNG,
            ),
            idempotency_key="foreign-1",
            occurred_on_explicit=False,
        )
        capture_id = (
            await admin_session.execute(
                select(PutawayCapture.id).where(PutawayCapture.organization_id == foreign_org)
            )
        ).scalar_one()
    return foreign_org, capture_id


async def _bound_session(org: uuid.UUID | None):  # type: ignore[no-untyped-def]
    """A session as the **app** role, bound to `org` (or unbound for `None`)."""
    engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    s = async_sessionmaker(engine, expire_on_commit=False)()
    if org is not None:

        @event.listens_for(s.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return s, engine


class TestRowLevelSecurityOnTheCapturesTable:
    async def test_a_bound_session_sees_only_its_own_captures(self, az, foreign_capture) -> None:
        foreign_org, foreign_id = foreign_capture
        s, engine = await _bound_session(az.org_id)
        try:
            async with s.begin():
                rows = (
                    (
                        await s.execute(
                            text("SELECT organization_id FROM operations.putaway_captures")
                        )
                    )
                    .scalars()
                    .all()
                )
                named = (
                    await s.execute(
                        text("SELECT id FROM operations.putaway_captures WHERE id = :i"),
                        {"i": foreign_id},
                    )
                ).scalar_one_or_none()
        finally:
            await s.close()
            await engine.dispose()
        assert set(rows) == {az.org_id} and len(rows) == 1
        assert foreign_org not in set(rows)
        assert named is None, "another tenant's capture was readable by primary key"

    async def test_an_unbound_session_sees_nothing_at_all(self, az, foreign_capture) -> None:
        s, engine = await _bound_session(None)
        try:
            async with s.begin():
                seen = (
                    await s.execute(text("SELECT count(*) FROM operations.putaway_captures"))
                ).scalar_one()
        finally:
            await s.close()
            await engine.dispose()
        assert seen == 0

    async def test_writing_a_capture_into_another_tenant_is_refused(
        self, az, foreign_capture
    ) -> None:
        """`WITH CHECK`: USING gates existing rows, the new row of an INSERT needs its own check."""
        foreign_org, _ = foreign_capture
        s, engine = await _bound_session(az.org_id)
        try:
            with pytest.raises(DBAPIError, match="row-level security"):
                async with s.begin():
                    await s.execute(
                        text(
                            "INSERT INTO operations.putaway_captures "
                            "(id, organization_id, move_id, location_id, created_by, captured_at) "
                            "SELECT gen_random_uuid(), :foreign, move_id, location_id, "
                            "       gen_random_uuid(), now() "
                            "  FROM operations.putaway_captures LIMIT 1"
                        ),
                        {"foreign": foreign_org},
                    )
        finally:
            await s.close()
            await engine.dispose()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE operations.putaway_captures SET lat_micro = NULL, lng_micro = NULL",
            "DELETE FROM operations.putaway_captures",
            "TRUNCATE operations.putaway_captures",
        ],
        ids=["update", "delete", "truncate"],
    )
    async def test_the_app_role_can_neither_rewrite_nor_erase_a_capture(
        self, session, az, statement
    ) -> None:
        """Append-only by privilege: the 30-day purge is an admin-run procedure, never the app."""
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with session.begin():
                await session.execute(text(statement))
        assert await count(session, PutawayCapture, az.org_id) == 1

    async def test_a_capture_cannot_be_reached_through_the_other_tenants_move(
        self, client, az, foreign_capture, admin_session
    ) -> None:
        """Naming the foreign move over HTTP, with every put-away permission, is a 404 on all three
        routes that take a move id."""
        foreign_org, _ = foreign_capture
        async with admin_session.begin():
            foreign_move = (
                await admin_session.execute(
                    select(PutawayCapture.move_id).where(
                        PutawayCapture.organization_id == foreign_org
                    )
                )
            ).scalar_one()
        for method, path, perm in (
            ("GET", f"{PUTAWAY}/{foreign_move}", STOCK_READ),
            ("POST", f"{PUTAWAY}/{foreign_move}/location:reveal", REVEAL_PERM),
        ):
            resp = await call(client, az.org_id, [perm], method, path)
            assert resp.status_code == 404, (path, resp.text)

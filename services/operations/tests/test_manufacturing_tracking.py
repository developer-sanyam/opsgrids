"""Manufacturing over Phase 1 lots and rolls: the lot and roll a request names must exist in the
caller's organization and belong to the item (404 / 422, never an IntegrityError or a misleading
"insufficient stock"), a produced lot is linked to the lots it consumed in the lot genealogy graph,
and a request that touches several rolls takes their locks before any balance lock.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.inventory.reconcile import reconcile_organization
from app.manufacturing import services
from app.manufacturing.schemas import OutputIn
from app.models import SCHEMA, InventoryBalance, LotGenealogy, OutboxEvent
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import balance, q
from tests.manufacturing_fixtures import (
    Mfg,
    build_mfg,
    call,
    make_bom,
    make_lot,
    make_order,
    make_roll,
    ok,
    stock,
)
from tests.tracking_fixtures import make_foreign_world

pytestmark = pytest.mark.integration


@pytest.fixture
async def m(session, org_id) -> Mfg:
    return await build_mfg(session, org_id)


async def edges(session, org_id: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID, str]]:
    async with session.begin():
        rows = (
            await session.execute(
                select(
                    LotGenealogy.parent_lot_id, LotGenealogy.child_lot_id, LotGenealogy.relation
                ).where(LotGenealogy.organization_id == org_id)
            )
        ).all()
    return {(p, c, r) for p, c, r in rows}


async def bucket(  # type: ignore[no-untyped-def]
    session, org_id, item, location, status, lot, roll
) -> tuple[int, int]:
    async with session.begin():
        row = (
            await session.execute(
                select(InventoryBalance.quantity_milli, InventoryBalance.reserved_milli).where(
                    InventoryBalance.organization_id == org_id,
                    InventoryBalance.inventory_item_id == item,
                    InventoryBalance.location_id == location,
                    InventoryBalance.status == status,
                    InventoryBalance.lot_id.is_not_distinct_from(lot),
                    InventoryBalance.roll_id.is_not_distinct_from(roll),
                )
            )
        ).one_or_none()
    return (row[0], row[1]) if row else (0, 0)


class TestNamedLotsAndRollsAreChecked:
    async def _order(self, client, m: Mfg) -> dict:
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "2", "0")])
        return await make_order(client, m, m.shirt, "4")

    def _line(self, order: dict, m: Mfg, **over) -> dict:  # type: ignore[no-untyped-def]
        return {
            "material_id": order["materials"][0]["id"],
            "location_id": str(m.t.weaving),
            "quantity": "2",
            **over,
        }

    @pytest.mark.parametrize("what", ["lot_id", "roll_id"])
    @pytest.mark.parametrize("action", ["reserve", "issue"])
    async def test_an_unknown_lot_or_roll_is_404(
        self, client, m: Mfg, what: str, action: str
    ) -> None:
        order = await self._order(client, m)
        response = await call(
            client,
            m.org_id,
            "POST",
            f"/orders/{order['id']}/{action}",
            json={"lines": [self._line(order, m, **{what: str(uuid.uuid4())})]},
        )
        assert response.status_code == 404, response.text

    async def test_another_organizations_lot_is_404(self, client, m: Mfg, admin_session) -> None:
        foreign = await make_foreign_world(admin_session)
        order = await self._order(client, m)
        for action in ("reserve", "issue"):
            response = await call(
                client,
                m.org_id,
                "POST",
                f"/orders/{order['id']}/{action}",
                json={"lines": [self._line(order, m, lot_id=str(foreign.lot))]},
            )
            assert response.status_code == 404, response.text

    async def test_a_lot_of_another_item_is_422(self, client, session, m: Mfg) -> None:
        order = await self._order(client, m)
        dye_lot = await make_lot(session, m, m.t.dye, "DYE-LOT")
        for action in ("reserve", "issue"):
            response = await call(
                client,
                m.org_id,
                "POST",
                f"/orders/{order['id']}/{action}",
                json={"lines": [self._line(order, m, lot_id=str(dye_lot))]},
            )
            assert response.status_code == 422, response.text

    async def test_a_bogus_output_lot_or_roll_is_404_and_consumes_nothing(
        self, client, session, m: Mfg
    ) -> None:
        await stock(session, m, m.t.fabric, "20")
        order = await self._order(client, m)
        oid = order["id"]
        await ok(client, m.org_id, "POST", f"/orders/{oid}/reserve", json={})
        await ok(client, m.org_id, "POST", f"/orders/{oid}/issue", json={})
        wip_before = await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP")
        for over in ({"lot_id": str(uuid.uuid4())}, {"roll_id": str(uuid.uuid4())}):
            response = await call(
                client, m.org_id, "POST", f"/orders/{oid}/outputs", json={"quantity": "1", **over}
            )
            assert response.status_code == 404, response.text
        # A lot of the wrong item (the shirt's output cannot sit in a fabric lot).
        fabric_lot = await make_lot(session, m, m.t.fabric, "FAB-X")
        response = await call(
            client,
            m.org_id,
            "POST",
            f"/orders/{oid}/outputs",
            json={"quantity": "1", "lot_id": str(fabric_lot)},
        )
        assert response.status_code == 422, response.text
        assert await balance(session, m.org_id, m.t.fabric, m.t.weaving, status="WIP") == wip_before
        async with session.begin():
            assert (await reconcile_organization(session, m.org_id)).clean


class TestLotGenealogyFromProduction:
    async def _run(self, client, session, m: Mfg, lots: list[tuple[uuid.UUID, str]], out_lot):  # type: ignore[no-untyped-def]
        """Issue the named fabric lots to a 4-shirt order (2 m each) and return its id."""
        for lot, quantity in lots:
            await stock(session, m, m.t.fabric, quantity, lot=lot)
        await make_bom(client, m, m.shirt, "BOM-S", [(m.t.fabric, "2", "0")])
        order = await make_order(client, m, m.shirt, "4")
        material_id = order["materials"][0]["id"]
        await ok(
            client,
            m.org_id,
            "POST",
            f"/orders/{order['id']}/issue",
            json={
                "lines": [
                    {
                        "material_id": material_id,
                        "location_id": str(m.t.weaving),
                        "lot_id": str(lot),
                        "quantity": quantity,
                    }
                    for lot, quantity in lots
                ],
                "over_issue": True,
            },
        )
        return order["id"]

    async def test_one_input_lot_into_the_output_lot_is_a_derived_edge(
        self, client, session, m: Mfg
    ) -> None:
        fab = await make_lot(session, m, m.t.fabric, "FAB-1")
        out = await make_lot(session, m, m.shirt, "SHIRT-1")
        oid = await self._run(client, session, m, [(fab, "8")], out)
        await ok(
            client, m.org_id, "POST", f"/orders/{oid}/outputs",
            json={"quantity": "2", "lot_id": str(out)},
        )  # fmt: skip
        assert await edges(session, m.org_id) == {(fab, out, "DERIVED")}
        # Output recorded again from the same input lot: the edge is not duplicated, no 409.
        await ok(
            client, m.org_id, "POST", f"/orders/{oid}/outputs",
            json={"quantity": "2", "lot_id": str(out)},
        )  # fmt: skip
        assert await edges(session, m.org_id) == {(fab, out, "DERIVED")}
        async with session.begin():
            count = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.organization_id == m.org_id,
                        OutboxEvent.event_type == "operations.inventory.lot.linked",
                    )
                )
            ).all()
        assert len(count) == 1

    async def test_several_lots_of_one_item_blend_into_the_output_lot(
        self, client, session, m: Mfg
    ) -> None:
        a = await make_lot(session, m, m.t.fabric, "FAB-A")
        b = await make_lot(session, m, m.t.fabric, "FAB-B")
        out = await make_lot(session, m, m.shirt, "SHIRT-AB")
        oid = await self._run(client, session, m, [(a, "5"), (b, "3")], out)
        # 4 shirts = 8 m: all of A, then all of B.
        await ok(
            client, m.org_id, "POST", f"/orders/{oid}/outputs",
            json={"quantity": "4", "lot_id": str(out)},
        )  # fmt: skip
        assert await edges(session, m.org_id) == {(a, out, "BLEND"), (b, out, "BLEND")}
        # The lot trace walks from the shirt lot back to both fabric lots.
        principal = principal_for(m.org_id, ["operations.inventory.stock.read:ORG"])
        with as_principal(principal):
            trace = await client.get(f"/api/v1/operations/inventory/lots/{out}/trace")
        assert trace.status_code == 200, trace.text
        assert {n["number"] for n in trace.json()["ancestors"]} == {"FAB-A", "FAB-B"}

    async def test_no_output_lot_means_no_lot_edge_and_an_unchanged_production_record(
        self, client, session, m: Mfg
    ) -> None:
        fab = await make_lot(session, m, m.t.fabric, "FAB-1")
        oid = await self._run(client, session, m, [(fab, "8")], None)
        await ok(client, m.org_id, "POST", f"/orders/{oid}/outputs", json={"quantity": "2"})
        assert await edges(session, m.org_id) == set()
        trace = await ok(client, m.org_id, "GET", f"/orders/{oid}/genealogy")
        assert [g["parent_lot_id"] for g in trace] == [str(fab)]


class TestRollLocksPrecedeBalanceLocks:
    async def test_output_into_a_roll_waits_on_the_roll_before_it_locks_any_balance(
        self, client, session, m: Mfg, org_id
    ) -> None:
        """A merge or split holds a roll `FOR UPDATE` and then takes balances. `record_output`
        posts a consume move and then a receipt move; if the receipt's roll were only locked by
        the second move, the first would already hold the consumed bucket's balance while it
        waits — the reverse of the order everything else uses. Hold the output roll as a splitter
        would, start the output, and prove the request is parked on the roll holding *no* balance
        lock (a `NOWAIT` lock on the consumed WIP bucket still succeeds)."""
        fab_lot = await make_lot(session, m, m.t.fabric, "FAB-1")
        panel_lot = await make_lot(session, m, m.panel, "PANEL-1", track_roll=True)
        panel_roll = await make_roll(session, m, m.panel, panel_lot, "P-ROLL-1", "10")
        await stock(session, m, m.t.fabric, "50", lot=fab_lot)
        await make_bom(client, m, m.panel, "BOM-PANEL", [(m.t.fabric, "1", "0")])
        order = await make_order(client, m, m.panel, "10", output_location=m.t.weaving)
        oid = uuid.UUID(order["id"])
        await ok(client, m.org_id, "POST", f"/orders/{oid}/reserve", json={})
        await ok(client, m.org_id, "POST", f"/orders/{oid}/issue", json={})

        engine = create_async_engine(
            settings().app_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)

        def bind(s):  # type: ignore[no-untyped-def]
            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        try:
            async with maker() as holder, maker() as probe, maker() as worker:
                bind(holder)
                bind(probe)
                bind(worker)
                await holder.begin()
                await holder.execute(
                    text("SELECT id FROM rolls WHERE id = :r FOR UPDATE"), {"r": panel_roll}
                )

                async def output() -> None:
                    async with worker.begin():
                        await services.record_output(
                            worker,
                            m.t.ctx,
                            oid,
                            OutputIn(
                                quantity="10",
                                lot_id=panel_lot,
                                roll_id=panel_roll,
                                location_id=m.t.weaving,
                            ),
                        )

                task = asyncio.create_task(output())
                await asyncio.sleep(1.0)
                assert not task.done(), "the output should be parked on the held roll"

                await probe.begin()
                free = await probe.execute(
                    text(
                        "SELECT id FROM inventory_balances WHERE inventory_item_id = :i "
                        "AND status = 'WIP' FOR UPDATE NOWAIT"
                    ),
                    {"i": m.t.fabric},
                )
                assert free.all(), "the consumed WIP balance must not be locked yet"
                await probe.rollback()

                await holder.rollback()
                await asyncio.wait_for(task, timeout=30)
        finally:
            await engine.dispose()

        assert await bucket(
            session, m.org_id, m.panel, m.t.weaving, "AVAILABLE", panel_lot, panel_roll
        ) == (q("10"), 0)
        async with session.begin():
            assert (await reconcile_organization(session, m.org_id)).clean

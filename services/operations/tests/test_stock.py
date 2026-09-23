"""Stock movements through the item-level API — the ledger an item's `on_hand` is the sum of.

`on_hand` must never be writable except through a movement, and the invariant
(`on_hand == SUM(ledger quantity)`, ADR-0014) must hold after any mixed sequence of opening,
adjustment and count movements. Written against real PostgreSQL: the append-only privilege and the
row lock are database behaviour, not application convention.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.inventory import selectors as inv_selectors
from app.inventory import services as inv_services
from app.models import SCHEMA, LedgerEntry
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import as_principal, ctx_for, principal_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _org_session(org: uuid.UUID) -> tuple[AsyncSession, AsyncEngine]:
    """A second, independent connection bound to `org` — the concurrency test needs two real
    sessions racing each other rather than the one shared fixture session."""
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    @event.listens_for(session.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

    return session, engine


async def _sum_of_deltas(session: AsyncSession, item_id: uuid.UUID) -> int:
    """The item's ledger total, in whole units (every quantity this API posts is whole)."""
    stmt = select(func.coalesce(func.sum(LedgerEntry.quantity_milli), 0)).where(
        LedgerEntry.inventory_item_id == item_id
    )
    milli = int((await session.execute(stmt)).scalar_one())
    assert milli % 1000 == 0
    return milli // 1000


async def _history(session: AsyncSession, ctx, item_id: uuid.UUID) -> list:  # type: ignore[no-untyped-def]
    """The item's movement history as the API renders it, newest first."""
    rows = (await session.execute(inv_selectors.movements_stmt(ctx, item_id))).scalars().all()
    return [inv_selectors.movement_out(row) for row in rows]


class TestAdjustStock:
    async def test_a_delta_adjustment_raises_on_hand_and_records_a_movement(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            item = await inv_selectors.get_inventory_item(
                session, org_id, data["low_stock_item"].id, for_update=True
            )
            updated = await inv_services.adjust_stock(
                session, write, item, quantity_delta=10, reason="found"
            )
        assert updated.on_hand == 16

        rows = await _history(session, write, updated.id)
        assert rows[0].kind == "adjustment"
        assert rows[0].quantity_delta == 10
        assert rows[0].on_hand_after == 16
        assert rows[0].reason == "found"

    async def test_a_negative_result_is_refused_and_writes_nothing(
        self, session, org_id, data
    ) -> None:
        # Captured while the ORM object is fresh: the refused adjustment rolls the transaction
        # back, which expires the session's identity map, and a later attribute access on the
        # same object would trigger a lazy refresh outside the request greenlet.
        item_id = data["low_stock_item"].id
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        with pytest.raises(ConflictProblem, match="6 MT on hand, 0 MT reserved, so 6 MT available"):
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, org_id, item_id, for_update=True
                )
                assert item.on_hand == 6
                await inv_services.adjust_stock(
                    session, write, item, quantity_delta=-20, reason="damaged"
                )

        refreshed = await inv_selectors.get_inventory_item(session, org_id, item_id)
        assert refreshed is not None
        assert refreshed.on_hand == 6
        count = await _sum_of_deltas(session, item_id)
        # Only the seed's own opening movement remains — the refused adjustment wrote nothing.
        assert count == 6

    async def test_a_count_records_the_computed_delta_even_when_zero(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            item = await inv_selectors.get_inventory_item(
                session, org_id, data["low_stock_item"].id, for_update=True
            )
            updated = await inv_services.adjust_stock(
                session, write, item, counted_on_hand=6, occurred_on=date(2026, 9, 1)
            )
        assert updated.on_hand == 6
        assert updated.stocked_at == date(2026, 9, 1)

        rows = await _history(session, write, updated.id)
        assert rows[0].kind == "count"
        assert rows[0].quantity_delta == 0
        assert rows[0].on_hand_after == 6

    async def test_sum_of_deltas_equals_on_hand_after_a_mixed_sequence(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        item_id = data["low_stock_item"].id

        for kwargs in (
            {"quantity_delta": 20, "reason": "found"},
            {"quantity_delta": -5, "reason": "damaged"},
            {"counted_on_hand": 25},
            {"quantity_delta": -3, "reason": "consumed"},
        ):
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, org_id, item_id, for_update=True
                )
                await inv_services.adjust_stock(session, write, item, **kwargs)

        final = await inv_selectors.get_inventory_item(session, org_id, item_id)
        assert final is not None
        total = await _sum_of_deltas(session, item_id)
        assert final.on_hand == total

    async def test_movements_list_newest_first(self, session, org_id, data) -> None:
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        item_id = data["low_stock_item"].id
        for delta in (5, 7):
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, org_id, item_id, for_update=True
                )
                await inv_services.adjust_stock(
                    session, write, item, quantity_delta=delta, reason="found"
                )

        rows = await _history(session, write, item_id)
        deltas_newest_first = [r.quantity_delta for r in rows]
        assert deltas_newest_first[:2] == [7, 5]

    async def test_the_app_role_cannot_update_or_delete_a_movement(
        self, session, org_id, data
    ) -> None:
        write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
        async with session.begin():
            item = await inv_selectors.get_inventory_item(
                session, org_id, data["low_stock_item"].id, for_update=True
            )
            await inv_services.adjust_stock(session, write, item, quantity_delta=1, reason="found")
            rows = await _history(session, write, item.id)
            movement_id = rows[0].id

        with pytest.raises(Exception, match="permission denied"):
            async with session.begin():
                await session.execute(
                    LedgerEntry.__table__.update()
                    .where(LedgerEntry.id == uuid.UUID(movement_id))
                    .values(quantity_milli=0)
                )


class TestAdjustStockOverHttp:
    async def test_a_reader_without_stock_adjust_is_forbidden(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.item.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": 5, "reason": "found"},
            )
        assert response.status_code == 403

    async def test_a_writer_can_adjust_and_sees_the_new_balance(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": 10, "reason": "found"},
            )
        assert response.status_code == 201
        assert response.json()["on_hand"] == 16

    async def test_sending_both_delta_and_count_is_a_422(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": 5, "counted_on_hand": 5, "reason": "found"},
            )
        assert response.status_code == 422

    async def test_a_delta_without_reason_is_a_422(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": 5},
            )
        assert response.status_code == 422

    async def test_a_quantity_delta_beyond_the_ledger_ceiling_is_422(
        self, client, org_id, data
    ) -> None:
        # Captured once: a request through the shared session can expire the ORM object, and a
        # second access outside the request's own greenlet context would then fail to lazy-load.
        url = f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments"
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(url, json={"quantity_delta": 10**13, "reason": "found"})
        assert response.status_code == 422
        with as_principal(principal):
            response = await client.post(
                url, json={"quantity_delta": -(10**13), "reason": "damaged"}
            )
        assert response.status_code == 422

    async def test_a_counted_on_hand_beyond_the_ledger_ceiling_is_422(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"counted_on_hand": 10**13},
            )
        assert response.status_code == 422

    async def test_overdrawing_is_a_409(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.stock.adjust:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": -50, "reason": "damaged"},
            )
        assert response.status_code == 409

    async def test_cross_tenant_item_is_not_found(self, client, org_id, data) -> None:
        principal = principal_for(
            uuid.uuid4(), ["operations.inventory.stock.adjust:ORG"], subject_ref=uuid.uuid4()
        )
        with as_principal(principal):
            response = await client.post(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/adjustments",
                json={"quantity_delta": 5, "reason": "found"},
            )
        assert response.status_code == 404

    async def test_movement_history_over_http(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, ["operations.inventory.item.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            response = await client.get(
                f"/api/v1/operations/inventory/{data['low_stock_item'].id}/movements"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["kind"] == "opening"
        assert body["items"][0]["on_hand_after"] == 6


class TestConcurrentAdjustments:
    async def test_two_concurrent_adjustments_never_lose_an_update(
        self, org_id: uuid.UUID, data
    ) -> None:
        """Both adjustments target the same item; `FOR UPDATE` must serialise them so the final
        balance reflects both, rather than the second overwriting the first."""
        item_id = data["low_stock_item"].id

        async def _adjust(session: AsyncSession, delta: int) -> None:
            write = ctx_for(org_id, "operations.inventory.stock.adjust", Scope.ORG)
            async with session.begin():
                item = await inv_selectors.get_inventory_item(
                    session, org_id, item_id, for_update=True
                )
                assert item is not None
                await inv_services.adjust_stock(
                    session, write, item, quantity_delta=delta, reason="found"
                )

        s1, e1 = await _org_session(org_id)
        s2, e2 = await _org_session(org_id)
        try:
            await asyncio.gather(_adjust(s1, 5), _adjust(s2, 7))
        finally:
            await s1.close()
            await s2.close()
            await e1.dispose()
            await e2.dispose()

        # A fresh session to read the committed result.
        read_session, read_engine = await _org_session(org_id)
        try:
            async with read_session.begin():
                final = await inv_selectors.get_inventory_item(read_session, org_id, item_id)
                assert final is not None
                assert final.on_hand == 6 + 5 + 7
                total = await _sum_of_deltas(read_session, item_id)
                assert final.on_hand == total
        finally:
            await read_session.close()
            await read_engine.dispose()

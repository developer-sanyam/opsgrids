"""A PO deleted between the route's load and `_lock_po` must read as not found under the lock."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA
from app.schemas import PurchaseOrderPatch
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import NotFoundProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

LINES = [
    {
        "sku": "RM-STL-4501",
        "description": "Cold-rolled steel coil",
        "quantity": 10,
        "uom": "MT",
        "unit_price_minor": 100_00,
    }
]


@pytest.mark.parametrize("action", ["delete", "approve", "cancel", "update"])
async def test_a_po_deleted_after_it_was_loaded_is_not_found_under_the_lock(
    session, org_id, data, action
) -> None:
    write = ctx_for(org_id, "operations.procurement.purchase_order.write", Scope.ORG)
    approve = ctx_for(org_id, "operations.procurement.purchase_order.approve", Scope.ORG)
    async with session.begin():
        po = await services.raise_purchase_order(
            session,
            write,
            supplier_id=data["meridian"].id,
            warehouse="Pune Plant",
            expected_date=date.today() + timedelta(days=7),
            lines=LINES,
        )
    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    other = async_sessionmaker(engine, expire_on_commit=False)()

    @event.listens_for(other.sync_session, "after_begin")
    def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
        connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

    try:
        async with other.begin():
            stale = await selectors.get_purchase_order(other, org_id, po.id)
            assert stale is not None
            async with session.begin():
                live = await selectors.get_purchase_order(session, org_id, po.id)
                assert live is not None
                await services.delete_purchase_order(session, write, live)
            with pytest.raises(NotFoundProblem):
                if action == "delete":
                    await services.delete_purchase_order(other, write, stale)
                elif action == "approve":
                    await services.approve_purchase_order(other, approve, stale)
                elif action == "cancel":
                    await services.cancel_purchase_order(other, write, stale, reason="x")
                else:
                    await services.update_purchase_order(
                        other, write, stale, PurchaseOrderPatch(notes="x")
                    )
    finally:
        await other.close()
        await engine.dispose()

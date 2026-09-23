"""Ledger ↔ balance reconciliation (ADR-0014 §3): detect, record, surface — never repair.

Drift is manufactured the only way it can happen once the kernel is the sole writer: by a
bypassing role editing a balance behind the ledger's back (a bad manual fix, a restore gone wrong).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.cron import ReconcileSweep
from app.inventory import ledger
from app.inventory.ledger import MoveLine
from app.inventory.reconcile import EVT_INCONSISTENCY, reconcile_organization
from app.models import SCHEMA, InventoryDiscrepancy, OutboxEvent
from platform_core.db import TENANT_GUC
from tests.helpers import as_principal, principal_for
from tests.inventory_fixtures import TODAY, Textile, balance, build_textile, q

pytestmark = pytest.mark.integration

RECONCILE = "operations.inventory.stock.reconcile"


@pytest.fixture
async def t(session, org_id) -> Textile:
    t = await build_textile(session, org_id)
    async with session.begin():
        await ledger.post_move(
            session,
            t.ctx,
            kind="receipt",
            lines=[MoveLine(t.fabric, t.weaving, q("82.5")), MoveLine(t.dye, t.weaving, q("14.8"))],
            occurred_on=TODAY,
        )
        await ledger.reserve(
            session, t.ctx, item_id=t.fabric, location_id=t.weaving, quantity_milli=q("20")
        )
    return t


async def tamper(admin_session, sql: str, **params) -> None:  # type: ignore[no-untyped-def]
    async with admin_session.begin():
        await admin_session.execute(text(sql), params)


async def run(session, org_id):  # type: ignore[no-untyped-def]
    async with session.begin():
        return await reconcile_organization(session, org_id)


class TestDetection:
    async def test_a_healthy_ledger_reconciles_clean(self, session, t) -> None:
        report = await run(session, t.org_id)
        assert report.clean
        assert report.buckets_checked == 2

    async def test_a_balance_edited_behind_the_ledger_is_found_and_not_repaired(
        self, session, admin_session, t
    ) -> None:
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 90000 "
            "WHERE inventory_item_id = :i",
            i=t.fabric,
        )
        report = await run(session, t.org_id)
        [finding] = report.findings
        assert (finding.kind, finding.stored_milli, finding.expected_milli) == (
            "balance",
            q("90"),
            q("82.5"),
        )
        assert finding.new
        # Recorded, evented — and the balance is exactly as wrong as it was.
        assert await balance(session, t.org_id, t.fabric, t.weaving) == (q("90"), q("20"))
        async with session.begin():
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == EVT_INCONSISTENCY)
                    )
                )
                .scalars()
                .all()
            )
        assert len(events) == 1
        assert events[0].payload["stored"] == "90"
        assert events[0].payload["expected"] == "82.5"

    async def test_a_second_run_refreshes_the_open_discrepancy_instead_of_duplicating(
        self, session, admin_session, t
    ) -> None:
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 90000 "
            "WHERE inventory_item_id = :i",
            i=t.fabric,
        )
        first = await run(session, t.org_id)
        second = await run(session, t.org_id)
        assert first.findings[0].discrepancy_id == second.findings[0].discrepancy_id
        assert not second.findings[0].new
        async with session.begin():
            rows = (await session.execute(select(InventoryDiscrepancy))).scalars().all()
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == EVT_INCONSISTENCY)
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1 and len(events) == 1

    async def test_reserved_drift_is_its_own_finding(self, session, admin_session, t) -> None:
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET reserved_milli = 0 "
            "WHERE inventory_item_id = :i",
            i=t.fabric,
        )
        [finding] = (await run(session, t.org_id)).findings
        assert (finding.kind, finding.stored_milli, finding.expected_milli) == (
            "reserved",
            0,
            q("20"),
        )

    async def test_a_ledger_bucket_with_no_balance_row_is_drift(
        self, session, admin_session, t
    ) -> None:
        await tamper(
            admin_session,
            "DELETE FROM operations.inventory_balances WHERE inventory_item_id = :i",
            i=t.dye,
        )
        [finding] = (await run(session, t.org_id)).findings
        assert (finding.inventory_item_id, finding.stored_milli, finding.expected_milli) == (
            t.dye,
            0,
            q("14.8"),
        )

    async def test_another_organizations_drift_is_not_this_ones(
        self, session, admin_session, t
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        async with admin_session.begin():
            await ledger.post_move(
                admin_session,
                other.ctx,
                kind="receipt",
                lines=[MoveLine(other.fabric, other.weaving, q("5"))],
                occurred_on=TODAY,
            )
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 1 "
            "WHERE organization_id = :o",
            o=other.org_id,
        )
        assert (await run(session, t.org_id)).clean


class TestNightlySweep:
    async def test_the_sweep_discovers_every_organization_and_checks_each_as_its_own_tenant(
        self, session, admin_session, t
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        async with admin_session.begin():
            await ledger.post_move(
                admin_session,
                other.ctx,
                kind="receipt",
                lines=[MoveLine(other.fabric, other.weaving, q("5"))],
                occurred_on=TODAY,
            )
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 1 "
            "WHERE organization_id = :o",
            o=other.org_id,
        )

        connect = {"server_settings": {"search_path": f"{SCHEMA},public"}}
        # Discovery as the real outbox role: it may read `organization_id` and nothing else.
        discovery = create_async_engine(settings().outbox_dsn, connect_args=connect)
        work = create_async_engine(settings().app_dsn, connect_args=connect)
        work_maker = async_sessionmaker(work, expire_on_commit=False)

        @asynccontextmanager
        async def scope(org_id):  # type: ignore[no-untyped-def]
            async with work_maker() as s, s.begin():
                await s.execute(
                    text("SELECT set_config(:g, :o, true)"), {"g": TENANT_GUC, "o": str(org_id)}
                )
                yield s

        try:
            sweep = ReconcileSweep(
                discovery_factory=async_sessionmaker(discovery), work_scope=scope
            )
            report = await sweep.tick()
        finally:
            await discovery.dispose()
            await work.dispose()

        assert report.organizations == 2
        assert report.failed == 0
        assert report.new_findings == 1  # the other org's tampered bucket, and only that

    async def test_the_discovery_role_cannot_read_stock(self, t) -> None:
        engine = create_async_engine(
            settings().outbox_dsn,
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with engine.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text("SELECT quantity_milli FROM inventory_balances"))
        finally:
            await engine.dispose()


class TestOverHttp:
    async def test_reconcile_and_resolve(self, client, session, admin_session, t) -> None:
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 90000 "
            "WHERE inventory_item_id = :i",
            i=t.fabric,
        )
        with as_principal(principal_for(t.org_id, [f"{RECONCILE}:ORG"])):
            ran = await client.post("/api/v1/operations/inventory/reconcile")
            assert ran.status_code == 200, ran.text
            body = ran.json()
            assert (body["findings"], body["new_findings"]) == (1, 1)
            [found] = body["discrepancies"]
            assert (found["stored_qty"], found["expected_qty"]) == ("90", "82.5")

            listed = await client.get("/api/v1/operations/inventory/discrepancies")
            assert listed.json()["total"] == 1

            resolved = await client.post(
                f"/api/v1/operations/inventory/discrepancies/{found['id']}/resolve",
                json={"note": "Manual edit during stock move; corrected by count ADJ-12"},
            )
            assert resolved.status_code == 200
            assert resolved.json()["resolved_at"] is not None
            again = await client.post(
                f"/api/v1/operations/inventory/discrepancies/{found['id']}/resolve",
                json={"note": "twice"},
            )
            assert again.status_code == 409
            assert (await client.get("/api/v1/operations/inventory/discrepancies")).json()[
                "total"
            ] == 0

    async def test_an_inventory_reader_cannot_reconcile_or_see_discrepancies(
        self, client, t
    ) -> None:
        reader = principal_for(
            t.org_id,
            ["operations.inventory.item.read:ORG", "operations.inventory.stock.adjust:ORG"],
        )
        with as_principal(reader):
            assert (await client.post("/api/v1/operations/inventory/reconcile")).status_code == 403
            assert (
                await client.get("/api/v1/operations/inventory/discrepancies")
            ).status_code == 403

    async def test_an_inventory_reader_cannot_resolve_a_discrepancy(self, client, t) -> None:
        reader = principal_for(
            t.org_id,
            ["operations.inventory.item.read:ORG", "operations.inventory.stock.adjust:ORG"],
        )
        with as_principal(reader):
            response = await client.post(
                f"/api/v1/operations/inventory/discrepancies/{uuid.uuid4()}/resolve",
                json={"note": "not my call"},
            )
        # The permission check runs before the discrepancy is looked up, so a made-up id still
        # proves the authz gate rather than a 404 racing ahead of it.
        assert response.status_code == 403

    async def test_another_organizations_discrepancy_is_not_found(
        self, client, session, admin_session, t
    ) -> None:
        other = await build_textile(admin_session, uuid.uuid4())
        async with admin_session.begin():
            await ledger.post_move(
                admin_session,
                other.ctx,
                kind="receipt",
                lines=[MoveLine(other.fabric, other.weaving, q("5"))],
                occurred_on=TODAY,
            )
        await tamper(
            admin_session,
            "UPDATE operations.inventory_balances SET quantity_milli = 1 "
            "WHERE organization_id = :o",
            o=other.org_id,
        )
        async with admin_session.begin():
            await admin_session.execute(
                text("SELECT set_config(:g, :o, true)"),
                {"g": TENANT_GUC, "o": str(other.org_id)},
            )
            [theirs] = (await reconcile_organization(admin_session, other.org_id)).findings

        with as_principal(principal_for(t.org_id, [f"{RECONCILE}:ORG"])):
            listed = await client.get("/api/v1/operations/inventory/discrepancies")
            assert listed.json()["total"] == 0
            probe = await client.post(
                f"/api/v1/operations/inventory/discrepancies/{theirs.discrepancy_id}/resolve",
                json={"note": "not mine"},
            )
            assert probe.status_code == 404

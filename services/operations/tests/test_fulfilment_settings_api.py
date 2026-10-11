"""`GET`/`PUT /operations/fulfilment/settings` (INVENTORY-02): the write path `fulfilment_settings`
never had. Negative authz, cross-tenant isolation, boundary validation and the audit row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AuditLog, FulfilmentSettings, Warehouse
from tests.helpers import as_principal, principal_for
from tests.order_fixtures import build_shop

pytestmark = pytest.mark.integration

URL = "/api/v1/operations/fulfilment/settings"
READ = "operations.orders.order.read:ORG"
WRITE = "operations.fulfilment.settings.write:ORG"


def body(**overrides: object) -> dict[str, object]:
    return {
        "invoice_timing": "on_delivery",
        "payment_defaults": {"manual": "credit", "walk_in": "cash"},
        "offer_timeout_seconds": 120,
        "offer_rounds": 2,
        "require_pickup_code": True,
        "default_tax_rate_pct": "18.00",
        "auto_assignment_enabled": True,
        "task_sla_minutes": 45,
        "alert_email": "ops@example.com",
        "alert_phone": "+91 98765 43210",
        **overrides,
    }


async def get(client, org_id, perms, params=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, perms)):
        return await client.get(URL, params=params or {})


async def put(client, org_id, perms, payload, params=None):  # type: ignore[no-untyped-def]
    with as_principal(principal_for(org_id, perms)):
        return await client.put(URL, json=payload, params=params or {})


async def _other_org_world(admin_session) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    """Another tenant with its own warehouse and its own settings row."""
    other = uuid.uuid4()
    async with admin_session.begin():
        wh = Warehouse(organization_id=other, code="OTHER", name="Other", location="")
        admin_session.add(wh)
        await admin_session.flush()
        admin_session.add(
            FulfilmentSettings(
                organization_id=other,
                warehouse_id=None,
                invoice_timing="on_delivery",
                payment_defaults={"manual": "credit"},
                offer_timeout_seconds=999,
                offer_rounds=9,
                require_pickup_code=True,
                default_tax_rate_pct=Decimal("28.00"),
                task_sla_minutes=999,
                alert_email="other@example.com",
            )
        )
    return other, wh.id


class TestAuthz:
    async def test_read_without_permission_is_403(self, client, org_id) -> None:
        resp = await get(client, org_id, [])
        assert resp.status_code == 403, resp.text

    async def test_write_without_permission_is_403(self, client, session, org_id) -> None:
        # Reading orders is not enough to change how they are billed.
        resp = await put(client, org_id, [READ], body())
        assert resp.status_code == 403, resp.text
        async with session.begin():
            rows = (await session.execute(select(FulfilmentSettings))).scalars().all()
        assert rows == []

    async def test_write_needs_org_scope(self, client, org_id) -> None:
        resp = await put(client, org_id, ["operations.fulfilment.settings.write:OWN"], body())
        assert resp.status_code == 403, resp.text


class TestDefaults:
    async def test_a_fresh_org_reads_defaults_that_do_not_dead_end_handover(
        self, client, org_id
    ) -> None:
        resp = await get(client, org_id, [READ])
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["source"] == "default"
        assert out["invoice_timing"] == "on_delivery"
        assert out["warehouse_id"] is None
        assert out["updated_at"] is None


class TestWrite:
    async def test_put_upserts_the_org_row_and_audits_it(self, client, session, org_id) -> None:
        first = await put(client, org_id, [WRITE, READ], body())
        assert first.status_code == 200, first.text
        out = first.json()
        assert out["source"] == "organization"
        assert out["default_tax_rate_pct"] == "18.00"
        assert out["payment_defaults"] == {"manual": "credit", "walk_in": "cash"}
        assert out["alert_email"] == "ops@example.com"

        second = await put(client, org_id, [WRITE], body(task_sla_minutes=30, alert_email=None))
        assert second.status_code == 200, second.text

        read = await get(client, org_id, [READ])
        assert read.json()["task_sla_minutes"] == 30
        assert read.json()["alert_email"] is None

        async with session.begin():
            rows = (await session.execute(select(FulfilmentSettings))).scalars().all()
            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "fulfilment.settings.updated")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1 and rows[0].organization_id == org_id
        assert len(audits) == 2
        assert all(a.organization_id == org_id for a in audits)

    async def test_a_warehouse_override_is_its_own_row(self, client, session, org_id) -> None:
        shop = await build_shop(session, org_id)
        wh = {"warehouse_id": str(shop.warehouse_id)}

        inherited = await get(client, org_id, [READ], wh)
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()["source"] == "default"

        await put(client, org_id, [WRITE], body(task_sla_minutes=10))
        assert (await get(client, org_id, [READ], wh)).json()["source"] == "organization"

        resp = await put(client, org_id, [WRITE], body(task_sla_minutes=20), wh)
        assert resp.status_code == 200, resp.text
        assert resp.json()["source"] == "warehouse"
        assert resp.json()["warehouse_id"] == str(shop.warehouse_id)

        assert (await get(client, org_id, [READ], wh)).json()["task_sla_minutes"] == 20
        assert (await get(client, org_id, [READ])).json()["task_sla_minutes"] == 10


class TestCrossTenant:
    async def test_another_orgs_row_is_never_read(self, client, org_id, admin_session) -> None:
        await _other_org_world(admin_session)
        resp = await get(client, org_id, [READ])
        assert resp.status_code == 200
        assert resp.json()["source"] == "default"
        assert resp.json()["offer_rounds"] != 9

    async def test_another_orgs_row_is_never_written(self, client, org_id, admin_session) -> None:
        other, _ = await _other_org_world(admin_session)
        resp = await put(client, org_id, [WRITE], body(offer_rounds=1))
        assert resp.status_code == 200, resp.text
        async with admin_session.begin():
            theirs = (
                await admin_session.execute(
                    select(FulfilmentSettings).where(FulfilmentSettings.organization_id == other)
                )
            ).scalar_one()
        assert theirs.offer_rounds == 9

    async def test_another_orgs_warehouse_is_a_404(self, client, org_id, admin_session) -> None:
        _, their_wh = await _other_org_world(admin_session)
        params = {"warehouse_id": str(their_wh)}
        assert (await get(client, org_id, [READ], params)).status_code == 404
        assert (await put(client, org_id, [WRITE], body(), params)).status_code == 404


class TestValidation:
    @pytest.mark.parametrize("timing", ["on_accept", "on_dispatch"])
    async def test_an_invoice_before_handover_timing_is_refused(
        self, client, session, org_id, timing
    ) -> None:
        # Order invoicing is not wired yet (INVENTORY-01): choosing a timing that needs an
        # invoice before handover would refuse every handover again.
        resp = await put(client, org_id, [WRITE], body(invoice_timing=timing))
        assert resp.status_code == 422, resp.text
        assert any(e.get("field") == "invoice_timing" for e in resp.json()["errors"])
        async with session.begin():
            assert (await session.execute(select(FulfilmentSettings))).first() is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"invoice_timing": "whenever"},
            {"payment_defaults": {"manual": "barter"}},
            {"payment_defaults": {"telepathy": "cash"}},
            {"offer_timeout_seconds": 0},
            {"offer_rounds": 0},
            {"offer_rounds": 1000},
            {"task_sla_minutes": 0},
            {"default_tax_rate_pct": "-1"},
            {"default_tax_rate_pct": "100.01"},
            {"default_tax_rate_pct": "5.123"},
            {"alert_email": "not-an-email"},
            {"alert_phone": "call me"},
            {"surprise": True},
        ],
    )
    async def test_bad_input_is_a_422(self, client, org_id, overrides) -> None:
        resp = await put(client, org_id, [WRITE], body(**overrides))
        assert resp.status_code == 422, resp.text

    async def test_every_field_is_required(self, client, org_id) -> None:
        payload = body()
        del payload["require_pickup_code"]
        resp = await put(client, org_id, [WRITE], payload)
        assert resp.status_code == 422, resp.text

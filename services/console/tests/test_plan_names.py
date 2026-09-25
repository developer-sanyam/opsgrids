"""Plan and product display names on `GET /admin/subscriptions` and the pricing composite.

A bare `plan_version_id` is meaningless on a staff screen. The names come from catalog, and the
two properties that matter are *how many calls* and *what happens when catalog is down*:

1. **Never an N+1** — the subscription list resolves every plan name in one batch call per 200
   distinct versions (plus one for the products' public names), however many rows it renders.
2. **Best-effort** — catalog being unreachable turns the names into `null`; the list, which is
   otherwise built entirely from console's own schema, still renders.
3. **The pricing composite gets its names for free** — the pinned plan's name rides the plan-version
   document that screen already fetches for the price; no extra call.
4. **Authz is unchanged** — the list keeps `owner`/`billing_ops`, and a refused caller reaches
   catalog with nothing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgCommercialState
from tests.helpers import Downstream, as_principal, operator_principal

pytestmark = pytest.mark.integration


def _names(*rows: tuple[uuid.UUID, str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "versions": [
                {
                    "plan_version_id": str(version_id),
                    "plan_id": str(uuid.uuid4()),
                    "plan_name": plan_name,
                    "product_key": product_key,
                }
                for version_id, plan_name, product_key in rows
            ]
        },
    )


def _public_plans(*rows: tuple[str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "plans": [
                {
                    "product_key": key,
                    "product_name": name,
                    "plan_id": str(uuid.uuid4()),
                    "plan_version_id": str(uuid.uuid4()),
                    "amount_minor": 100000,
                    "currency": "INR",
                    "billing_interval": "month",
                }
                for key, name in rows
            ]
        },
    )


def _state(
    org_id: uuid.UUID, entries: dict[str, dict[str, object]], **extra: object
) -> OrgCommercialState:
    return OrgCommercialState(
        organization_id=org_id,
        mrr_minor=0,
        subscription_states=entries,
        **extra,
    )


class TestSubscriptionListNames:
    async def test_rows_carry_plan_and_product_names_from_two_catalog_calls(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        pv_hrms, pv_crm = uuid.uuid4(), uuid.uuid4()
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _state(
                        org_id,
                        {
                            "hrms": {"state": "active", "plan_version_id": str(pv_hrms)},
                            "crm": {"state": "trial", "plan_version_id": str(pv_crm)},
                        },
                    ),
                    _state(
                        org_b,
                        {"hrms": {"state": "active", "plan_version_id": str(pv_hrms)}},
                    ),
                ]
            )
        downstream = Downstream(
            {
                "/internal/v1/plan-versions": _names(
                    (pv_hrms, "Growth", "hrms"), (pv_crm, "Starter", "crm")
                ),
                "/internal/v1/public-flat-plans": _public_plans(
                    ("hrms", "People"), ("crm", "Sales")
                ),
            }
        )
        downstream.install()

        with as_principal(operator_principal(roles=["billing_ops"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 200, response.text
        rows = response.json()["subscriptions"]
        by_key = {(r["organization_id"], r["product_key"]): r for r in rows}
        hrms = by_key[(str(org_id), "hrms")]
        assert hrms["plan_name"] == "Growth" and hrms["product_name"] == "People"
        crm = by_key[(str(org_id), "crm")]
        assert crm["plan_name"] == "Starter" and crm["product_name"] == "Sales"
        assert by_key[(str(org_b), "hrms")]["plan_name"] == "Growth"

        # Three subscriptions, two distinct versions: ONE batch call naming exactly those two,
        # plus one public-plans call — never a call per row.
        batch = [c for c in downstream.calls if c.url.path == "/internal/v1/plan-versions"]
        assert len(batch) == 1
        assert sorted(batch[0].url.params.get_list("ids")) == sorted([str(pv_hrms), str(pv_crm)])
        assert len(downstream.calls) == 2

    async def test_an_entry_with_no_pin_gets_null_names_not_a_lookup(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A cancelled tombstone keeps no plan_version_id — nothing to name, nothing to ask."""
        async with admin_session.begin():
            admin_session.add(_state(org_id, {"hrms": {"state": "cancelled"}}))
        downstream = Downstream({})
        downstream.install()

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 200
        row = response.json()["subscriptions"][0]
        assert row["plan_name"] is None and row["plan_version_id"] is None
        assert downstream.calls == [], "no pins anywhere means no catalog call at all"

    async def test_no_subscriptions_at_all_is_an_empty_list_and_no_catalog_call(
        self, client: AsyncClient
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 200
        assert response.json()["subscriptions"] == []
        assert downstream.calls == []

    async def test_catalog_being_down_nulls_the_names_and_the_list_still_renders(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        pv = uuid.uuid4()
        async with admin_session.begin():
            admin_session.add(
                _state(
                    org_id,
                    {
                        "hrms": {
                            "state": "active",
                            "plan_version_id": str(pv),
                            "amount_minor": 50000,
                            "currency": "INR",
                        }
                    },
                )
            )
        Downstream({}).install()  # every catalog path -> 503

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 200, response.text
        row = response.json()["subscriptions"][0]
        assert row["plan_name"] is None and row["product_name"] is None
        assert row["amount_minor"] == 50000 and row["state"] == "active"

    async def test_more_than_one_chunk_of_versions_is_still_a_handful_of_calls(
        self, client: AsyncClient, admin_session: AsyncSession
    ) -> None:
        versions = [uuid.uuid4() for _ in range(205)]
        async with admin_session.begin():
            admin_session.add_all(
                [
                    _state(
                        uuid.uuid4(),
                        {"hrms": {"state": "active", "plan_version_id": str(pv)}},
                        org_created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    )
                    for pv in versions
                ]
            )

        def _answer(request: httpx.Request) -> httpx.Response:
            ids = [uuid.UUID(i) for i in request.url.params.get_list("ids")]
            return _names(*[(i, f"Plan {i.hex[:4]}", "hrms") for i in ids])

        downstream = Downstream(
            {
                "/internal/v1/plan-versions": _answer,
                "/internal/v1/public-flat-plans": _public_plans(("hrms", "People")),
            }
        )
        downstream.install()

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 200, response.text
        assert all(r["plan_name"] for r in response.json()["subscriptions"])
        batches = [c for c in downstream.calls if c.url.path == "/internal/v1/plan-versions"]
        assert sorted(len(c.url.params.get_list("ids")) for c in batches) == [5, 200]

    async def test_support_may_not_read_and_reaches_catalog_with_nothing(
        self, client: AsyncClient
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/subscriptions")
        assert response.status_code == 403
        assert downstream.calls == []

    async def test_a_product_token_gets_404_and_an_unauthenticated_call_401(
        self, client: AsyncClient
    ) -> None:
        with as_principal(operator_principal(staff=False)):
            assert (await client.get("/api/v1/admin/subscriptions")).status_code == 404
        assert (await client.get("/api/v1/admin/subscriptions")).status_code == 401


class TestPricingCompositeNames:
    def _wire(
        self,
        org_id: uuid.UUID,
        *,
        pinned: uuid.UUID,
        plan_name: str | None,
        version_status: int = 200,
    ) -> Downstream:
        version_doc = {
            "id": str(pinned),
            "plan_id": str(uuid.uuid4()),
            "product_key": "hrms",
            "plan_name": plan_name,
            "version_no": 1,
            "state": "published",
            "billing_interval": "month",
            "currency": "INR",
            "restricted_to_org_id": None,
            "prices": [{"kind": "flat", "amount_minor": 80000, "currency": "INR"}],
            "features": [],
            "seat_classes": [],
        }
        downstream = Downstream(
            {
                f"/organizations/{org_id}/subscriptions": httpx.Response(
                    200,
                    json=[
                        {
                            "id": str(uuid.uuid4()),
                            "product_key": "hrms",
                            "plan_version_id": str(pinned),
                            "state": "active",
                            "trial_ends_at": None,
                            "current_period_start": "2026-09-01T00:00:00+00:00",
                            "current_period_end": "2026-10-01T00:00:00+00:00",
                        }
                    ],
                ),
                f"/plan-versions/{pinned}": httpx.Response(
                    version_status, json=version_doc if version_status == 200 else {}
                ),
                "/public-flat-plans": _public_plans(("hrms", "People")),
                f"/directory/organizations/{org_id}": httpx.Response(
                    200,
                    json={
                        "organization_id": str(org_id),
                        "slug": "acme",
                        "name": "Acme",
                        "status": "active",
                        "country": "IN",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                ),
            }
        )
        downstream.install()
        return downstream

    async def test_the_pinned_and_public_plan_names_are_on_the_composite(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        pinned = uuid.uuid4()
        downstream = self._wire(org_id, pinned=pinned, plan_name="Growth")

        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert response.status_code == 200, response.text
        product = response.json()["products"][0]
        assert product["product_name"] == "People"
        assert product["subscription"]["plan_name"] == "Growth"
        assert product["current_price"]["plan_name"] == "Growth"
        assert product["public_price"]["plan_name"] == "People"

        # Names ride the documents the screen already fetched: one plan-version read, no batch.
        assert [c.url.path for c in downstream.calls].count(
            f"/internal/v1/plan-versions/{pinned}"
        ) == 1
        assert "/internal/v1/plan-versions" not in [c.url.path for c in downstream.calls]

    async def test_a_version_catalog_no_longer_knows_has_no_name_and_the_page_still_renders(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        self._wire(org_id, pinned=uuid.uuid4(), plan_name="Growth", version_status=404)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert response.status_code == 200, response.text
        product = response.json()["products"][0]
        assert product["subscription"]["plan_name"] is None
        assert product["current_price"] is None
        assert product["public_price"]["plan_name"] == "People"

    async def test_an_older_catalog_that_sends_no_plan_name_is_a_null_not_a_failure(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """`plan_name` is additive on catalog's side — a document without it (a rolling deploy,
        catalog upgraded last) must degrade to `null`, not break the composite."""
        self._wire(org_id, pinned=uuid.uuid4(), plan_name=None)
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert response.status_code == 200
        assert response.json()["products"][0]["current_price"]["plan_name"] is None

    async def test_the_composite_only_ever_reads_the_named_organizations_subscriptions(
        self, client: AsyncClient, org_id: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        downstream = self._wire(org_id, pinned=uuid.uuid4(), plan_name="Growth")
        with as_principal(operator_principal(roles=["owner"])):
            await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        paths = [c.url.path for c in downstream.calls]
        assert not any(str(org_b) in p for p in paths)
        assert f"/internal/v1/organizations/{org_id}/subscriptions" in paths

    async def test_a_product_token_gets_404_and_reaches_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(staff=False)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/pricing")
        assert response.status_code == 404
        assert downstream.calls == []

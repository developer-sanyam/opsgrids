"""A trial ends, the first invoice is raised, and payment tries to collect it — the real stack.

Before the billing run nothing ever moved a trial: `trial_ends_at` passed, the activation's
read-time expiry switched the product off, and no invoice was ever issued. Each service's suite
was green, because each did what its own contract said and nobody called the next one. This
journey is the contract *between* them, end to end:

    1  the owner starts a trial of Inventory                  201, state `trial`
    2  the organization has a usable mandate                  (seeded; no mandate route yet)
    3  the trial ends and the billing run passes              subscription → billing → catalog,
                                                              subscription → payment
    4  the subscription is `active` on its first paid period  period opens at the trial's end
    5  billing holds exactly one finalized invoice for it     priced off the pinned version
    6  payment raised exactly one charge against it           billing relay → payment consumer
    7  running the billing run again changes nothing          no second invoice, no second charge
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx
import pytest
from test_journey import auth_header, login

PRODUCT = "operations"
#: Past the longest trial a plan version may grant by default (14 days) — the pass must see it
#: as ended however the version's `trial_days` is set.
FIFTEEN_DAYS = 15 * 24 * 3600


@pytest.fixture(scope="session")
def billing_journey() -> dict[str, Any]:
    return {}


def _token(client: httpx.Client, tenant: dict[str, Any]) -> str:
    return login(client, tenant["owner"]["email"], tenant["password"], tenant["org_id"])


def _mine(client: httpx.Client, token: str) -> dict[str, Any]:
    response = client.get("/api/v1/subscriptions", headers=auth_header(token))
    assert response.status_code == 200, response.text[:300]
    return next(s for s in response.json() if s["product_key"] == PRODUCT)


class TestBillingRunJourney:
    """Ordered. Each step depends on the one before it."""

    def test_01_the_owner_starts_a_trial(
        self, client: httpx.Client, billing_tenant: dict[str, Any], billing_journey: dict
    ) -> None:
        token = _token(client, billing_tenant)
        headers = auth_header(token)
        plans = client.get("/api/v1/plans", params={"product_key": PRODUCT}, headers=headers)
        assert plans.status_code == 200, plans.text[:300]
        version = next(
            v
            for plan in plans.json()
            for v in plan["versions"]
            if v["state"] == "published" and v["restricted_to_org_id"] is None
        )
        response = client.post(
            "/api/v1/subscriptions",
            headers=headers,
            json={"product_key": PRODUCT, "plan_version_id": version["id"], "trial_days": 14},
        )
        assert response.status_code == 201, response.text[:400]
        body = response.json()
        assert body["state"] == "trial" and body["trial_ends_at"]
        billing_journey.update(
            token=token,
            subscription_id=body["id"],
            trial_ends_at=body["trial_ends_at"],
            currency=version["currency"],
            expected_total=sum(
                p["amount_minor"] or 0
                for p in version["prices"]
                if p["kind"] in ("flat", "per_seat")
            ),
        )
        assert billing_journey["expected_total"] > 0, "the journey needs a plan that costs money"

    def test_02_the_organization_has_a_usable_mandate(
        self, billing_tenant: dict[str, Any], billing_journey: dict, seed_mandate: Any
    ) -> None:
        billing_journey["mandate_id"] = seed_mandate(
            billing_tenant["org_id"], billing_journey["currency"], 10**9
        )

    def test_03_the_trial_ends_and_the_billing_run_passes(
        self, billing_journey: dict, billing_run: Any
    ) -> None:
        report = billing_run(FIFTEEN_DAYS)
        # Platform-wide counts include other journeys' tenants; assert only that the pass ran
        # and issued something. Step 4 onwards asserts about this tenant, off its own rows.
        assert report["considered"] >= 1, report
        assert report["issued"] >= 1, report
        billing_journey["report"] = report

    def test_04_the_subscription_is_active_on_its_first_paid_period(
        self, client: httpx.Client, billing_tenant: dict[str, Any], billing_journey: dict
    ) -> None:
        mine = _mine(client, _token(client, billing_tenant))
        assert mine["state"] == "active", (
            f"expected the trial to convert with a usable mandate, got {mine['state']}: {mine}"
        )
        assert datetime.fromisoformat(mine["current_period_start"]) == datetime.fromisoformat(
            billing_journey["trial_ends_at"]
        ), "the first paid period must open at the trial's end, not when the sweep ran"
        assert mine["dunning"] is None

    def test_05_billing_holds_exactly_one_finalized_invoice(
        self, billing_tenant: dict[str, Any], billing_journey: dict, commerce_probe: Any
    ) -> None:
        billed = commerce_probe("billing", billing_tenant["org_id"])
        invoices = [
            i
            for i in billed["invoices"]
            if i["subscription_id"] == billing_journey["subscription_id"]
        ]
        assert len(invoices) == 1, billed
        (invoice,) = invoices
        assert invoice["state"] == "finalized"
        assert invoice["currency"] == billing_journey["currency"]
        assert invoice["total_minor"] == billing_journey["expected_total"]
        assert invoice["idempotency_key"].startswith(
            f"subscription:{billing_journey['subscription_id']}:"
        )
        billing_journey["invoice_id"] = invoice["id"]

    def test_06_payment_raised_exactly_one_charge_for_it(
        self, billing_tenant: dict[str, Any], billing_journey: dict, commerce_probe: Any
    ) -> None:
        """billing's relay publishes `commerce.invoice.finalized`; payment's consumer charges it."""
        deadline = time.monotonic() + 60.0
        charges: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            charges = [
                c
                for c in commerce_probe("payment", billing_tenant["org_id"])["charges"]
                if c["invoice_id"] == billing_journey["invoice_id"]
            ]
            if charges:
                break
            time.sleep(0.5)
        billed = commerce_probe("billing", billing_tenant["org_id"])
        assert charges, (
            "payment never charged the invoice. finalized events published by billing's relay: "
            f"{billed['finalized_published']}"
        )
        (charge,) = charges
        assert charge["amount_minor"] == billing_journey["expected_total"]
        assert charge["currency"] == billing_journey["currency"]
        assert charge["idempotency_key"] == f"invoice:{billing_journey['invoice_id']}"
        assert charge["product_key"] == PRODUCT
        assert charge["state"] in ("authorized", "created"), charge

    def test_07_running_it_again_changes_nothing(
        self,
        client: httpx.Client,
        billing_tenant: dict[str, Any],
        billing_journey: dict,
        billing_run: Any,
        commerce_probe: Any,
    ) -> None:
        billing_run(FIFTEEN_DAYS)
        billing_run(FIFTEEN_DAYS)
        invoices = [
            i
            for i in commerce_probe("billing", billing_tenant["org_id"])["invoices"]
            if i["subscription_id"] == billing_journey["subscription_id"]
        ]
        assert [i["id"] for i in invoices] == [billing_journey["invoice_id"]]
        time.sleep(2.0)  # give a (wrong) second finalized event time to reach payment
        charges = commerce_probe("payment", billing_tenant["org_id"])["charges"]
        assert len(charges) == 1, charges
        assert _mine(client, _token(client, billing_tenant))["state"] == "active"

"""Buy the trio, and get the three products it names — through the real stack.

Until this slice a bundle was a price with nothing behind it. A `suite/trio` subscription
emitted `product_key: "suite"`; product-service projected an activation for `suite`, a key no
gateway route checks; account-service looked for a `suite` manifest and logged that there was
none. Every suite of unit tests was green, because each service did exactly what its own
contract said. The defect was the contract *between* them, which is the only kind this suite
exists to catch.

    1  a fresh owner has none of the trio's products        402 on every member
    2  the owner is refused; the platform team sells it     403, then 201 over /internal
    3  the event crosses three services                     outbox → projection → manifests
    4  a fresh token carries the members, not the bundle    ent ⊇ {hrms, crm, finance}, no suite
    5  each member answers 200; a non-member still 402s     the gateway, end to end
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from _http import auth_header, owner_token, token_claims

TRIO = ["hrms", "crm", "finance"]

#: One cheap, permission-light read per member, through the gateway prefix that product gates.
MEMBER_READS = {
    "hrms": "/api/v1/employees",
    "crm": "/api/v1/customers",
    "finance": "/api/v1/finance/info",
}
#: Not in the trio: PulseBoard owns `/api/v1/projects` at the gateway.
NON_MEMBER_READ = "/api/v1/projects"


@pytest.fixture(scope="session")
def bundle_journey() -> dict[str, Any]:
    return {}


def _wait_for_the_fan_out(event_probe: Any, org_id: str, *, timeout: float = 60.0) -> None:
    """Block until subscription's relay has published the lifecycle event and account's
    consumer has installed from it — the same two-hop, database-level wait the engage journey
    explains, for the same reason: a token minted before the owner's roles land is cached
    against the pre-grant permission version for a minute."""
    pattern = "commerce.subscription.%"
    deadline = time.monotonic() + timeout
    staged: dict[str, Any] = {}
    while time.monotonic() < deadline:
        staged = event_probe("subscription", "product", org_id, pattern)
        if staged.get("published", 0) >= 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            f"subscription-service never published a lifecycle event for {org_id}: {staged}"
        )
    published = {row["id"] for row in staged["rows"] if row["published"]}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processed = event_probe("account", "account", org_id, pattern)
        if published & set(processed.get("event_ids", [])):
            return
        time.sleep(0.5)
    raise AssertionError(
        f"account-service never processed {sorted(published)} — the members' manifests "
        "(and the owner's admin roles) never landed"
    )


def _wait_for_entitlement(client: httpx.Client, token: str, *, timeout: float = 60.0) -> None:
    """product-service's consumer is the third hop; wait until it says every member entitles."""
    deadline = time.monotonic() + timeout
    seen: dict[str, bool] = {}
    while time.monotonic() < deadline:
        response = client.get("/api/v1/products/activations", headers=auth_header(token))
        assert response.status_code == 200, response.text[:300]
        seen = {a["product_key"]: a["entitles"] for a in response.json()}
        if all(seen.get(key) for key in TRIO):
            return
        time.sleep(0.5)
    raise AssertionError(f"the trio's members never all entitled: {seen}")


class TestBundleJourney:
    """Ordered. Each step depends on the one before it."""

    def test_01_a_fresh_owner_is_refused_every_member(
        self, client: httpx.Client, bundle_tenant: dict[str, Any], bundle_journey: dict
    ) -> None:
        token = owner_token(client, bundle_tenant)
        for key, path in MEMBER_READS.items():
            response = client.get(path, headers=auth_header(token))
            assert response.status_code == 402, (
                f"{key} answered {response.status_code} before anything was bought — every "
                f"later step would pass for the wrong reason: {response.text[:200]}"
            )
        bundle_journey["token"] = token

    def test_02_the_owner_cannot_buy_a_bundle_but_the_platform_team_can_sell_one(
        self,
        client: httpx.Client,
        bundle_tenant: dict[str, Any],
        bundle_journey: dict,
        operator_call: Any,
    ) -> None:
        """Bundles are sales-led: the customer route refuses one with 403, and the operator path
        — a service token, the organization named, an accountable operator — sells it."""
        headers = auth_header(bundle_journey["token"])
        plans = client.get("/api/v1/plans", params={"product_key": "suite"}, headers=headers)
        assert plans.status_code == 200, plans.text[:300]
        trio = next(
            version
            for plan in plans.json()
            for version in plan["versions"]
            if version["state"] == "published"
            and version["restricted_to_org_id"] is None
            and {f["feature_key"]: f["value"] for f in version["features"]}.get("bundle_tier")
            == "trio"
        )

        refused = client.post(
            "/api/v1/subscriptions",
            headers=headers,
            json={"product_key": "suite", "plan_version_id": trio["id"], "trial_days": 365},
        )
        assert refused.status_code == 403, refused.text[:300]

        response = operator_call(
            "subscription",
            "/internal/v1/subscriptions",
            {
                "organization_id": bundle_tenant["org_id"],
                "product_key": "suite",
                "plan_version_id": trio["id"],
                "operator_id": "e2e-sales",
                # A member list in the body is ignored: the pinned version is the only authority.
                "entitled_products": ["hrms", "crm", "finance", "pulseboard"],
            },
        )
        assert response.status_code == 201, response.text[:400]
        body = response.json()
        assert body["entitled_products"] == TRIO
        bundle_journey["subscription_id"] = body["id"]

    def test_03_the_event_crosses_three_services(
        self,
        client: httpx.Client,
        bundle_tenant: dict[str, Any],
        bundle_journey: dict,
        event_probe: Any,
    ) -> None:
        _wait_for_the_fan_out(event_probe, bundle_tenant["org_id"])
        # The owner's admin roles landing bumped the permission version, so the token from step 1
        # is stale by design; read the activations with a fresh one.
        _wait_for_entitlement(client, owner_token(client, bundle_tenant))

    def test_04_a_fresh_token_carries_the_members_not_the_bundle(
        self, client: httpx.Client, bundle_tenant: dict[str, Any], bundle_journey: dict
    ) -> None:
        token = owner_token(client, bundle_tenant)
        ent = set(token_claims(token).get("ent", []))
        assert set(TRIO) <= ent, f"the token is missing trio members: {sorted(ent)}"
        assert "suite" not in ent, "the bundle's own key routes nowhere and must not be entitled"
        assert "pulseboard" not in ent, "a member list from the request body was honoured"
        bundle_journey["token"] = token

    def test_05_each_member_answers_and_a_non_member_still_402s(
        self, client: httpx.Client, bundle_journey: dict
    ) -> None:
        headers = auth_header(bundle_journey["token"])
        for key, path in MEMBER_READS.items():
            response = client.get(path, headers=headers)
            assert response.status_code == 200, (
                f"{key} is in the trio and answered {response.status_code}: {response.text[:300]}"
            )
        refused = client.get(NON_MEMBER_READ, headers=headers)
        assert refused.status_code == 402, (
            f"a product outside the trio answered {refused.status_code}: {refused.text[:200]}"
        )

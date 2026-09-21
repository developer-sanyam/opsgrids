"""Every product the marketplace offers as available has a plan a customer can be sold.

Activation is a subscription to a plan version (ADR-0013 §1). A product registered as
`available` in product-service with no published plan in catalog-service is shown with a buy
button that can never succeed — which is exactly how Engage shipped answering 402 on every
screen. The two facts live in different schemas owned by different services, so no unit test in
either can see the gap; only the stack, migrated as production is, can.
"""

from __future__ import annotations

from typing import Any

import httpx
from test_journey import auth_header, login


def test_every_available_product_has_a_published_plan(
    client: httpx.Client, tenants: dict[str, Any]
) -> None:
    alpha = tenants["alpha"]
    token = login(client, alpha["owner"]["email"], tenants["password"], alpha["org_id"])
    headers = auth_header(token)

    response = client.get("/api/v1/products", params={"available_only": "true"}, headers=headers)
    assert response.status_code == 200, response.text
    sellable = [p["key"] for p in response.json() if not p["is_platform"]]
    assert sellable, "the marketplace offers nothing — the product seed did not run"

    unbuyable = []
    for key in sellable:
        plans = client.get("/api/v1/plans", params={"product_key": key}, headers=headers)
        assert plans.status_code == 200, f"{key}: {plans.status_code} {plans.text}"
        if not any(plan["versions"] for plan in plans.json()):
            unbuyable.append(key)

    assert not unbuyable, f"available but with no plan version to subscribe to: {unbuyable}"

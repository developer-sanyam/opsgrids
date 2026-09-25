"""`storefront.enquiry.created` (now carrying `status`/`lines`) and the new
`storefront.enquiry.status_changed` emission, built through the real write path and validated
against the JSON Schema committed for it under `libs/contracts/events/` (ADR-0020 slice I7,
`docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: drive the real write path
— here, the public submission and the merchant status-change routes, which is how these events
are actually produced — read the staged outbox row back on the BYPASSRLS reader, and check it
against the schema.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.models import OutboxEvent
from platform_core.events import validate_event_schema
from tests.helpers import all_perms, seed_category, seed_product, seed_store

pytestmark = pytest.mark.integration

#: `tests`, `storefront`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

PUBLIC_ENQUIRY = "/api/v1/storefront/public/enquiry"
ENQUIRIES = "/api/v1/storefront/enquiries"


def _payload(store: str, lines: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    return {
        "store": store,
        "name": "Priya Shah",
        "company": "Shah Traders",
        "email": "priya@shahtraders.example",
        "phone": "9876543210",
        "requirement": "Need a quote for 500 bags of rice.",
        "quantity": "500 bags",
        "lines": lines,
        **overrides,
    }


async def _last_payload(admin_session: Any, organization_id: uuid.UUID, event_type: str) -> dict:
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.organization_id == organization_id,
            OutboxEvent.event_type == event_type,
        )
        .order_by(OutboxEvent.created_at.desc())
    )
    row = (await admin_session.execute(stmt)).scalars().first()
    assert row is not None, f"no {event_type} event was staged"
    return dict(row.payload)


async def test_enquiry_created_carries_status_and_lines(
    client, admin_session, org_id: uuid.UUID, slug: str
) -> None:
    await seed_store(admin_session, org_id, slug, status="published")
    category = await seed_category(admin_session, org_id)
    product = await seed_product(admin_session, org_id, category.id)
    lines = [{"product_id": str(product.id), "name": "Anything", "quantity": 5}]

    response = await client.post(PUBLIC_ENQUIRY, json=_payload(slug, lines))
    assert response.status_code == 201

    payload = await _last_payload(admin_session, org_id, "storefront.enquiry.created")
    validate_event_schema("storefront.enquiry.created", 1, payload, contracts_root=CONTRACTS_ROOT)
    assert payload["status"] == "new"
    assert payload["lines"] == [
        {"product_id": str(product.id), "name": product.name, "quantity": 5}
    ]


async def test_enquiry_status_changed_is_now_emitted(
    client, as_principal, admin_session, org_id: uuid.UUID, slug: str
) -> None:
    await seed_store(admin_session, org_id, slug, status="published")
    category = await seed_category(admin_session, org_id)
    product = await seed_product(admin_session, org_id, category.id)
    lines = [{"product_id": str(product.id), "name": "Anything", "quantity": 5}]
    await client.post(PUBLIC_ENQUIRY, json=_payload(slug, lines))

    with as_principal(all_perms(org_id)):
        listed = await client.get(ENQUIRIES)
        enquiry_id = listed.json()["items"][0]["id"]
        response = await client.patch(f"{ENQUIRIES}/{enquiry_id}", json={"status": "contacted"})
    assert response.status_code == 200

    payload = await _last_payload(admin_session, org_id, "storefront.enquiry.status_changed")
    validate_event_schema(
        "storefront.enquiry.status_changed", 1, payload, contracts_root=CONTRACTS_ROOT
    )
    assert payload["from"] == "new"
    assert payload["to"] == "contacted"

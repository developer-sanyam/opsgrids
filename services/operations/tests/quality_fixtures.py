"""Shared set-up for the quality tests (inventory plan Phase 4): a small shop
(`tests.order_fixtures.build_shop`) plus the HTTP helpers every quality test drives templates and
inspections through — real write paths, no service-layer shortcuts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory import selectors as inv_selectors
from tests.helpers import as_principal, principal_for
from tests.order_fixtures import Shop, build_shop

READ = "operations.quality.inspection.read:ORG"
INSPECT = "operations.quality.inspection.inspect:ORG"
APPROVE = "operations.quality.inspection.approve:ORG"
REJECT = "operations.quality.inspection.reject:ORG"
ALL_PERMS = [READ, INSPECT, APPROVE, REJECT]

BASE = "/api/v1/operations/quality"


@dataclass
class QcShop:
    org_id: uuid.UUID
    shop: Shop
    location_id: uuid.UUID


async def build_qc_shop(session: AsyncSession, org_id: uuid.UUID, *, stock: int = 100) -> QcShop:
    shop = await build_shop(session, org_id, stock=stock)
    async with session.begin():
        location = await inv_selectors.default_location(session, org_id, shop.warehouse_id)
    assert location is not None
    return QcShop(org_id=org_id, shop=shop, location_id=location.id)


async def call(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    method: str,
    path: str,
    *,
    perms: list[str] | None = None,
    json: Any = None,
    key: str | None = None,
    params: dict[str, Any] | None = None,
):
    """One request as a principal holding `perms` (default: everything quality has)."""
    principal = principal_for(
        org_id, perms if perms is not None else ALL_PERMS, subject_ref=uuid.uuid4()
    )
    headers = {"Idempotency-Key": key} if key else {}
    with as_principal(principal):
        return await client.request(
            method, f"{BASE}{path}", json=json, headers=headers, params=params
        )


def template_body(code: str = "INC-FAB", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "name": "Incoming fabric",
        "kind": "incoming",
        "params": [
            {"name": "GSM", "kind": "numeric", "unit": "g/m2", "min": "170", "max": "190"},
            {
                "name": "Shrinkage",
                "kind": "numeric",
                "unit": "%",
                "max": "3.5",
                "is_critical": True,
            },
            {"name": "Colour match", "kind": "pass_fail"},
        ],
    }
    body.update(over)
    return body


async def make_template(client, org_id: uuid.UUID, **over: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    response = await call(client, org_id, "POST", "/templates", json=template_body(**over))
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def open_inspection(  # type: ignore[no-untyped-def]
    client, qc: QcShop, *, quantity: str = "40", item: uuid.UUID | None = None, **over: Any
):
    body: dict[str, Any] = {
        "kind": "incoming",
        "item_id": str(item or qc.shop.widget),
        "location_id": str(qc.location_id),
        "quantity": quantity,
    }
    body.update(over)
    return await call(client, qc.org_id, "POST", "/inspections", json=body)


async def open_ok(client, qc: QcShop, **kwargs: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    response = await open_inspection(client, qc, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def record(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, inspection_id: str, results: list[dict[str, Any]], **kw: Any
):
    return await call(
        client,
        org_id,
        "PUT",
        f"/inspections/{inspection_id}/results",
        json={"results": results},
        **kw,
    )


ALL_PASS = [
    {"param_name": "GSM", "value": "180"},
    {"param_name": "Shrinkage", "value": "2"},
    {"param_name": "Colour match", "passed": True},
]

"""Shared set-up for the counting tests (ADR-0026, `docs/fulfilment/plan.md` F8): a small shop
(`tests.order_fixtures.build_shop`) plus the HTTP helpers every counting test drives sessions and
lines through — real write paths, no service-layer shortcuts, the same reasoning
`fulfilment_fixtures.py` gives.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import as_principal, principal_for
from tests.order_fixtures import Shop, build_shop

COUNT_READ_PERM = "operations.inventory.count.read:ORG"
COUNT_MANAGE_PERM = "operations.inventory.count.manage:ORG"
COUNT_EXECUTE_PERM = "operations.inventory.count.execute:ORG"
COUNT_REVIEW_PERM = "operations.inventory.count.review:ORG"
ALL_COUNT_PERMS = [COUNT_READ_PERM, COUNT_MANAGE_PERM, COUNT_EXECUTE_PERM, COUNT_REVIEW_PERM]


@dataclass
class CountingShop:
    org_id: uuid.UUID
    shop: Shop


async def build_counting_shop(
    session: AsyncSession, org_id: uuid.UUID, *, stock: int = 100
) -> CountingShop:
    shop = await build_shop(session, org_id, stock=stock)
    return CountingShop(org_id=org_id, shop=shop)


async def open_session(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID,
    scope_kind: str = "WAREHOUSE",
    zone_location_id: uuid.UUID | None = None,
    location_ids: list[uuid.UUID] | None = None,
    label: str = "Q3 stocktake",
    perms: list[str] | None = None,
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_MANAGE_PERM], subject_ref=uuid.uuid4()
    )
    body: dict = {"warehouse_id": str(warehouse_id), "scope_kind": scope_kind, "label": label}
    if zone_location_id is not None:
        body["zone_location_id"] = str(zone_location_id)
    if location_ids is not None:
        body["location_ids"] = [str(x) for x in location_ids]
    with as_principal(principal):
        return await client.post("/api/v1/operations/counting/sessions", json=body)


async def open_session_ok(client, org_id: uuid.UUID, *, warehouse_id: uuid.UUID, **kwargs) -> dict:  # type: ignore[no-untyped-def]
    response = await open_session(client, org_id, warehouse_id=warehouse_id, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


async def list_lines(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, session_id: str, *, perms: list[str] | None = None
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_READ_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.get(f"/api/v1/operations/counting/sessions/{session_id}/lines")


async def add_line(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    session_id: str,
    *,
    inventory_item_id: uuid.UUID,
    location_id: uuid.UUID,
    counted: str,
    count_method: str = "manual",
    status: str = "AVAILABLE",
    lot_id: uuid.UUID | None = None,
    roll_id: uuid.UUID | None = None,
    perms: list[str] | None = None,
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_EXECUTE_PERM], subject_ref=uuid.uuid4()
    )
    body = {
        "inventory_item_id": str(inventory_item_id),
        "location_id": str(location_id),
        "status": status,
        "counted": counted,
        "count_method": count_method,
    }
    if lot_id is not None:
        body["lot_id"] = str(lot_id)
    if roll_id is not None:
        body["roll_id"] = str(roll_id)
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/counting/sessions/{session_id}/lines", json=body
        )


async def submit_count(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    session_id: str,
    line_id: str,
    *,
    counted: str,
    count_method: str = "manual",
    scanned_barcode: str | None = None,
    perms: list[str] | None = None,
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_EXECUTE_PERM], subject_ref=uuid.uuid4()
    )
    body = {"counted": counted, "count_method": count_method, "scanned_barcode": scanned_barcode}
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/counting/sessions/{session_id}/lines/{line_id}/count", json=body
        )


async def review_line(  # type: ignore[no-untyped-def]
    client,
    org_id: uuid.UUID,
    session_id: str,
    line_id: str,
    *,
    decision: str,
    note: str = "",
    perms: list[str] | None = None,
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_REVIEW_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/counting/sessions/{session_id}/lines/{line_id}/review",
            json={"decision": decision, "note": note},
        )


async def start_review(
    client, org_id: uuid.UUID, session_id: str, *, perms: list[str] | None = None
):  # type: ignore[no-untyped-def]
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_MANAGE_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/counting/sessions/{session_id}/review")


async def close_session(
    client, org_id: uuid.UUID, session_id: str, *, perms: list[str] | None = None
):  # type: ignore[no-untyped-def]
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_MANAGE_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(f"/api/v1/operations/counting/sessions/{session_id}/close")


async def cancel_session(  # type: ignore[no-untyped-def]
    client, org_id: uuid.UUID, session_id: str, *, reason: str = "", perms: list[str] | None = None
):
    principal = principal_for(
        org_id, perms if perms is not None else [COUNT_MANAGE_PERM], subject_ref=uuid.uuid4()
    )
    with as_principal(principal):
        return await client.post(
            f"/api/v1/operations/counting/sessions/{session_id}/cancel", json={"reason": reason}
        )

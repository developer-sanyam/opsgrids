"""operations' `GET /internal/v1/insights/export/purchase_orders` (ADR-0020 slice I5,
`docs/insights/api.md` §6.1) — the backfill runner's `operations.purchase_orders` entity
(`app/backfill.py`'s `ENTITY_REGISTRY`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import entity_export_client, services
from app.config import Settings
from app.entity_export_client import ExportEndpoint

AUDIENCE = "operations"
PATH = "/internal/v1/insights/export/purchase_orders"


class PurchaseOrderBackfillSource:
    """Implements `app.backfill.BackfillSource` for `operations.purchase_orders`."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> Any:
        from app.backfill import ExportPage

        endpoint = ExportEndpoint(
            service_url=settings.operations_service_url, audience=AUDIENCE, path=PATH
        )
        rows, next_cursor = await entity_export_client.fetch_page(
            settings, client, endpoint, organization_id=organization_id, cursor=cursor, limit=limit
        )
        return ExportPage(rows=rows, next_cursor=next_cursor)

    async def apply_page(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        rows: list[dict[str, Any]],
        run_id: uuid.UUID,
        page_requested_at: datetime,
    ) -> int:
        applied = 0
        for row in rows:
            result = await services.upsert_purchase_order_fact(
                session,
                organization_id=organization_id,
                purchase_order_id=uuid.UUID(str(row["purchase_order_id"])),
                number=str(row["number"]),
                status=str(row["status"]),
                supplier_ref=uuid.UUID(str(row["supplier_id"])),
                supplier_name=str(row["supplier_name"]),
                owner_ref=uuid.UUID(str(row["raised_by_id"])) if row.get("raised_by_id") else None,
                created_date=date.fromisoformat(row["created_date"]),
                expected_date=date.fromisoformat(row["expected_date"]),
                received_date=date.fromisoformat(row["received_date"])
                if row.get("received_date")
                else None,
                currency=str(row["currency"]),
                total_minor=int(row["total_minor"]),
                source_created_at=datetime.fromisoformat(row["created_at"]),
                source_updated_at=datetime.fromisoformat(row["updated_at"]),
                deleted_at=datetime.fromisoformat(row["deleted_at"])
                if row.get("deleted_at")
                else None,
                from_export=True,
                page_requested_at=page_requested_at,
                run_id=run_id,
            )
            if result == "applied":
                applied += 1
        return applied


SOURCE = PurchaseOrderBackfillSource()

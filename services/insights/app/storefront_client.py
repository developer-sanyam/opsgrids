"""storefront's `GET /internal/v1/insights/export/enquiries` (ADR-0020 slice I7,
`docs/insights/api.md` §6.1) — the backfill runner's `storefront.enquiries` entity
(`app/backfill.py`'s `ENTITY_REGISTRY`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import entity_export_client, services
from app.config import Settings
from app.entity_export_client import ExportEndpoint
from platform_core.logging import get_logger

log = get_logger("insights.storefront_client")

AUDIENCE = "storefront"
PATH = "/internal/v1/insights/export/enquiries"


class EnquiryBackfillSource:
    """Implements `app.backfill.BackfillSource` for `storefront.enquiries`."""

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
            service_url=settings.storefront_service_url, audience=AUDIENCE, path=PATH
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
            if not row.get("store_id"):
                # `enquiry_facts.store_ref` is `NOT NULL` — the export route joins to this
                # organization's storefront row, which today's one-storefront-per-organization
                # constraint makes essentially always present. Skipped rather than guessed; the
                # next weekly re-export retries once the storefront row exists.
                log.warning(
                    "insights.backfill.enquiry_missing_store",
                    organization_id=str(organization_id),
                    enquiry_id=str(row.get("enquiry_id")),
                )
                continue
            result = await services.upsert_enquiry_fact(
                session,
                organization_id=organization_id,
                enquiry_id=uuid.UUID(str(row["enquiry_id"])),
                store_ref=uuid.UUID(str(row["store_id"])),
                company=str(row["company"]),
                status=str(row["status"]),
                lines=list(row["lines"]),
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


SOURCE = EnquiryBackfillSource()

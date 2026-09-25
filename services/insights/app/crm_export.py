"""Backfill sources for deal-, lead- and customer-service's own exports (ADR-0020 §3, slices I3
and I4). Each is a `BackfillSource` (`app/backfill.py`) registered into `ENTITY_REGISTRY`:
`fetch_page` calls the source's `GET /internal/v1/insights/export/{entity}`
(`docs/insights/api.md` §6.1), and `apply_page` upserts the page under the export write rule
(`app/services.py`, data-model.md §1.1) — overwrite unless a fresher event already folded.

One shared page-fetch helper (`_fetch_export_page`) does the retried HTTP call every entity
needs identically; only the row shape and the fold differ, so only `apply_page` is written per
entity.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.backfill import ExportPage
from app.config import Settings
from platform_core.internal_auth import mint_service_token
from platform_core.logging import get_logger

log = get_logger("insights.crm_export")

CALLER = "insights"

#: ADR-0020 §3: "3 retries at 2/8/30 s" for one page's own HTTP read. `app/backfill.py`'s own
#: `_fetch_page_with_retry` already retries at this layer too — this is the same backoff
#: `account_client.py` uses, kept identical rather than relying on the caller's retry alone,
#: since a `BackfillSource.fetch_page` is also called directly in tests and by
#: `bin/backfill --verify`-shaped tooling without that wrapper.
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)


class ExportUnavailable(Exception):
    """The source's export route refused, or was unreachable after every retry."""


async def _fetch_export_page(
    *,
    base_url: str,
    entity_path: str,
    audience: str,
    settings: Settings,
    client: httpx.AsyncClient,
    organization_id: uuid.UUID,
    cursor: dict[str, str] | None,
    limit: int,
) -> ExportPage:
    """One page of `GET {base_url}/internal/v1/insights/export/{entity_path}`, retried."""
    url = f"{base_url}/internal/v1/insights/export/{entity_path}"
    headers = {"X-Internal-Token": mint_service_token(settings, audience=audience, caller=CALLER)}
    params: dict[str, Any] = {"organization_id": str(organization_id), "limit": limit}
    if cursor is not None:
        params["after_updated_at"] = cursor["after_updated_at"]
        params["after_id"] = cursor["after_id"]

    attempts = len(RETRY_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
            return ExportPage(rows=body["rows"], next_cursor=body.get("next"))
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                await asyncio.sleep(RETRY_BACKOFFS_SECONDS[attempt])
    raise ExportUnavailable(f"{entity_path} export is unavailable: {last_error}")


def _parse_dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


class DealSource:
    """`deal.deals` (I3)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_export_page(
            base_url=settings.deal_service_url,
            entity_path="deals",
            audience="deal",
            settings=settings,
            client=client,
            organization_id=organization_id,
            cursor=cursor,
            limit=limit,
        )

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
            written = await services.upsert_deal_fact_from_export(
                session,
                organization_id=organization_id,
                deal_id=uuid.UUID(str(row["id"])),
                name=str(row["name"]),
                stage=str(row["stage"]),
                value_minor=int(row["value_minor"]),
                currency=str(row["currency"]),
                owner_ref=uuid.UUID(str(row["owner_id"])),
                company_ref=uuid.UUID(str(row["company_id"])),
                close_date=(
                    datetime.fromisoformat(str(row["close_date"])).date()
                    if row.get("close_date")
                    else None
                ),
                probability=int(row["probability"]),
                closed_at=_parse_dt(row.get("closed_at")),
                deleted_at=_parse_dt(row.get("deleted_at")),
                source_created_at=datetime.fromisoformat(str(row["created_at"])),
                source_updated_at=datetime.fromisoformat(str(row["updated_at"])),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
            if written:
                applied += 1
        return applied


class LeadSource:
    """`lead.leads` (I4)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_export_page(
            base_url=settings.lead_service_url,
            entity_path="leads",
            audience="lead",
            settings=settings,
            client=client,
            organization_id=organization_id,
            cursor=cursor,
            limit=limit,
        )

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
            owner_raw = row.get("owner_id")
            written = await services.upsert_lead_fact_from_export(
                session,
                organization_id=organization_id,
                lead_id=uuid.UUID(str(row["id"])),
                status=str(row["status"]),
                source=str(row["source"]),
                owner_ref=uuid.UUID(str(owner_raw)) if owner_raw else None,
                converted_at=_parse_dt(row.get("converted_at")),
                deleted_at=_parse_dt(row.get("deleted_at")),
                source_created_at=datetime.fromisoformat(str(row["created_at"])),
                source_updated_at=datetime.fromisoformat(str(row["updated_at"])),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
            if written:
                applied += 1
        return applied


class CompanySource:
    """`customer.companies` (I4)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_export_page(
            base_url=settings.customer_service_url,
            entity_path="companies",
            audience="customer",
            settings=settings,
            client=client,
            organization_id=organization_id,
            cursor=cursor,
            limit=limit,
        )

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
            written = await services.upsert_company_fact_from_export(
                session,
                organization_id=organization_id,
                company_id=uuid.UUID(str(row["id"])),
                name=str(row["name"]),
                status=str(row["status"]),
                owner_ref=uuid.UUID(str(row["owner_id"])),
                deleted_at=_parse_dt(row.get("deleted_at")),
                source_created_at=datetime.fromisoformat(str(row["created_at"])),
                source_updated_at=datetime.fromisoformat(str(row["updated_at"])),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
            if written:
                applied += 1
        return applied

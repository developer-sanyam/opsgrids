"""Calling finance-service's insights export (ADR-0020 slice I2, `docs/insights/api.md` §6.1).

Three `BackfillSource` implementations — one per `docs/insights/data-model.md` §3 fact table —
registered into `app/backfill.py:ENTITY_REGISTRY` under `finance.invoices`, `finance.payments`
and `finance.purchases`. Each pages `GET /internal/v1/insights/export/{entity}` on finance, under
a service token minted for trusted caller `insights` (finance's own
`app/api/internal.py:TRUSTED_CALLER`), and applies every page through `app/services.py`'s own
`upsert_*_fact_from_export` — the **export** write rule (data-model.md §1.1), never the event
rule: a page is a point-in-time read, so it overwrites a fact unless an event already wrote it
after this page was requested.

Same retry shape as `app/account_client.py`: 5 s connect / 30 s read, 3 retries at 2/8/30 s
(ADR-0020 §3). A page that still fails after every retry raises, and `app/backfill.py`'s own
`run_backfill` marks that run `failed` for the next hour's retry — the same as any other source
being down.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import ExportPage
from app.config import Settings
from app.services import (
    upsert_invoice_fact_from_export,
    upsert_payment_fact_from_export,
    upsert_purchase_fact_from_export,
)
from platform_core.internal_auth import mint_service_token

FINANCE_AUDIENCE = "finance"
CALLER = "insights"

#: ADR-0020 §3: "3 retries at 2/8/30 s" for one page's own HTTP read.
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (2.0, 8.0, 30.0)


class FinanceExportError(Exception):
    """finance-service refused the export, or was unreachable after every retry."""


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "X-Internal-Token": mint_service_token(settings, audience=FINANCE_AUDIENCE, caller=CALLER)
    }


async def _fetch_page(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    entity: str,
    organization_id: uuid.UUID,
    cursor: dict[str, str] | None,
    limit: int,
) -> ExportPage:
    url = f"{settings.finance_service_url}/internal/v1/insights/export/{entity}"
    params: dict[str, Any] = {"organization_id": str(organization_id), "limit": limit}
    if cursor is not None:
        params["after_updated_at"] = cursor["after_updated_at"]
        params["after_id"] = cursor["after_id"]

    attempts = len(RETRY_BACKOFFS_SECONDS) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers=_headers(settings))
            response.raise_for_status()
            body = response.json()
            return ExportPage(rows=list(body["rows"]), next_cursor=body.get("next"))
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            last_error = exc
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                await asyncio.sleep(RETRY_BACKOFFS_SECONDS[attempt])
    raise FinanceExportError(f"finance {entity} export is unavailable: {last_error}")


def _optional_datetime(row: dict[str, Any], key: str) -> datetime | None:
    value = row.get(key)
    return datetime.fromisoformat(value) if value else None


class InvoicesExportSource:
    """`finance.invoices` — `invoice_facts` (data-model.md §3)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_page(
            settings,
            client,
            entity="invoices",
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
        for row in rows:
            await upsert_invoice_fact_from_export(
                session,
                organization_id=organization_id,
                id=uuid.UUID(str(row["id"])),
                number=str(row["number"]),
                document_type=str(row["document_type"]),
                status=str(row["status"]),
                issued_date=date.fromisoformat(row["issued_date"]),
                due_date=date.fromisoformat(row["due_date"]),
                currency=str(row["currency"]),
                subtotal_minor=int(row["subtotal_minor"]),
                tax_minor=int(row["tax_minor"]),
                total_minor=int(row["total_minor"]),
                customer_ref=uuid.UUID(str(row["customer_id"])),
                paid_at=_optional_datetime(row, "paid_at"),
                deleted_at=_optional_datetime(row, "deleted_at"),
                source_created_at=datetime.fromisoformat(row["created_at"]),
                source_updated_at=datetime.fromisoformat(row["updated_at"]),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
        return len(rows)


class PaymentsExportSource:
    """`finance.payments` — `payment_facts` (data-model.md §3)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_page(
            settings,
            client,
            entity="payments",
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
        for row in rows:
            await upsert_payment_fact_from_export(
                session,
                organization_id=organization_id,
                id=uuid.UUID(str(row["id"])),
                reference=str(row["reference"]),
                received_date=date.fromisoformat(row["received_date"]),
                method=str(row["method"]),
                currency=row.get("currency"),
                amount_minor=int(row["amount_minor"]),
                deleted_at=_optional_datetime(row, "deleted_at"),
                source_created_at=datetime.fromisoformat(row["created_at"]),
                source_updated_at=datetime.fromisoformat(row["updated_at"]),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
        return len(rows)


class PurchasesExportSource:
    """`finance.purchases` — `purchase_facts` (data-model.md §3)."""

    async def fetch_page(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        organization_id: uuid.UUID,
        cursor: dict[str, str] | None,
        limit: int,
    ) -> ExportPage:
        return await _fetch_page(
            settings,
            client,
            entity="purchases",
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
        for row in rows:
            await upsert_purchase_fact_from_export(
                session,
                organization_id=organization_id,
                id=uuid.UUID(str(row["id"])),
                number=str(row["number"]),
                document_type=str(row["document_type"]),
                document_date=date.fromisoformat(row["document_date"]),
                currency=str(row["currency"]),
                taxable_minor=int(row["taxable_minor"]),
                tax_minor=int(row["tax_minor"]),
                total_minor=int(row["total_minor"]),
                deleted_at=_optional_datetime(row, "deleted_at"),
                source_created_at=datetime.fromisoformat(row["created_at"]),
                source_updated_at=datetime.fromisoformat(row["updated_at"]),
                run_id=run_id,
                page_requested_at=page_requested_at,
            )
        return len(rows)

"""`/health/billing-drafts` — billing-run drafts the run never finalized, for an alarm to scrape.

A billing-run invoice is written as a `draft` at the subscription side's record step and
finalized — announced, and so charged — straight after (review round 2, M5). A draft that stays a
draft is a period nobody is charged for, and nothing errors: the subscription side's own probe
counts `finalize_pending` claims, but a claim that was lost or rolled back past its migration
(RUNBOOK, the `f5a7b9c1d3e6` downgrade) leaves the draft visible only here. `drafts_stale` is the
number to alert on (review round 3, m6).

Counted on the outbox engine (BYPASSRLS, SELECT on `invoices(state, created_at)` only — not even
an id or a tenant), because the NOBYPASSRLS app role with no tenant bound would count nothing.
**It returns counts and an age, never an id.** Like every `/health/*` route it is reachable on the
back network only, and it is not part of readiness.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from platform_core.db import get_outbox_engine

router = APIRouter(tags=["health"], include_in_schema=False)

#: A run draft is finalized within one pass of the run; an hour is many passes.
DRAFT_STALE_AFTER = timedelta(hours=1)


@router.get("/health/billing-drafts", summary="Un-finalized billing-run drafts — scrape this")
async def billing_drafts_health() -> dict[str, Any]:
    async with AsyncSession(get_outbox_engine()) as session, session.begin():
        return await selectors.billing_drafts_health(
            session, now=datetime.now(UTC), stale_after=DRAFT_STALE_AFTER
        )

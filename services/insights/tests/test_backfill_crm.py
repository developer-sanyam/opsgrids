"""Backfill resume for the I3/I4 entities — `deal.deals`, `lead.leads`, `customer.companies`
(`app/crm_export.py`), registered into `ENTITY_REGISTRY`. `test_backfill.py` already proves the
generic framework resumes a `FakeSource`; this proves the *real* `apply_page` (the export write
rule, data-model.md §1.1) lands facts correctly across a resumed, paged run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import ENTITY_REGISTRY, ExportPage, run_backfill
from app.config import settings as settings_factory
from app.crm_export import DealSource
from app.models import BackfillRun, DealFact

pytestmark = pytest.mark.integration


def _deal_row(
    deal_id: uuid.UUID,
    *,
    name: str,
    updated_at: datetime,
    owner_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(deal_id),
        "name": name,
        "company_id": str(company_id or uuid.uuid4()),
        "owner_id": str(owner_id or uuid.uuid4()),
        "value_minor": 100_00,
        "currency": "INR",
        "stage": "lead",
        "close_date": "2026-12-01",
        "probability": 20,
        "closed_at": None,
        "created_at": updated_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "deleted_at": None,
    }


def test_deal_deals_is_registered() -> None:
    assert "deal.deals" in ENTITY_REGISTRY
    assert "lead.leads" in ENTITY_REGISTRY
    assert "customer.companies" in ENTITY_REGISTRY


class TestDealSourceResume:
    async def test_a_failed_page_resumes_from_its_cursor_and_lands_every_row(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        work_scope: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = uuid.uuid4()
        async with work_scope(org_id) as scoped_session:
            scoped_session.add(
                BackfillRun(id=run_id, organization_id=org_id, entity="deal.deals", kind="initial")
            )

        deal_a, deal_b = uuid.uuid4(), uuid.uuid4()
        t0 = datetime(2026, 9, 1, tzinfo=UTC)
        t1 = datetime(2026, 9, 2, tzinfo=UTC)

        pages = {
            0: ExportPage(
                rows=[_deal_row(deal_a, name="Page 0 deal", updated_at=t0)],
                next_cursor={"after_updated_at": t0.isoformat(), "after_id": str(deal_a)},
            ),
            1: ExportPage(
                rows=[_deal_row(deal_b, name="Page 1 deal", updated_at=t1)], next_cursor=None
            ),
        }
        calls: list[int] = []

        source = DealSource()

        async def _fake_fetch_page(settings, client, *, organization_id, cursor, limit):
            index = 0 if cursor is None else 1
            calls.append(index)
            if index == 1 and len(calls) == 2:
                raise RuntimeError("simulated transient failure")
            return pages[index]

        monkeypatch.setattr(source, "fetch_page", _fake_fetch_page)

        now = datetime.now(UTC)
        first = await run_backfill(
            settings=settings_factory(),
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="deal.deals",
            source=source,
            start_cursor=None,
            client=httpx.AsyncClient(),
            now=now,
        )
        assert first.failed is True
        assert first.rows_upserted == 1

        refreshed = (
            await session.execute(
                select(BackfillRun)
                .where(BackfillRun.id == run_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert refreshed.status == "failed"
        assert refreshed.cursor == {"after_updated_at": t0.isoformat(), "after_id": str(deal_a)}

        # Only the first page's deal has landed so far.
        landed = (
            (await session.execute(select(DealFact).where(DealFact.organization_id == org_id)))
            .scalars()
            .all()
        )
        assert {row.id for row in landed} == {deal_a}

        # Resume: the retry does not fail this time.
        second = await run_backfill(
            settings=settings_factory(),
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="deal.deals",
            source=source,
            start_cursor=refreshed.cursor,
            client=httpx.AsyncClient(),
            now=datetime.now(UTC),
        )
        assert second.completed is True
        assert second.rows_upserted == 1  # only the resumed page, page 0 not re-applied

        final_rows = (
            (await session.execute(select(DealFact).where(DealFact.organization_id == org_id)))
            .scalars()
            .all()
        )
        assert {row.id for row in final_rows} == {deal_a, deal_b}

    async def test_a_stale_export_row_never_regresses_a_fresher_event(
        self, session: AsyncSession, org_id: uuid.UUID, work_scope: Any
    ) -> None:
        """The export write rule (data-model.md §1.1): a page's row yields to a fact an event
        already folded after this page was requested."""
        from app import services

        deal_id = uuid.uuid4()
        owner_id = uuid.uuid4()
        company_id = uuid.uuid4()
        page_requested_at = datetime.now(UTC) - timedelta(minutes=5)

        async with work_scope(org_id) as scoped_session:
            # An event folded *after* the export page was requested.
            await services.upsert_deal_fact_from_event(
                scoped_session,
                organization_id=org_id,
                deal_id=deal_id,
                name="Fresher, from an event",
                stage="qualified",
                value_minor=500_00,
                currency="INR",
                owner_ref=owner_id,
                company_ref=company_id,
                close_date=None,
                probability=40,
                closed_at=None,
                source_created_at=datetime.now(UTC),
                source_updated_at=datetime.now(UTC),
            )

        async with work_scope(org_id) as scoped_session:
            applied = await services.upsert_deal_fact_from_export(
                scoped_session,
                organization_id=org_id,
                deal_id=deal_id,
                name="Stale, from the export page",
                stage="lead",
                value_minor=100_00,
                currency="INR",
                owner_ref=owner_id,
                company_ref=company_id,
                close_date=None,
                probability=20,
                closed_at=None,
                deleted_at=None,
                source_created_at=datetime.now(UTC),
                source_updated_at=datetime.now(UTC) - timedelta(hours=1),
                run_id=uuid.uuid4(),
                page_requested_at=page_requested_at,
            )
        assert applied is False

        row = (await session.execute(select(DealFact).where(DealFact.id == deal_id))).scalar_one()
        assert row.name == "Fresher, from an event"

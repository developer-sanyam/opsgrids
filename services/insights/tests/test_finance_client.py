"""`app/finance_client.py`'s export sources (ADR-0020 slice I2, `docs/insights/api.md` §6.1):
the entities are registered, the fetch honours the keyset cursor and the trusted-caller token,
and a page applies through the export write rule. The backfill-resume case runs the real source
against `app/backfill.run_backfill`, the same shape `test_backfill.py`'s own resumability test
uses for a fake one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backfill import ENTITY_REGISTRY, run_backfill
from app.config import settings as settings_factory
from app.finance_client import InvoicesExportSource
from app.models import BackfillRun, InvoiceFacts

pytestmark = pytest.mark.integration


def test_entity_registry_has_all_three_finance_entities() -> None:
    assert set(ENTITY_REGISTRY) >= {"finance.invoices", "finance.payments", "finance.purchases"}


def _invoice_row(id_: str, *, updated_at: str, number: str = "INV-1") -> dict[str, object]:
    return {
        "id": id_,
        "number": number,
        "document_type": "invoice",
        "status": "sent",
        "issued_date": "2026-09-01",
        "due_date": "2026-09-16",
        "currency": "INR",
        "subtotal_minor": 100000,
        "tax_minor": 18000,
        "total_minor": 118000,
        "customer_id": str(uuid.uuid4()),
        "paid_at": None,
        "deleted_at": None,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


async def test_fetch_page_sends_the_organization_and_cursor_and_a_trusted_token() -> None:
    organization_id = uuid.uuid4()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["token"] = request.headers.get("X-Internal-Token")
        return httpx.Response(
            200,
            json={
                "rows": [_invoice_row("a", updated_at="2026-09-01T00:00:00+00:00")],
                "next": None,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = InvoicesExportSource()
    page = await source.fetch_page(
        settings_factory(),
        client,
        organization_id=organization_id,
        cursor={"after_updated_at": "2026-08-01T00:00:00+00:00", "after_id": "x"},
        limit=500,
    )
    assert seen["path"] == "/internal/v1/insights/export/invoices"
    assert seen["params"]["organization_id"] == str(organization_id)
    assert seen["params"]["after_updated_at"] == "2026-08-01T00:00:00+00:00"
    assert seen["params"]["after_id"] == "x"
    assert seen["token"]
    assert len(page.rows) == 1
    assert page.next_cursor is None


async def test_apply_page_writes_through_the_export_rule(
    session: AsyncSession, org_id: uuid.UUID, admin_session: AsyncSession
) -> None:
    source = InvoicesExportSource()
    invoice_id = str(uuid.uuid4())
    run_id = uuid.uuid4()
    async with session.begin():
        applied = await source.apply_page(
            session,
            organization_id=org_id,
            rows=[_invoice_row(invoice_id, updated_at="2026-09-01T00:00:00+00:00")],
            run_id=run_id,
            page_requested_at=datetime.now(UTC),
        )
    assert applied == 1
    row = (
        await admin_session.execute(
            select(InvoiceFacts).where(
                InvoiceFacts.organization_id == org_id, InvoiceFacts.id == uuid.UUID(invoice_id)
            )
        )
    ).scalar_one()
    assert row.number == "INV-1"
    assert row.last_seen_run == run_id


async def _create_run(work_scope, *, organization_id: uuid.UUID, entity: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    async with work_scope(organization_id) as scoped_session:
        scoped_session.add(
            BackfillRun(id=run_id, organization_id=organization_id, entity=entity, kind="initial")
        )
    return run_id


async def test_a_real_finance_backfill_resumes_from_its_cursor(
    session: AsyncSession, org_id: uuid.UUID, work_scope
) -> None:
    """The same resumability contract `test_backfill.py` proves with a fake source, run here
    against the real `InvoicesExportSource` and a stubbed finance export server."""
    run_id = await _create_run(work_scope, organization_id=org_id, entity="finance.invoices")

    first_id = str(uuid.uuid4())
    second_id = str(uuid.uuid4())
    pages = {
        "page-0": {
            "rows": [_invoice_row(first_id, updated_at="2026-09-01T00:00:00+00:00")],
            "next": {"after_updated_at": "2026-09-01T00:00:00+00:00", "after_id": first_id},
        },
    }
    calls = {"n": 0}

    def failing_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        after_id = request.url.params.get("after_id")
        if after_id is None:
            return httpx.Response(200, json=pages["page-0"])
        # A plain `RuntimeError`, never `httpx.HTTPError`/`KeyError`/`TypeError` — deliberately
        # skips both `finance_client._fetch_page`'s own retry-with-backoff loop and
        # `app/backfill.py:_fetch_page_with_retry`'s, the same trick `test_backfill.py`'s own
        # `FakeSource` uses, so this test pays no real seconds of sleep. The retry loops
        # themselves are exercised by `test_account_client.py`.
        raise RuntimeError("simulated outage on page 1")

    source = InvoicesExportSource()
    now = datetime.now(UTC)
    report = await run_backfill(
        settings_factory(),
        work_scope=work_scope,
        organization_id=org_id,
        run_id=run_id,
        entity="finance.invoices",
        source=source,
        start_cursor=None,
        client=httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
        now=now,
    )
    assert report.failed is True
    assert report.rows_upserted == 1

    refreshed = (
        await session.execute(
            select(BackfillRun)
            .where(BackfillRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.status == "failed"
    assert refreshed.cursor == {
        "after_updated_at": "2026-09-01T00:00:00+00:00",
        "after_id": first_id,
    }

    # Resume: page 1 no longer fails, starting from the persisted cursor.
    def resumed_handler(request: httpx.Request) -> httpx.Response:
        after_id = request.url.params.get("after_id")
        assert after_id == first_id  # resumed from the persisted cursor, not from the start
        return httpx.Response(
            200,
            json={
                "rows": [_invoice_row(second_id, updated_at="2026-09-02T00:00:00+00:00")],
                "next": None,
            },
        )

    second_report = await run_backfill(
        settings_factory(),
        work_scope=work_scope,
        organization_id=org_id,
        run_id=run_id,
        entity="finance.invoices",
        source=source,
        start_cursor=refreshed.cursor,
        client=httpx.AsyncClient(transport=httpx.MockTransport(resumed_handler)),
        now=datetime.now(UTC),
    )
    assert second_report.completed is True
    assert second_report.rows_upserted == 1  # only the resumed page, not page 0 again

    final = (
        await session.execute(
            select(BackfillRun)
            .where(BackfillRun.id == run_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert final.status == "done"
    assert final.rows_upserted == 2  # page 0 (1 row) + the resumed page (1 row)

    ids = {
        str(r.id)
        for r in (
            await session.execute(
                select(InvoiceFacts).where(InvoiceFacts.organization_id == org_id)
            )
        )
        .scalars()
        .all()
    }
    assert ids == {first_id, second_id}

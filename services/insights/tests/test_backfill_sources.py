"""The I5-I7 `BackfillSource`s (`app/operations_client.py`, `app/projects_client.py`,
`app/storefront_client.py`) against real PostgreSQL, through the real `run_backfill` framework
(ADR-0020 §3): a two-page export resumes correctly from a persisted cursor after a simulated
failure, and each page's rows land as version-guarded fact upserts — not a hand-rolled fake, the
actual `ENTITY_REGISTRY` entries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app import entity_export_client, projects_client
from app.backfill import run_backfill
from app.config import settings as settings_factory
from app.models import BackfillRun, TaskFacts

pytestmark = pytest.mark.integration


def _task_row(task_id: uuid.UUID, updated_at: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(task_id),
        "task_id": str(task_id),
        "project_id": str(uuid.uuid4()),
        "stage": "backlog",
        "title": "Backfilled card",
        "assignee_id": None,
        "priority": "medium",
        "due_date": None,
        "points": 2,
        "completed_at": None,
        "created_at": updated_at,
        "updated_at": updated_at,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


async def test_projects_backfill_resumes_from_its_persisted_cursor_after_a_failure(
    org_id: uuid.UUID, work_scope, admin_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_fetch_page_with_retry` retries a transport error 3 times with real backoff sleeps
    # (2/8/30 s, ADR-0020 §3) before giving up — zeroed here so the simulated failure below is
    # immediate, the same trick `test_account_client.py` uses for the same reason.
    monkeypatch.setattr(entity_export_client, "RETRY_BACKOFFS_SECONDS", (0.0, 0.0, 0.0))
    task_a, task_b = uuid.uuid4(), uuid.uuid4()
    page_1 = {
        "rows": [_task_row(task_a, "2026-09-01T00:00:00+00:00")],
        "next": {"after_updated_at": "2026-09-01T00:00:00+00:00", "after_id": str(task_a)},
    }
    page_2 = {"rows": [_task_row(task_b, "2026-09-02T00:00:00+00:00")], "next": None}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/insights/export/tasks"
        assert request.headers["X-Internal-Token"]
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=page_1)
        # The second page is only ever requested with the first page's own cursor — proves the
        # resumed run picked up exactly where the failed one left off, not from the start.
        assert request.url.params["after_id"] == str(task_a)
        raise httpx.ConnectError("simulated failure mid-run")

    run_id = uuid.uuid4()
    async with work_scope(org_id) as session:
        session.add(
            BackfillRun(
                id=run_id,
                organization_id=org_id,
                entity="projects.tasks",
                kind="initial",
                status="running",
            )
        )

    settings = settings_factory()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await run_backfill(
            settings,
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="projects.tasks",
            source=projects_client.SOURCE,
            start_cursor=None,
            client=client,
            now=datetime.now(UTC),
        )
    assert first.failed is True
    assert first.rows_upserted == 1  # only the first page landed before the failure

    row = (
        await admin_session.execute(select(TaskFacts).where(TaskFacts.id == task_a))
    ).scalar_one()
    assert row.title == "Backfilled card"

    # Resume: the runner's own drain loop reads the persisted cursor and passes it back in —
    # simulated here directly, the same as `BackfillRunner._drain_one` would after the next tick.
    stored_cursor = (
        await admin_session.execute(select(BackfillRun.cursor).where(BackfillRun.id == run_id))
    ).scalar_one()
    assert stored_cursor == page_1["next"]

    def resumed_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after_id"] == str(task_a)
        return httpx.Response(200, json=page_2)

    async with httpx.AsyncClient(transport=httpx.MockTransport(resumed_handler)) as client:
        second = await run_backfill(
            settings,
            work_scope=work_scope,
            organization_id=org_id,
            run_id=run_id,
            entity="projects.tasks",
            source=projects_client.SOURCE,
            start_cursor=stored_cursor,
            client=client,
            now=datetime.now(UTC),
        )
    assert second.completed is True
    assert second.rows_upserted == 1

    ids = set(
        (
            await admin_session.execute(
                select(TaskFacts.id).where(TaskFacts.id.in_([task_a, task_b]))
            )
        )
        .scalars()
        .all()
    )
    assert ids == {task_a, task_b}

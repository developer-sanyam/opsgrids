"""The hourly local capture for `open_tasks` (ADR-0020 §2, slice I6, `docs/insights/metrics.md`
§8): computed from this organization's own `task_facts`, already folded by the consumer — no
pulled read, unlike I5's stock capture (`app/stock_capture.py`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.models import TaskFacts

METRIC_OPEN_TASKS = "open_tasks"


class OpenTasksCapture:
    """Implements `app.snapshot.SnapshotCapture`."""

    metric = METRIC_OPEN_TASKS

    async def capture(
        self, session: AsyncSession, *, organization_id: uuid.UUID, day: date, now: datetime
    ) -> None:
        count = (
            await session.execute(
                select(func.count())
                .select_from(TaskFacts)
                .where(
                    TaskFacts.organization_id == organization_id,
                    TaskFacts.deleted_at.is_(None),
                    TaskFacts.stage != "done",
                )
            )
        ).scalar_one()
        await services.upsert_daily_snapshot(
            session,
            organization_id=organization_id,
            metric=METRIC_OPEN_TASKS,
            dimension="",
            day=day,
            value=count,
            currency=None,
            captured_at=now,
        )

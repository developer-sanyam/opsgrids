"""Every enriched `projects.board.task.*` event, built through the real write path, validated
against the JSON Schema committed for it under `libs/contracts/events/` (ADR-0020 slice I6,
`docs/insights/events.md` §6).

Same shape as `services/notification/tests/test_event_contracts.py`: drive the real
`app/services.py` function inside a real transaction, read the staged outbox row back, and check
it against the schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app import services
from app.models import OutboxEvent
from app.schemas import TaskUpdate
from platform_core.auth import Scope
from platform_core.events import validate_event_schema
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

#: `tests`, `projects`, `services`, ROOT.
CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "libs" / "contracts"

TASK_WRITE = "projects.board.task.write"


async def _last_payload(session: Any, event_type: str) -> dict[str, Any]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.created_at.desc())
    )
    async with session.begin():
        row = (await session.execute(stmt)).scalars().first()
        assert row is not None, f"no {event_type} event was staged"
        return dict(row.payload)


async def _validate(session: Any, event_type: str) -> None:
    payload = await _last_payload(session, event_type)
    validate_event_schema(event_type, 1, payload, contracts_root=CONTRACTS_ROOT)


async def test_task_created_and_updated_match_their_schema(session, org_id, data) -> None:
    write = ctx_for(org_id, TASK_WRITE, Scope.ORG)

    async with session.begin():
        task = await services.create_task(
            session,
            write,
            project_id=data["project_a"].id,
            title="Contract test task",
            assignee_id=data["member_a"].id,
            stage="backlog",
            priority="medium",
            points=2,
        )
    await _validate(session, services.EVT_TASK_CREATED)

    async with session.begin():
        task = await services.update_task(session, write, task, TaskUpdate(stage="done"))
    await _validate(session, services.EVT_TASK_UPDATED)
    assert task.completed_at is not None

    async with session.begin():
        await services.update_task(session, write, task, TaskUpdate(stage="in_progress"))
    await _validate(session, services.EVT_TASK_UPDATED)


async def test_task_created_directly_in_done_stamps_completed_at(session, org_id, data) -> None:
    write = ctx_for(org_id, TASK_WRITE, Scope.ORG)

    async with session.begin():
        task = await services.create_task(
            session,
            write,
            project_id=data["project_a"].id,
            title="Already-done import",
            stage="done",
        )
    await _validate(session, services.EVT_TASK_CREATED)
    assert task.completed_at is not None

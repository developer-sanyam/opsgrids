"""The internal API — service-to-service, never reachable from outside the cluster.

`GET /internal/v1/insights/export/tasks` — insights' backfill read (ADR-0020 slice I6,
`docs/insights/api.md` §6.1): a keyset page over every task in one organization, shaped like
`task.created`/`.updated`'s own full-state event payload.

Guarded by a short-lived service token addressed to `projects` by name
(`require_service_token`), narrowed further to caller `insights` — the same
`TRUSTED_CALLER`/`require_*_caller` shape `subscription`'s and `account`'s own internal modules
use for their own narrower routes. `set_tenant()` is the first statement, on the NOBYPASSRLS
app role, so row-level security confirms every row actually belongs to the named organization
rather than trusting the query filter alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.models import Task
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.logging import get_logger

log = get_logger("projects.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: insights is the only caller of this route — a cross-tenant task export is narrower than "any
#: service holding a projects-audience token" should reach.
TRUSTED_CALLER = "insights"


def require_insights_caller(request: Request) -> None:
    """The narrower caller check: valid and addressed to projects (`require_service_token`,
    applied to the whole router, already checked that) and issued by insights specifically."""
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


InsightsCallerDep = Annotated[None, Depends(require_insights_caller)]


def _task_row(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "task_id": str(task.id),
        "project_id": str(task.project_id),
        "stage": task.stage,
        "title": task.title,
        "assignee_id": str(task.assignee_id) if task.assignee_id else None,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "points": task.points,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "deleted_at": task.deleted_at.isoformat() if task.deleted_at else None,
    }


@router.get(
    "/insights/export/tasks",
    summary="Every task's tenant-scoped state, paginated — insights' backfill read",
)
async def tasks_export(
    session: SessionDep,
    _insights: InsightsCallerDep,
    request: Request,
    organization_id: Annotated[uuid.UUID, Query()],
    after_updated_at: Annotated[datetime | None, Query()] = None,
    after_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> dict[str, Any]:
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)

    rows = (
        (
            await session.execute(
                selectors.tasks_export_stmt(
                    organization_id=organization_id,
                    after_updated_at=after_updated_at,
                    after_id=after_id,
                    limit=limit,
                )
            )
        )
        .scalars()
        .all()
    )
    next_cursor = (
        {"after_updated_at": rows[-1].updated_at.isoformat(), "after_id": str(rows[-1].id)}
        if len(rows) == limit
        else None
    )
    return {"rows": [_task_row(task) for task in rows], "next": next_cursor}

"""projects' `GET /internal/v1/insights/export/tasks` (ADR-0020 slice I6,
`docs/insights/api.md` §6.1) — the backfill runner's `projects.tasks` entity
(`app/backfill.py`'s `ENTITY_REGISTRY`).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app import entity_export_client, services
from app.config import Settings
from app.entity_export_client import ExportEndpoint

AUDIENCE = "projects"
PATH = "/internal/v1/insights/export/tasks"


class TaskBackfillSource:
    """Implements `app.backfill.BackfillSource` for `projects.tasks`."""

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
            service_url=settings.projects_service_url, audience=AUDIENCE, path=PATH
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
            result = await services.upsert_task_fact(
                session,
                organization_id=organization_id,
                task_id=uuid.UUID(str(row["task_id"])),
                project_ref=uuid.UUID(str(row["project_id"])),
                title=str(row["title"]),
                stage=str(row["stage"]),
                priority=str(row["priority"]),
                points=int(row["points"]),
                due_date=date.fromisoformat(row["due_date"]) if row.get("due_date") else None,
                owner_ref=uuid.UUID(str(row["assignee_id"])) if row.get("assignee_id") else None,
                completed_at=datetime.fromisoformat(row["completed_at"])
                if row.get("completed_at")
                else None,
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


SOURCE = TaskBackfillSource()

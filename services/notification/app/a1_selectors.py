"""Reads for the automation workflow engine. Every query filters on `organization_id`; list
statements take the caller's `ScopeContext` and call `ctx.apply()`, the same as every other
selector in this service."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain
from app.a1_models import Automation, AutomationRun, AutomationRunStep
from platform_core.auth import ScopeContext

WINDOW_DAYS = 30


def automations_stmt(
    ctx: ScopeContext, *, status: str | None, module: str | None, q: str | None
) -> Select[Any]:
    stmt = select(Automation).where(
        Automation.organization_id == ctx.organization_id,
        Automation.deleted_at.is_(None),
    )
    stmt = ctx.apply(stmt, owner_col=Automation.created_by)
    if status:
        stmt = stmt.where(Automation.status == status)
    if module:
        stmt = stmt.where(Automation.module == module)
    if q:
        stmt = stmt.where(Automation.name.ilike(f"%{q.strip()}%"))
    return stmt.order_by(Automation.updated_at.desc(), Automation.id)


async def get_automation(
    session: AsyncSession, org_id: uuid.UUID, automation_id: uuid.UUID, *, lock: bool = False
) -> Automation | None:
    stmt = select(Automation).where(
        Automation.id == automation_id,
        Automation.organization_id == org_id,
        Automation.deleted_at.is_(None),
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def run_stats_30d(
    session: AsyncSession, org_id: uuid.UUID, automation_ids: list[uuid.UUID], *, now: datetime
) -> dict[uuid.UUID, tuple[int, int, int]]:
    """`(executions, completed, failed)` in the last 30 days, per automation, in one grouped
    query for the whole page."""
    if not automation_ids:
        return {}
    since = now - timedelta(days=WINDOW_DAYS)
    rows = (
        await session.execute(
            select(
                AutomationRun.automation_id,
                func.count(),
                func.count().filter(AutomationRun.state == "completed"),
                func.count().filter(AutomationRun.state == "failed"),
            )
            .where(
                AutomationRun.organization_id == org_id,
                AutomationRun.automation_id.in_(automation_ids),
                AutomationRun.started_at >= since,
            )
            .group_by(AutomationRun.automation_id)
        )
    ).all()
    return {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in rows}


def runs_stmt(ctx: ScopeContext, *, automation_id: uuid.UUID, state: str | None) -> Select[Any]:
    stmt = select(AutomationRun).where(
        AutomationRun.organization_id == ctx.organization_id,
        AutomationRun.automation_id == automation_id,
    )
    if state:
        stmt = stmt.where(AutomationRun.state == state)
    return stmt.order_by(AutomationRun.started_at.desc(), AutomationRun.id)


async def get_run(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID
) -> AutomationRun | None:
    return (
        await session.execute(
            select(AutomationRun).where(
                AutomationRun.id == run_id, AutomationRun.organization_id == org_id
            )
        )
    ).scalar_one_or_none()


async def steps_for_run(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID
) -> list[AutomationRunStep]:
    return list(
        (
            await session.execute(
                select(AutomationRunStep)
                .where(
                    AutomationRunStep.organization_id == org_id, AutomationRunStep.run_id == run_id
                )
                .order_by(AutomationRunStep.seq)
            )
        )
        .scalars()
        .all()
    )


async def names_for(
    session: AsyncSession, org_id: uuid.UUID, automation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not automation_ids:
        return {}
    rows = (
        await session.execute(
            select(Automation.id, Automation.name).where(
                Automation.organization_id == org_id, Automation.id.in_(automation_ids)
            )
        )
    ).all()
    return {r[0]: r[1] for r in rows}


async def stats(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Executions, per-day series, failures by reason and a per-automation table over
    `[start, end)`, from four grouped queries — none of them per automation."""
    in_range = and_(
        AutomationRun.organization_id == org_id,
        AutomationRun.started_at >= start,
        AutomationRun.started_at < end,
    )
    duration_ms = func.extract("epoch", AutomationRun.finished_at - AutomationRun.started_at) * 1000

    totals = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AutomationRun.state == "completed"),
                func.count().filter(AutomationRun.state == "failed"),
                func.avg(duration_ms).filter(AutomationRun.finished_at.is_not(None)),
            ).where(in_range)
        )
    ).one()

    day = func.date(func.timezone("UTC", AutomationRun.started_at))
    series_rows = (
        await session.execute(
            select(
                day,
                func.count().filter(AutomationRun.state == "completed"),
                func.count().filter(AutomationRun.state == "failed"),
            )
            .where(in_range)
            .group_by(day)
            .order_by(day)
        )
    ).all()

    reason_rows = (
        await session.execute(
            select(AutomationRun.failure_code, func.count())
            .where(in_range, AutomationRun.state == "failed")
            .group_by(AutomationRun.failure_code)
            .order_by(func.count().desc(), AutomationRun.failure_code)
        )
    ).all()

    per_rows = (
        await session.execute(
            select(
                AutomationRun.automation_id,
                func.count(),
                func.count().filter(AutomationRun.state == "completed"),
                func.count().filter(AutomationRun.state == "failed"),
                func.avg(duration_ms).filter(AutomationRun.finished_at.is_not(None)),
                func.max(AutomationRun.started_at),
            )
            .where(in_range)
            .group_by(AutomationRun.automation_id)
            .order_by(func.count().desc(), AutomationRun.automation_id)
            .limit(200)
        )
    ).all()
    return {"totals": totals, "series": series_rows, "reasons": reason_rows, "per": per_rows}


# ----------------------------------------------------------------------------- triggers


def find_contracts_dir(start: Path) -> Path | None:
    """The repository's `libs/contracts/events`, found by walking up from `start`, or None.

    The runtime image holds only this service (`/app`), not the repository, so the directory does
    not exist there and the walk ends at the filesystem root: the trigger catalogue then lists
    events without their variables, which is what "best effort" means for it. Never index a fixed
    number of parents: a shallow install path has fewer than that.
    """
    for parent in start.resolve().parents:
        candidate = parent / "libs" / "contracts" / "events"
        if candidate.is_dir():
            return candidate
    return None


_CONTRACTS = find_contracts_dir(Path(__file__))


def _label(event_type: str) -> str:
    parts = event_type.split(".")
    tail = parts[-2:] if len(parts) > 2 else parts
    return " ".join(p.replace("_", " ") for p in tail).capitalize()


def _kind(schema: dict[str, Any]) -> str:
    if schema.get("format") == "uuid":
        return "id"
    if schema.get("format") == "date-time":
        return "datetime"
    kind = schema.get("type")
    return kind if isinstance(kind, str) else "text"


def _flatten(prefix: str, properties: dict[str, Any], depth: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        path = f"{prefix}.{name}"
        if (
            schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
            and depth < 3
        ):
            out.extend(_flatten(path, schema["properties"], depth + 1))
        else:
            out.append(
                {"path": path, "label": name.replace("_", " ").capitalize(), "kind": _kind(schema)}
            )
    return out


@lru_cache(maxsize=1)
def trigger_catalogue() -> tuple[dict[str, Any], ...]:
    """Every subscribable event with its variables, best effort from the contract schemas. An
    event with no committed schema simply lists no variables."""
    items: list[dict[str, Any]] = []
    for event_type in domain.SUBSCRIBABLE_EVENTS:
        variables: list[dict[str, str]] = []
        try:
            if _CONTRACTS is None:
                raise OSError("no contracts directory in this install")
            schema = json.loads((_CONTRACTS / event_type / "v1.schema.json").read_text())
            props = schema.get("properties")
            if isinstance(props, dict):
                variables = _flatten("payload", props, 0)
        except (OSError, ValueError):
            variables = []
        items.append(
            {
                "event_type": event_type,
                "label": _label(event_type),
                "module": event_type.split(".", 1)[0],
                "variables": variables,
            }
        )
    return tuple(items)


def default_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    return now - timedelta(days=WINDOW_DAYS), now + timedelta(seconds=1)

"""Builders for tests that need a pipeline the API cannot create yet.

Stage-configuration writes arrive in R2.5. Until then a test that needs an organization with its
own stages, an archived stage, a second pipeline or a row in an odd state inserts it here, as the
BYPASSRLS admin session (which is also how a test seeds *another* tenant). Every helper commits
its own transaction, so a test can wrap one call in `pytest.raises` and keep using the session
afterwards. Nothing in `app/` imports this module.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import SCHEMA, Deal, Pipeline, PipelineStage
from app.pipeline import StageSpec
from platform_core.db import TENANT_GUC

#: Three open stages, one won and one lost, none of them a legacy key.
CUSTOM_STAGES: tuple[StageSpec, ...] = (
    StageSpec("discovery", "Discovery", 0, "open", 10, "info"),
    StageSpec("scoping", "Scoping", 1, "open", 30, "info"),
    StageSpec("quote", "Quote", 2, "open", 60, "warning"),
    StageSpec("won", "Won", 3, "won", 100, "success"),
    StageSpec("lost", "Lost", 4, "lost", 0, "danger"),
)


@dataclass(frozen=True)
class Installed:
    """What `install_pipeline` made: the pipeline's id and each stage's id by key."""

    pipeline_id: uuid.UUID
    stage_ids: dict[str, uuid.UUID]


@asynccontextmanager
async def tenant_session(organization_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """A second **app-role** session whose every transaction is bound to `organization_id` — the
    `session` fixture's own binding, for a test that needs two transactions open at once (two
    writers racing, a reader that must not see another tenant's rows)."""
    engine = create_async_engine(
        settings().app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_session: Any, _transaction: Any, connection: Any) -> None:
            connection.exec_driver_sql(
                f"SELECT set_config('{TENANT_GUC}', '{organization_id}', true)"
            )

        yield session
    await engine.dispose()


async def insert_pipeline(
    admin: AsyncSession,
    organization_id: uuid.UUID,
    *,
    key: str = "sales",
    name: str = "Sales pipeline",
    is_default: bool = True,
    deleted: bool = False,
) -> uuid.UUID:
    """One `deal.pipelines` row, in its own committed transaction. Returns its id."""
    pipeline_id = uuid.uuid4()
    async with admin.begin():
        await admin.execute(
            Pipeline.__table__.insert().values(
                id=pipeline_id,
                organization_id=organization_id,
                key=key,
                name=name,
                is_default=is_default,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )
    return pipeline_id


async def insert_stage(
    admin: AsyncSession,
    organization_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    key: str,
    *,
    label: str | None = None,
    position: int = 0,
    outcome: str = "open",
    default_probability: int = 10,
    tone: str = "info",
    archived: bool = False,
    deleted: bool = False,
) -> uuid.UUID:
    """One `deal.pipeline_stages` row, in its own committed transaction. Every column can be
    given a value the database should refuse, which is the point of the schema tests."""
    stage_id = uuid.uuid4()
    async with admin.begin():
        await admin.execute(
            PipelineStage.__table__.insert().values(
                id=stage_id,
                organization_id=organization_id,
                pipeline_id=pipeline_id,
                key=key,
                label=label if label is not None else key.title(),
                position=position,
                outcome=outcome,
                default_probability=default_probability,
                tone=tone,
                archived_at=datetime.now(UTC) if archived else None,
                deleted_at=datetime.now(UTC) if deleted else None,
            )
        )
    return stage_id


async def install_pipeline(
    admin: AsyncSession,
    organization_id: uuid.UUID,
    stages: Sequence[StageSpec] = CUSTOM_STAGES,
    *,
    key: str = "sales",
    name: str = "Sales pipeline",
) -> Installed:
    """A default pipeline with these stages for one organization, as a stage-configuration write
    will one day leave it. `StageSpec.archived` archives the stage."""
    pipeline_id = await insert_pipeline(admin, organization_id, key=key, name=name)
    stage_ids: dict[str, uuid.UUID] = {}
    for stage in stages:
        stage_ids[stage.key] = await insert_stage(
            admin,
            organization_id,
            pipeline_id,
            stage.key,
            label=stage.label,
            position=stage.position,
            outcome=stage.outcome,
            default_probability=stage.default_probability,
            tone=stage.tone,
            archived=stage.archived,
        )
    return Installed(pipeline_id=pipeline_id, stage_ids=stage_ids)


async def insert_deal(
    admin: AsyncSession,
    organization_id: uuid.UUID,
    *,
    stage: str,
    name: str | None = None,
    owner_id: uuid.UUID | None = None,
    value_minor: int = 100,
    probability: int = 10,
    deleted: bool = False,
    closed: bool = False,
) -> uuid.UUID:
    """One `deal.deals` row exactly as given — a stage key no pipeline contains, a soft-deleted
    deal, a deal of another tenant — bypassing every service rule on purpose."""
    deal_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": deal_id,
        "organization_id": organization_id,
        "name": name or f"Deal {deal_id.hex[:8]}",
        "company_id": uuid.uuid4(),
        "value_minor": value_minor,
        "currency": "INR",
        "stage": stage,
        "owner_id": owner_id or uuid.uuid4(),
        "probability": probability,
        "close_date": date(2026, 12, 31),
        "source": "",
        "deleted_at": datetime.now(UTC) if deleted else None,
        "closed_at": datetime.now(UTC) if closed else None,
    }
    async with admin.begin():
        await admin.execute(Deal.__table__.insert().values(**values))
    return deal_id

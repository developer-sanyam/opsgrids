"""m5: two concurrent captures of the same email in one organization. The pre-check
(`selectors.get_lead_by_email_index`) narrows this to a rare race, not eliminates it — two
real sessions, both reading "not found" before either has committed its own insert, is exactly
the window `create_lead`'s `IntegrityError` catch exists for. Without it, the loser's insert
would raise a raw, unhandled `IntegrityError` instead of the same ordinary 409 the pre-check
gives everyone else.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import services
from app.config import settings
from app.models import SCHEMA, Lead
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def test_two_concurrent_captures_of_the_same_email_only_one_succeeds(org_id) -> None:
    cfg = settings()
    engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    def _bind(org: uuid.UUID):
        def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

        return _listener

    ctx = ctx_for(org_id, "crm.lead.lead.write", Scope.ORG)

    async def _create(sess, name: str):
        try:
            async with sess.begin():
                return await services.create_lead(
                    sess,
                    ctx,
                    full_name=name,
                    company=None,
                    email="race@stellar.example",
                    phone=None,
                    source="form",
                    owner_id=None,
                    notes="",
                )
        except ConflictProblem as exc:
            return exc

    try:
        async with maker_a() as session_a, maker_b() as session_b:
            sa_event.listen(session_a.sync_session, "after_begin", _bind(org_id))
            sa_event.listen(session_b.sync_session, "after_begin", _bind(org_id))

            result_a, result_b = await asyncio.gather(
                _create(session_a, "Racer A"), _create(session_b, "Racer B")
            )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    outcomes = [result_a, result_b]
    successes = [r for r in outcomes if not isinstance(r, ConflictProblem)]
    refusals = [r for r in outcomes if isinstance(r, ConflictProblem)]
    assert len(successes) == 1
    assert len(refusals) == 1

    check_engine = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    try:
        check_maker = async_sessionmaker(check_engine, expire_on_commit=False)
        async with check_maker() as check:
            sa_event.listen(check.sync_session, "after_begin", _bind(org_id))
            async with check.begin():
                count = (
                    await check.execute(
                        select(func.count())
                        .select_from(Lead)
                        .where(Lead.organization_id == org_id, Lead.email_index.is_not(None))
                    )
                ).scalar_one()
    finally:
        await check_engine.dispose()
    assert count == 1

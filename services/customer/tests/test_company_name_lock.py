"""M2: the database no longer refuses a *new* case-insensitive duplicate company name outright
(`uq_companies_org_name` stayed case-sensitive, on purpose — see its own comment in
`app/models.py`) — `services.create_company`'s `pg_advisory_xact_lock` is what "no new
case-insensitive duplicate" rests on now. Proven here with two real sessions racing to create
the same name in different cases, not a single session where nothing would ever actually
contend for the lock.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Owner
from platform_core.auth import Scope
from platform_core.db import TENANT_GUC
from platform_core.errors import ConflictProblem
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def test_two_concurrent_case_variant_creates_only_one_succeeds(
    org_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Widens the natural check-then-insert race window with an artificial delay right after
    the name lookup — on localhost, two real transactions started via `asyncio.gather` are not
    reliably guaranteed to actually overlap inside that window otherwise, and the point of
    this test is that they *are* forced to. `services.create_company`'s advisory lock is what
    is supposed to close this window; without it, both branches would read "not found" and
    both would insert, since the database itself no longer refuses a case-insensitive
    duplicate outright (M2)."""
    original_lookup = selectors.get_company_by_name

    async def _slow_lookup(session, organization_id, name):  # type: ignore[no-untyped-def]
        result = await original_lookup(session, organization_id, name)
        await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(selectors, "get_company_by_name", _slow_lookup)

    cfg = settings()
    engine_a = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    engine_b = create_async_engine(cfg.app_dsn, connect_args=_connect_args())
    maker_a = async_sessionmaker(engine_a, expire_on_commit=False)
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)

    def _bind(org: uuid.UUID):
        def _listener(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org}', true)")

        return _listener

    ctx = ctx_for(org_id, "crm.book.company.write", Scope.ORG)
    # Two *different* owners -- `create_company` also row-locks the owner it is assigning
    # (`_assert_owner_assignable`), and sharing one owner between the two concurrent creates
    # would serialize them on that lock regardless of whether the name lock under test does
    # anything at all, making the test pass even with the fix removed.
    owner_a, owner_b = uuid.uuid4(), uuid.uuid4()

    try:
        async with maker_a() as session_a, maker_b() as session_b:
            event.listen(session_a.sync_session, "after_begin", _bind(org_id))
            event.listen(session_b.sync_session, "after_begin", _bind(org_id))

            async with session_a.begin():
                for owner_id, email in (
                    (owner_a, "priya-lock-test@example.com"),
                    (owner_b, "arjun-lock-test@example.com"),
                ):
                    await session_a.execute(
                        Owner.__table__.insert().values(
                            id=owner_id,
                            organization_id=org_id,
                            full_name="Rep",
                            email=email,
                            role="",
                            is_active=True,
                        )
                    )

            async def _create(sess, name: str, owner_id: uuid.UUID):
                try:
                    async with sess.begin():
                        return await services.create_company(
                            sess, ctx, name=name, owner_id=owner_id
                        )
                except ConflictProblem as exc:
                    return exc

            result_a, result_b = await asyncio.gather(
                _create(session_a, "Riverside Traders", owner_a),
                _create(session_b, "RIVERSIDE TRADERS", owner_b),
            )
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

    outcomes = [result_a, result_b]
    successes = [r for r in outcomes if not isinstance(r, ConflictProblem)]
    refusals = [r for r in outcomes if isinstance(r, ConflictProblem)]
    assert len(successes) == 1
    assert len(refusals) == 1

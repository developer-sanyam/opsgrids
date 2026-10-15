"""`ensure_own_owner` under two concurrent first calls for the same login.

The CRM SPA can ask `PUT /customers/owners/me` from two places at once on a first load (two tabs,
or an activity and a lead form). Both used to read "no row", both inserted, and the loser's
unique-index violation surfaced as an error instead of the row the winner had just created.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import selectors, services
from app.config import settings
from app.models import SCHEMA, Owner
from platform_core.auth import Scope, ScopeContext
from platform_core.db import TENANT_GUC
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


async def test_two_concurrent_first_calls_both_get_the_one_row(session, org_id) -> None:
    membership_id = uuid.uuid4()
    principal = principal_for(org_id, ["crm.book.owner.write:ORG"], membership_id=membership_id)
    ctx = ScopeContext(principal=principal, scope=Scope.ORG, permission="crm.book.owner.write")
    email = f"owner-{uuid.uuid4().hex[:6]}@example.com"

    engine = create_async_engine(
        settings().app_dsn,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    # Both callers finish their "is there a row?" reads before either inserts: the race, forced.
    both_read = asyncio.Barrier(2)
    real_by_email = selectors.get_owner_by_email

    async def _by_email_then_wait(s, org, address):  # type: ignore[no-untyped-def]
        found = await real_by_email(s, org, address)
        await both_read.wait()
        return found

    async def _call() -> tuple[uuid.UUID, bool]:
        async with maker() as s:

            @event.listens_for(s.sync_session, "after_begin")
            def _bind(_session, _transaction, connection) -> None:  # type: ignore[no-untyped-def]
                connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

            async with s.begin():
                owner, created = await services.ensure_own_owner(
                    s, ctx, full_name="Asha Verma", email=email
                )
                return owner.id, created

    mp = pytest.MonkeyPatch()
    mp.setattr(selectors, "get_owner_by_email", _by_email_then_wait)
    try:
        results = await asyncio.wait_for(asyncio.gather(_call(), _call()), timeout=20)
    finally:
        mp.undo()
        await engine.dispose()

    assert {owner_id for owner_id, _ in results} == {membership_id}
    assert sorted(created for _, created in results) == [False, True]
    async with session.begin():
        count = await session.scalar(
            select(func.count()).select_from(Owner).where(Owner.organization_id == org_id)
        )
    assert count == 1

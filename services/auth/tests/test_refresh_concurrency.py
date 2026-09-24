"""Genuine concurrency, against real PostgreSQL and two independent connections — the same
shape `test_operator_concurrency.py` uses, for the identical reason: only a real race between
two live sessions proves the grace window forgives it, rather than a single test connection
happening to exercise the code in whatever order the test wrote its calls.

The scenario is SSO's own: one refresh cookie is shared by every tab and every product SPA a
person has open (ADR-0003 addendum), so once a tab rotates it, every other tab still holding
the now-spent token — a proactive refresh timer, a 401 retry — presents it again within
milliseconds. `services.rotate_refresh`'s reuse response revokes the whole family; the grace
window (`refresh_reuse_grace_seconds`) is what stops that from signing everybody out just for
having the app open twice.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app import services
from app.config import settings
from app.models import SCHEMA
from tests.conftest import PASSWORD, FakeEntitlements

pytestmark = pytest.mark.integration


def _connect_args() -> dict:
    return {"server_settings": {"search_path": f"{SCHEMA},public"}}


async def _second_session() -> tuple[AsyncSession, AsyncEngine]:
    engine = create_async_engine(settings().app_dsn, connect_args=_connect_args())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return maker(), engine


class TestConcurrentRefreshFromTwoTabs:
    async def test_two_tabs_racing_the_same_spent_token_both_land_on_the_same_pair(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        """Tab A already rotated (committed, out of the race — a real request always reaches
        the server before any other, however close two clocks look). Tabs B and C are the
        race: both still hold the token Tab A just spent, and both present it at once."""
        outcome = await services.login(
            session,
            cfg=cfg,
            pair=pair,
            directory=directory,
            entitlements=FakeEntitlements(),
            email="sam@example.com",
            password=PASSWORD,
            org_hint=None,
        )
        assert outcome.issued is not None
        captured = outcome.issued.refresh_token
        await session.commit()

        rotated = await services.rotate_refresh(
            session,
            entitlements=FakeEntitlements(),
            cfg=cfg,
            pair=pair,
            directory=directory,
            raw_token=captured,
        )
        await session.commit()

        second, second_engine = await _second_session()
        third, third_engine = await _second_session()
        try:

            async def _replay(s: AsyncSession) -> services.Issued:
                return await services.rotate_refresh(
                    s,
                    entitlements=FakeEntitlements(),
                    cfg=cfg,
                    pair=pair,
                    directory=directory,
                    raw_token=captured,
                )

            first, other = await asyncio.gather(_replay(second), _replay(third))
        finally:
            await second.close()
            await second_engine.dispose()
            await third.close()
            await third_engine.dispose()

        # Both racing tabs land on Tab A's own successor pair — neither was signed out, and
        # neither minted a chain of its own.
        assert first.refresh_token == rotated.refresh_token
        assert first.access_token == rotated.access_token
        assert other.refresh_token == rotated.refresh_token
        assert other.access_token == rotated.access_token

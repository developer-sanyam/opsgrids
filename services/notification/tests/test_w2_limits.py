"""Rate limits on the two routes that spend Meta's quota, and a sync keeping what a webhook set."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import w2_templates
from app.models import ProviderTemplate
from app.main import app
from tests.test_w2_templates_api import (
    BASE,
    FakeMeta,
    admin,
    as_principal,
    connect,
    create_body,
    meta_template,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def meta() -> Iterator[FakeMeta]:
    fake = FakeMeta()
    app.dependency_overrides[w2_templates.graph_transport] = lambda: httpx.MockTransport(fake)
    yield fake
    app.dependency_overrides.pop(w2_templates.graph_transport, None)


async def test_sync_is_rate_limited_per_organization(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    meta.pages["waba-a"] = [[meta_template("1", "a")]]
    with as_principal(admin(org_id)):
        codes = [
            (await client.post(f"{BASE}/sync")).status_code
            for _ in range(w2_templates.SYNC_PER_MINUTE + 1)
        ]
    assert codes[:-1] == [200] * w2_templates.SYNC_PER_MINUTE
    assert codes[-1] == 429
    # The refused call never reached Meta.
    assert len(meta.calls) == w2_templates.SYNC_PER_MINUTE


async def test_create_is_rate_limited_per_organization(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    with as_principal(admin(org_id)):
        codes = []
        for i in range(w2_templates.CREATE_PER_MINUTE + 1):
            response = await client.post(BASE, json=create_body(name=f"order_update_{i}"))
            codes.append(response.status_code)
    assert codes[-1] == 429
    assert set(codes[:-1]) == {201}


async def test_a_sync_whose_answer_has_no_rating_keeps_the_webhook_set_one(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID, meta: FakeMeta
) -> None:
    await connect(session, org_id)
    unrated = meta_template("1", "a")
    del unrated["quality_score"]
    meta.pages["waba-a"] = [[unrated]]
    with as_principal(admin(org_id)):
        await client.post(f"{BASE}/sync")
        async with session.begin():
            await session.execute(update(ProviderTemplate).values(quality="low"))
        await client.post(f"{BASE}/sync")
        detail = await client.get(f"{BASE}/a")
    assert detail.json()["quality"] == "low"

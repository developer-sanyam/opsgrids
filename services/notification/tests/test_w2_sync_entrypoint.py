"""`sync_templates(session, organization_id)`: the importable entry point other slices call."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ProviderAccount, ProviderTemplate
from app.w2_services import ChannelNotConnected, sync_templates
from platform_core import pii

pytestmark = pytest.mark.integration

TOKEN_A = "ci-only-token-org-a"
TOKEN_B = "ci-only-token-org-b"


def _template(meta_id: str, name: str) -> dict:
    return {
        "id": meta_id,
        "name": name,
        "language": "en",
        "status": "APPROVED",
        "category": "UTILITY",
        "components": [{"type": "BODY", "text": "Hello there friend"}],
    }


def _sealed(token: str) -> bytes:
    return pii.seal(token, kind="generic", settings=settings())[0]


async def test_it_syncs_on_a_callers_own_session(session: AsyncSession, org_id: uuid.UUID) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [_template("1", "a"), _template("2", "b")]})

    async with session.begin():
        session.add(
            ProviderAccount(
                organization_id=org_id,
                channel="whatsapp",
                waba_id="waba-a",
                phone_number_id="1001",
                display_number="+1",
                access_token_enc=_sealed(TOKEN_A),
                status="connected",
            )
        )
    async with session.begin():
        result = await sync_templates(session, org_id, transport=httpx.MockTransport(handler))
    assert (result.synced, result.created, result.updated, result.removed) == (2, 2, 0, 0)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN_A}"

    async with session.begin():
        codes = {r.code for r in (await session.execute(select(ProviderTemplate))).scalars()}
    assert codes == {"a", "b"}


async def test_it_cannot_be_pointed_at_another_organization(
    session: AsyncSession, admin_session: AsyncSession, org_id: uuid.UUID
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    other_org = uuid.uuid4()
    async with admin_session.begin():
        admin_session.add(
            ProviderAccount(
                organization_id=other_org,
                channel="whatsapp",
                waba_id="waba-b",
                phone_number_id="2002",
                display_number="+1",
                access_token_enc=_sealed(TOKEN_B),
                status="connected",
            )
        )
    async with session.begin():
        with pytest.raises(ChannelNotConnected):
            await sync_templates(session, other_org, transport=httpx.MockTransport(handler))
    assert calls == []

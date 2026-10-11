"""`GET /internal/v1/organizations/{org}/references` — what customer asks deal before it deletes an
aborted conversion's orphan company or contact: how many live deals and how many activities name
them. Counts only, never ids. `exclude_deal_id` (the attempt's own deal) is left out of the deal
counts only when deal itself would treat it as disposable (`services.is_pristine_lead_deal`)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.config import settings
from app.main import app
from app.models import Deal
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration


def _token(
    org_id: uuid.UUID, *, caller: str = "customer", claims: dict[str, str] | None = None
) -> str:
    return mint_service_token(
        settings(),
        audience="deal",
        caller=caller,
        claims={"org": str(org_id)} if claims is None else claims,
    )


def _url(org_id: uuid.UUID) -> str:
    return f"/internal/v1/organizations/{org_id}/references"


async def _get(client: AsyncClient, org_id: uuid.UUID, token: str | None = None, **params: object):  # type: ignore[no-untyped-def]
    headers = {"X-Internal-Token": token if token is not None else _token(org_id)}
    return await client.get(
        _url(org_id), headers=headers, params={k: str(v) for k, v in params.items() if v}
    )


async def _deal(
    session: AsyncSession,
    org_id: uuid.UUID,
    company_id: uuid.UUID,
    *,
    contact_id: uuid.UUID | None = None,
    by_lead: bool = False,
) -> Deal:
    ctx = (
        services.lead_conversion_ctx(org_id, None)
        if by_lead
        else ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG)
    )
    return await services.create_deal(
        session,
        ctx,
        name=f"Deal {uuid.uuid4()}",
        company_id=company_id,
        primary_contact_id=contact_id,
        owner_id=uuid.uuid4(),
        value_minor=0,
        close_date=date(2026, 12, 31),
        acting_as="lead" if by_lead else None,
    )


async def _activity(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    deal_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
) -> None:
    await services.log_activity(
        session,
        ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG),
        activity_type="note",
        subject="Called",
        owner_id=uuid.uuid4(),
        deal_id=deal_id,
        company_id=company_id,
        contact_id=contact_id,
    )


async def test_references_without_a_token_is_401(client: AsyncClient, org_id: uuid.UUID) -> None:
    resp = await client.get(_url(org_id), params={"company_id": str(uuid.uuid4())})
    assert resp.status_code == 401


@pytest.mark.parametrize("caller", ["lead", "notification", "finance", "deal"])
async def test_references_from_another_service_is_403(
    client: AsyncClient, org_id: uuid.UUID, caller: str
) -> None:
    resp = await _get(client, org_id, _token(org_id, caller=caller), company_id=uuid.uuid4())
    assert resp.status_code == 403


async def test_references_with_another_organizations_claim_is_403(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    resp = await _get(client, org_id, _token(uuid.uuid4()), company_id=uuid.uuid4())
    assert resp.status_code == 403


async def test_references_with_no_org_claim_is_403(client: AsyncClient, org_id: uuid.UUID) -> None:
    resp = await _get(client, org_id, _token(org_id, claims={}), company_id=uuid.uuid4())
    assert resp.status_code == 403


async def test_a_malformed_uuid_is_422(client: AsyncClient, org_id: uuid.UUID) -> None:
    resp = await _get(client, org_id, company_id="not-a-uuid")
    assert resp.status_code == 422


async def test_references_counts_live_deals_by_company_and_by_contact(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    company, contact = uuid.uuid4(), uuid.uuid4()
    async with session.begin():
        await _deal(session, org_id, company, contact_id=contact)
        await _deal(session, org_id, company)
        await _deal(session, org_id, company)
        gone = await _deal(session, org_id, company, contact_id=contact)
        await services.delete_deal(
            session, ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG), gone
        )
    resp = await _get(client, org_id, company_id=company, contact_id=contact)
    assert resp.status_code == 200
    body = resp.json()
    assert body["company_deal_count"] == 3
    assert body["contact_deal_count"] == 1


async def test_exclude_deal_id_removes_only_a_disposable_deal(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    company, contact = uuid.uuid4(), uuid.uuid4()
    async with session.begin():
        pristine = await _deal(session, org_id, company, contact_id=contact, by_lead=True)
        touched = await _deal(session, org_id, company, contact_id=contact, by_lead=True)
        await _activity(session, org_id, deal_id=touched.id)
        deleted = await _deal(session, org_id, company, contact_id=contact, by_lead=True)
        await services.delete_deal(session, services.service_ctx(org_id), deleted)

    def counts(body: dict) -> tuple[int, int]:
        return body["company_deal_count"], body["contact_deal_count"]

    base = (await _get(client, org_id, company_id=company, contact_id=contact)).json()
    assert counts(base) == (2, 2)
    without_pristine = await _get(
        client, org_id, company_id=company, contact_id=contact, exclude_deal_id=pristine.id
    )
    assert counts(without_pristine.json()) == (1, 1)
    without_touched = await _get(
        client, org_id, company_id=company, contact_id=contact, exclude_deal_id=touched.id
    )
    assert counts(without_touched.json()) == (2, 2)
    without_deleted = await _get(
        client, org_id, company_id=company, contact_id=contact, exclude_deal_id=deleted.id
    )
    assert counts(without_deleted.json()) == (2, 2)


async def test_activity_counts_include_every_activity_naming_the_company_or_contact(
    client: AsyncClient, session: AsyncSession, org_id: uuid.UUID
) -> None:
    company, contact = uuid.uuid4(), uuid.uuid4()
    async with session.begin():
        deal = await _deal(session, org_id, company, contact_id=contact)
        await _activity(session, org_id, deal_id=deal.id, company_id=company, contact_id=contact)
        await _activity(session, org_id, company_id=company)
        await services.delete_deal(
            session, ctx_for(org_id, "crm.pipeline.deal.write", Scope.ORG), deal
        )
    body = (await _get(client, org_id, company_id=company, contact_id=contact)).json()
    assert body == {
        "company_deal_count": 0,
        "contact_deal_count": 0,
        "company_activity_count": 2,
        "contact_activity_count": 1,
    }


async def test_an_unknown_company_counts_zero_not_404(
    client: AsyncClient, org_id: uuid.UUID
) -> None:
    resp = await _get(client, org_id, company_id=uuid.uuid4(), contact_id=uuid.uuid4())
    assert resp.status_code == 200
    assert resp.json() == {
        "company_deal_count": 0,
        "contact_deal_count": 0,
        "company_activity_count": 0,
        "contact_activity_count": 0,
    }


async def test_references_never_counts_another_tenants_deals(
    client: AsyncClient,
    session: AsyncSession,
    admin_session: AsyncSession,
    org_id: uuid.UUID,
) -> None:
    company, contact = uuid.uuid4(), uuid.uuid4()
    other = uuid.uuid4()
    async with admin_session.begin():
        await _deal(admin_session, other, company, contact_id=contact)
        await _activity(admin_session, other, company_id=company, contact_id=contact)
    async with session.begin():
        await _deal(session, org_id, company)
    body = (await _get(client, org_id, company_id=company, contact_id=contact)).json()
    assert body == {
        "company_deal_count": 1,
        "contact_deal_count": 0,
        "company_activity_count": 0,
        "contact_activity_count": 0,
    }


def test_the_route_is_absent_from_the_public_openapi() -> None:
    assert not [path for path in app.openapi()["paths"] if "/references" in path]

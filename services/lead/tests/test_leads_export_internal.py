"""`GET /internal/v1/organizations/{org}/leads:export` — the read behind Engage's cold e-mail to
leads. Notification only, bound to the token's organization, the caller's OWN narrowing applied in
the query, the address opened only on request and the read audited with counts and no address.

Also the Leads list's `source` filter, which the "all leads matching the filter" selection uses."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import AuditLog
from platform_core.auth import get_principal
from platform_core.internal_auth import mint_service_token
from tests.helpers import principal_for

pytestmark = pytest.mark.integration


def _headers(org_id: uuid.UUID, *, caller: str = "notification") -> dict[str, str]:
    token = mint_service_token(
        settings(), audience="lead", caller=caller, claims={"org": str(org_id)}
    )
    return {"X-Internal-Token": token}


def _url(org_id: uuid.UUID, query: str = "") -> str:
    return f"/internal/v1/organizations/{org_id}/leads:export{query}"


class TestLeadsExportInternal:
    async def test_without_a_service_token_it_is_refused(self, client, org_id) -> None:
        assert (await client.get(_url(org_id))).status_code == 401

    @pytest.mark.parametrize("caller", ["customer", "finance", "marketing"])
    async def test_a_caller_other_than_notification_is_refused(
        self, client, org_id, caller
    ) -> None:
        resp = await client.get(_url(org_id), headers=_headers(org_id, caller=caller))
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id
    ) -> None:
        resp = await client.get(_url(org_id), headers=_headers(uuid.uuid4()))
        assert resp.status_code == 403

    async def test_another_organizations_leads_are_never_returned(
        self, client, org_id, data
    ) -> None:
        other = uuid.uuid4()
        resp = await client.get(_url(other), headers=_headers(other))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    @pytest.mark.parametrize("query", ["?ids=not-a-uuid", "?limit=0", "?limit=501"])
    async def test_bad_input_is_a_422(self, client, org_id, query) -> None:
        assert (await client.get(_url(org_id, query), headers=_headers(org_id))).status_code == 422

    async def test_returns_the_real_address_and_audits_a_count_not_the_address(
        self, client, org_id, data, session
    ) -> None:
        resp = await client.get(_url(org_id), headers=_headers(org_id))
        assert resp.status_code == 200
        items = {i["lead_id"]: i for i in resp.json()["items"]}
        priya = items[str(data["lead_a_id"])]
        assert priya["email"] == "priya@stellar.example"
        assert (priya["full_name"], priya["company"]) == ("Priya Sharma", "Stellar Robotics")
        assert (priya["status"], priya["source"]) == ("qualified", "form")
        assert items[str(data["lead_b_id"])]["email"] is None
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "lead.exported_for_engage")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1 and rows[0].acting_as == "notification"
        assert rows[0].after == {"count": 2, "with_email": 1}
        assert "stellar" not in str(rows[0].after)

    async def test_emails_false_opens_and_audits_nothing(
        self, client, org_id, data, session
    ) -> None:
        resp = await client.get(_url(org_id, "?emails=false"), headers=_headers(org_id))
        assert all(i["email"] is None for i in resp.json()["items"])
        async with session.begin():
            rows = (await session.execute(select(AuditLog))).scalars().all()
        assert not [r for r in rows if r.action == "lead.exported_for_engage"]

    async def test_the_owner_narrowing_applies_in_the_query(self, client, org_id, data) -> None:
        resp = await client.get(
            _url(org_id, f"?owner_id={data['owner_b']}"), headers=_headers(org_id)
        )
        assert [i["lead_id"] for i in resp.json()["items"]] == [str(data["lead_b_id"])]

    async def test_ids_status_and_source_filters(self, client, org_id, data) -> None:
        by_id = await client.get(
            _url(org_id, f"?ids={data['lead_b_id']}"), headers=_headers(org_id)
        )
        assert [i["lead_id"] for i in by_id.json()["items"]] == [str(data["lead_b_id"])]
        by_status = await client.get(_url(org_id, "?status=qualified"), headers=_headers(org_id))
        assert [i["lead_id"] for i in by_status.json()["items"]] == [str(data["lead_a_id"])]
        by_source = await client.get(_url(org_id, "?source=manual"), headers=_headers(org_id))
        assert [i["lead_id"] for i in by_source.json()["items"]] == [str(data["lead_b_id"])]

    async def test_keyset_paging_walks_every_lead_once(self, client, org_id, data) -> None:
        seen: list[str] = []
        query = "?limit=1"
        for _ in range(5):
            body = (await client.get(_url(org_id, query), headers=_headers(org_id))).json()
            seen += [i["lead_id"] for i in body["items"]]
            if body["next_after_id"] is None:
                break
            query = f"?limit=1&after_id={body['next_after_id']}"
        assert sorted(seen) == sorted([str(data["lead_a_id"]), str(data["lead_b_id"])])


class TestLeadsListSourceFilter:
    async def test_source_narrows_the_list_and_own_scope_still_applies(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(
            org_id, ["crm.lead.lead.read:OWN"], entitlements=["crm"], subject_ref=data["owner_a"]
        )
        app.dependency_overrides[get_principal] = lambda: principal
        try:
            mine = await client.get("/api/v1/leads?source=form")
            theirs = await client.get("/api/v1/leads?source=manual")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert [i["id"] for i in mine.json()["items"]] == [str(data["lead_a_id"])]
        assert theirs.json()["items"] == []

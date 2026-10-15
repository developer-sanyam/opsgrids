"""`GET /internal/v1/organizations/{org}/contacts:export` (C53) — the read Engage's projection
makes. Notification only, bound to the token's organization, soft-deleted rows included, the
number opened only for live rows and the read audited with ids and no number."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app import services
from app.config import settings
from app.models import AuditLog
from platform_core.auth import Scope
from platform_core.internal_auth import mint_service_token
from tests.helpers import ctx_for

pytestmark = pytest.mark.integration

RAW = "+917976603446"


def _token(org_id: uuid.UUID, *, caller: str = "notification") -> str:
    return mint_service_token(
        settings(), audience="customer", caller=caller, claims={"org": str(org_id)}
    )


def _url(org_id: uuid.UUID, query: str = "") -> str:
    return f"/internal/v1/organizations/{org_id}/contacts:export{query}"


class TestContactsExportInternal:
    async def test_without_a_service_token_it_is_refused(self, client, org_id, data) -> None:
        assert (await client.get(_url(org_id, f"?ids={data['contact_a_id']}"))).status_code == 401

    @pytest.mark.parametrize("caller", ["finance", "lead", "support"])
    async def test_a_caller_other_than_notification_is_refused(
        self, client, org_id, data, caller
    ) -> None:
        resp = await client.get(
            _url(org_id, f"?ids={data['contact_a_id']}"),
            headers={"X-Internal-Token": _token(org_id, caller=caller)},
        )
        assert resp.status_code == 403

    async def test_a_token_addressed_to_another_organization_is_refused(
        self, client, org_id, data
    ) -> None:
        resp = await client.get(
            _url(org_id, f"?ids={data['contact_a_id']}"),
            headers={"X-Internal-Token": _token(uuid.uuid4())},
        )
        assert resp.status_code == 403

    async def test_another_organizations_ids_come_back_empty(self, client, org_id, data) -> None:
        other = uuid.uuid4()
        resp = await client.get(
            _url(other, f"?ids={data['contact_a_id']}"),
            headers={"X-Internal-Token": _token(other)},
        )
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    @pytest.mark.parametrize("query", ["?ids=not-a-uuid", "?limit=0", "?limit=501"])
    async def test_bad_input_is_a_422(self, client, org_id, query) -> None:
        resp = await client.get(_url(org_id, query), headers={"X-Internal-Token": _token(org_id)})
        assert resp.status_code == 422

    async def test_too_many_ids_is_a_422(self, client, org_id) -> None:
        ids = ",".join(str(uuid.uuid4()) for _ in range(101))
        resp = await client.get(
            _url(org_id, f"?ids={ids}"), headers={"X-Internal-Token": _token(org_id)}
        )
        assert resp.status_code == 422

    async def test_returns_the_fields_engage_needs_and_audits_without_the_number(
        self, client, org_id, data, session
    ) -> None:
        write = ctx_for(org_id, "crm.book.contact.write", Scope.ORG)
        async with session.begin():
            await services.set_contact_phone(session, write, data["contact_a"], RAW)
        resp = await client.get(
            _url(org_id, f"?ids={data['contact_a_id']}"),
            headers={"X-Internal-Token": _token(org_id)},
        )
        assert resp.status_code == 200
        (item,) = resp.json()["items"]
        assert item["contact_id"] == str(data["contact_a_id"])
        assert item["company_id"] == str(data["company_a_id"])
        assert item["full_name"] == "Rajesh Khanna"
        assert item["email"] == "rajesh.khanna@northwind.co"
        assert item["phone"] == RAW
        assert item["status"] == "customer"
        assert item["deleted_at"] is None
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(AuditLog).where(AuditLog.action == "contact.exported")
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].acting_as == "notification"
        assert RAW not in str(rows[0].after)

    async def test_a_soft_deleted_contact_is_returned_marked_and_without_a_number(
        self, client, org_id, data, session
    ) -> None:
        write = ctx_for(org_id, "crm.book.contact.write", Scope.ORG)
        async with session.begin():
            await services.set_contact_phone(session, write, data["contact_a"], RAW)
            await services.delete_contact(session, write, data["contact_a"])
        resp = await client.get(
            _url(org_id, f"?ids={data['contact_a_id']}"),
            headers={"X-Internal-Token": _token(org_id)},
        )
        (item,) = resp.json()["items"]
        assert item["deleted_at"] is not None
        assert item["phone"] is None

    async def test_keyset_paging_walks_every_contact_once(self, client, org_id, data) -> None:
        seen: list[str] = []
        query = "?limit=1"
        for _ in range(5):
            body = (
                await client.get(_url(org_id, query), headers={"X-Internal-Token": _token(org_id)})
            ).json()
            seen += [i["contact_id"] for i in body["items"]]
            if body["next_after_id"] is None:
                break
            query = (
                f"?limit=1&after_updated_at={body['next_after_updated_at']}"
                f"&after_id={body['next_after_id']}"
            )
        assert sorted(seen) == sorted([str(data["contact_a_id"]), str(data["contact_b_id"])])

"""Staff-only per-organization users: `GET /admin/organizations/{id}/users`, and the accounts
list's `user_count`.

Console writes and holds no PII here. The members read is **delegated** to account-service's own
staff-only directory route (which masks the email); these tests pin the console half of that:

1. **Authz** — every operator role may read (it is `OperatorReadAny`, like the org detail), a
   product token is a 404, an un-MFA'd operator token a 403, no token a 401.
2. **Two credentials forwarded** — console's own service token (aud=account, iss=console) *and*
   the operator's own bearer token, verbatim, exactly like `GET /admin/organizations/{id}`.
3. **Nothing is unmasked** — the response carries `email_masked` and no plaintext email field.
4. **Audited** — every read writes `organization.members_viewed` bound to the organization.
5. **Failure modes** — unknown organization 404, account down 503, bad paging 422.
6. **`user_count`** — merged live from account's bulk count; `null` (not a failed list) when
   account cannot be reached.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.config import settings
from app.models import OrgCommercialState
from tests.helpers import Downstream, as_principal, operator_principal

pytestmark = pytest.mark.integration


def _member(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "name": "Priya Nair",
        "email_masked": "p•••@a•••",
        "status": "active",
        "joined_at": "2026-01-05T09:00:00+00:00",
        "last_active_at": "2026-09-24T18:30:00+00:00",
        "roles": ["Owner", "HR Admin"],
    }
    row.update(overrides)
    return row


def _page(items: list[dict[str, object]], *, page: int = 1, size: int = 25) -> dict[str, object]:
    return {"items": items, "total": len(items), "page": page, "size": size, "pages": 1}


class TestMembersRead:
    async def test_returns_the_members_masked_and_forwards_both_credentials(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream(
            {f"/organizations/{org_id}/members": httpx.Response(200, json=_page([_member()]))}
        )
        downstream.install()

        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(
                f"/api/v1/admin/organizations/{org_id}/users",
                headers={"Authorization": "Bearer the-operators-own-access-token"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1 and body["page"] == 1 and body["size"] == 25
        member = body["members"][0]
        assert member["name"] == "Priya Nair"
        assert member["email_masked"] == "p•••@a•••"
        assert member["roles"] == ["Owner", "HR Admin"]
        assert member["status"] == "active"
        assert "email" not in member, "console never carries a plaintext email"

        call = downstream.calls[0]
        assert call.headers["x-operator-token"] == "the-operators-own-access-token"
        claims = jwt.decode(
            call.headers["x-internal-token"],
            settings().internal_service_token.get_secret_value(),
            algorithms=["HS256"],
            audience="account",
            options={"require": ["exp", "iss", "aud", "jti"]},
        )
        assert claims["iss"] == "console"

    async def test_a_plaintext_email_account_should_never_send_is_still_not_forwarded(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        """Defence in depth: `OrgMemberOut` has no email field, so even if account-service ever
        regressed and put one in its response, console would drop it rather than relay it."""
        leaky = _member(email="priya@acme.example", phone="+91 98765 43210")
        Downstream(
            {f"/organizations/{org_id}/members": httpx.Response(200, json=_page([leaky]))}
        ).install()
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 200, response.text
        assert "priya@acme.example" not in response.text
        assert "98765" not in response.text
        assert set(response.json()["members"][0]) == {
            "id",
            "name",
            "email_masked",
            "status",
            "joined_at",
            "last_active_at",
            "roles",
        }

    async def test_the_page_and_size_are_forwarded(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream(
            {
                f"/organizations/{org_id}/members": httpx.Response(
                    200, json=_page([], page=3, size=10)
                )
            }
        )
        downstream.install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(
                f"/api/v1/admin/organizations/{org_id}/users", params={"page": 3, "size": 10}
            )
        assert response.status_code == 200, response.text
        assert downstream.calls[0].url.params["page"] == "3"
        assert downstream.calls[0].url.params["size"] == "10"

    async def test_an_organization_with_no_members_is_an_empty_page(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        Downstream(
            {f"/organizations/{org_id}/members": httpx.Response(200, json=_page([]))}
        ).install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 200
        assert response.json()["members"] == []
        assert response.json()["total"] == 0

    async def test_an_unknown_organization_is_404(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        Downstream({f"/organizations/{org_id}/members": httpx.Response(404)}).install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 404

    async def test_account_service_being_down_is_a_503_not_an_empty_list(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        Downstream({}).install()  # every path -> 503
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 503

    async def test_paging_bounds_are_enforced_at_the_boundary(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        with as_principal(operator_principal(roles=["owner"])):
            page_zero = await client.get(
                f"/api/v1/admin/organizations/{org_id}/users", params={"page": 0}
            )
            too_big = await client.get(
                f"/api/v1/admin/organizations/{org_id}/users", params={"size": 201}
            )
        assert page_zero.status_code == 422
        assert too_big.status_code == 422


class TestMembersReadIsAuthorizedAndAudited:
    @pytest.mark.parametrize("role", ["owner", "billing_ops", "support"])
    async def test_every_operator_role_may_read(
        self, client: AsyncClient, org_id: uuid.UUID, role: str
    ) -> None:
        Downstream(
            {f"/organizations/{org_id}/members": httpx.Response(200, json=_page([]))}
        ).install()
        with as_principal(operator_principal(roles=[role])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 200, role

    async def test_a_role_outside_the_operator_set_is_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["intern"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 403
        assert downstream.calls == [], "a refused caller must not reach account-service"

    async def test_an_operator_token_that_never_completed_mfa_gets_403(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(roles=["owner"], amr=["pwd"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 403
        assert downstream.calls == []

    async def test_a_product_audience_token_gets_404_and_reaches_nothing(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        downstream = Downstream({})
        downstream.install()
        with as_principal(operator_principal(staff=False)):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 404
        assert downstream.calls == []

    async def test_an_unauthenticated_request_is_401(
        self, client: AsyncClient, org_id: uuid.UUID
    ) -> None:
        response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 401

    async def test_every_read_is_audited_against_the_organization(
        self, client: AsyncClient, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        Downstream(
            {f"/organizations/{org_id}/members": httpx.Response(200, json=_page([_member()]))}
        ).install()
        with as_principal(operator_principal(roles=["support"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 200

        audit = await selectors.recent_audit(directory_session, organization_id=org_id)
        assert [row.action for row in audit] == ["organization.members_viewed"]
        assert audit[0].organization_id == org_id
        assert audit[0].acting_as is not None and audit[0].acting_as.startswith("operator:")

    async def test_a_failed_read_is_not_audited_as_if_it_happened(
        self, client: AsyncClient, directory_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        Downstream({f"/organizations/{org_id}/members": httpx.Response(404)}).install()
        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get(f"/api/v1/admin/organizations/{org_id}/users")
        assert response.status_code == 404
        assert await selectors.recent_audit(directory_session, organization_id=org_id) == []


class TestUserCountOnTheList:
    async def test_the_list_carries_each_organizations_live_member_count(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org_id: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        async with admin_session.begin():
            admin_session.add_all(
                [
                    OrgCommercialState(organization_id=org_id, mrr_minor=0),
                    OrgCommercialState(organization_id=org_b, mrr_minor=0),
                ]
            )
        Downstream(
            {
                "/internal/v1/organizations/member-counts": httpx.Response(
                    200,
                    json={
                        "counts": [
                            {"organization_id": str(org_id), "count": 7},
                            # org_b has no memberships at all, so account omits it (GROUP BY).
                        ]
                    },
                )
            }
        ).install()

        with as_principal(operator_principal(roles=["support"])):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 200, response.text
        rows = {r["organization_id"]: r for r in response.json()["organizations"]}
        assert rows[str(org_id)]["user_count"] == 7
        assert rows[str(org_b)]["user_count"] == 0, (
            "account answered and does not list org_b: it has no members, a real zero"
        )

    async def test_an_account_outage_degrades_to_null_and_the_list_still_renders(
        self, client: AsyncClient, admin_session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        async with admin_session.begin():
            admin_session.add(
                OrgCommercialState(
                    organization_id=org_id,
                    mrr_minor=50000,
                    org_created_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
        Downstream({}).install()  # member-counts -> 503

        with as_principal(operator_principal(roles=["owner"])):
            response = await client.get("/api/v1/admin/organizations")
        assert response.status_code == 200, response.text
        row = response.json()["organizations"][0]
        assert row["user_count"] is None
        assert row["mrr_minor"] == 50000, "the rest of the row must not go blank"

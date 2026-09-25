"""The staff-only per-organization members read: `GET /admin/directory/organizations/{id}/members`,
and the bulk count behind console's Users column: `GET /internal/v1/organizations/member-counts`.

The members route is the sibling of `test_admin_directory.py`'s org-detail read and shares its
two-credential contract — console's service token *and* the operator's own token, both required —
so those properties are pinned here again for the new route rather than assumed by inheritance.
What this route adds, and what these tests exist for:

1. **Email is masked, always** — `email_masked` and no plaintext address anywhere in the body; the
   route has no unmask path at all (out of scope, and would need its own permission and audit).
2. **Roles by name** — the role's display name, not its code.
3. **Every status is listed**, so a support ticket about a removed or suspended person can see them.
4. **One organization only** — another organization's people never appear, in either direction.
5. **Audited** — `organization.members_viewed`, bound to the organization, attributed to the
   token's own `sub`; a refused or not-found read writes nothing.
6. **Authz negatives** — a missing credential, the wrong caller, the wrong audience, no second
   factor, and a role outside the operator set are all refused.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.admin as admin_module
from app import services
from app.config import settings
from app.models import AuditLog, Membership, Organization, Role
from app.schemas import OrganizationIn
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(
    *,
    sub: str | None = None,
    aud: str = "operator",
    roles: list[str] | None = None,
    amr: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "sub": sub if sub is not None else str(uuid.uuid4()),
            "aud": aud,
            "roles": roles if roles is not None else ["owner"],
            "amr": amr if amr is not None else ["pwd", "otp"],
        }
    )


def _fake_decode_token(
    _settings: object, token: str, *, expected_type: str, audience: str | None = None
) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = json.loads(token)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedProblem("Token is not valid") from exc
    if audience is not None and claims.get("aud") != audience:
        raise UnauthorizedProblem("Token is not valid")
    return claims


@pytest.fixture(autouse=True)
def _stub_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_module, "decode_token", _fake_decode_token)


def _headers(token: str, *, caller: str = "console", service: bool = True) -> dict[str, str]:
    headers = {"X-Operator-Token": token}
    if service:
        headers["X-Internal-Token"] = mint_service_token(
            settings(), audience="account", caller=caller
        )
    return headers


def _url(org_id: uuid.UUID) -> str:
    return f"/api/v1/admin/directory/organizations/{org_id}/members"


@pytest.fixture
async def other_org(admin_session: AsyncSession) -> Organization:
    async with admin_session.begin():
        return await services.create_organization(
            admin_session, OrganizationIn(name="Second Org", slug="second-org"), actor_user_id=None
        )


class TestBothCredentialsAreRequired:
    async def test_an_operator_token_without_a_service_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(), service=False))
        assert response.status_code == 401

    async def test_a_service_token_without_an_operator_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        token = mint_service_token(settings(), audience="account", caller="console")
        response = await client.get(_url(org.id), headers={"X-Internal-Token": token})
        assert response.status_code == 401

    async def test_a_service_token_from_a_caller_other_than_console_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(), caller="billing"))
        assert response.status_code == 401

    async def test_no_credentials_at_all_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        assert (await client.get(_url(org.id))).status_code == 401


class TestOperatorAuthz:
    async def test_a_product_audience_token_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(aud="app")))
        assert response.status_code == 401

    async def test_a_malformed_token_is_refused_not_a_500(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers("not-even-json"))
        assert response.status_code == 401

    async def test_a_token_that_never_completed_mfa_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(amr=["pwd"])))
        assert response.status_code == 403

    async def test_a_role_outside_the_operator_set_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(roles=["intern"])))
        assert response.status_code == 403

    @pytest.mark.parametrize("role", ["owner", "billing_ops", "support"])
    async def test_every_operator_role_may_read(
        self, client: AsyncClient, org: Organization, role: str
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(roles=[role])))
        assert response.status_code == 200, role

    async def test_a_refused_read_is_not_audited(
        self, client: AsyncClient, admin_session: AsyncSession, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token(roles=["intern"])))
        assert response.status_code == 403
        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "organization.members_viewed")
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


class TestTheMembersList:
    async def test_lists_members_with_masked_email_and_roles_by_name(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        admin_role_name = (
            await admin_session.execute(
                select(Role.name).where(Role.organization_id == org.id, Role.code == "ORG_ADMIN")
            )
        ).scalar_one()

        response = await client.get(_url(org.id), headers=_headers(_token()))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 4
        by_name = {row["name"]: row for row in body["items"]}
        maya = by_name["Maya Manager"]
        assert maya["id"] == str(people["manager"].id)
        assert maya["email_masked"].startswith("m") and "•" in maya["email_masked"]
        assert maya["status"] == "active"
        assert maya["roles"] == [admin_role_name]
        assert "ORG_ADMIN" not in maya["roles"], "roles are names, not codes"
        assert {"joined_at", "last_active_at"} <= maya.keys()

    async def test_no_plaintext_email_appears_anywhere_in_the_response(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token()))
        assert response.status_code == 200
        for plaintext in ("manager@example.com", "report@example.com", "@example.com"):
            assert plaintext not in response.text
        assert all("email" not in row for row in response.json()["items"])

    async def test_every_status_is_listed_not_only_active(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org: Organization,
        people: dict[str, Membership],
    ) -> None:
        async with admin_session.begin():
            await admin_session.execute(
                update(Membership)
                .where(Membership.id == people["outsider"].id)
                .values(status="removed")
            )
        response = await client.get(_url(org.id), headers=_headers(_token()))
        statuses = {row["name"]: row["status"] for row in response.json()["items"]}
        assert statuses["Otto Outsider"] == "removed"

    async def test_an_organization_with_no_members_is_an_empty_page(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(_url(org.id), headers=_headers(_token()))
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["total"] == 0

    async def test_another_organizations_people_never_appear(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org: Organization,
        other_org: Organization,
        people: dict[str, Membership],
    ) -> None:
        async with admin_session.begin():
            await services.add_member(
                admin_session,
                org_id=other_org.id,
                email="stranger@other.example",
                full_name="Sam Stranger",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )

        ours = (await client.get(_url(org.id), headers=_headers(_token()))).json()
        theirs = (await client.get(_url(other_org.id), headers=_headers(_token()))).json()
        assert "Sam Stranger" not in {row["name"] for row in ours["items"]}
        assert ours["total"] == 4
        assert [row["name"] for row in theirs["items"]] == ["Sam Stranger"]
        assert theirs["total"] == 1

    async def test_paging_is_bounded_and_offsets_through_the_list(
        self, client: AsyncClient, org: Organization, people: dict[str, Membership]
    ) -> None:
        first = (
            await client.get(
                _url(org.id), params={"page": 1, "size": 3}, headers=_headers(_token())
            )
        ).json()
        second = (
            await client.get(
                _url(org.id), params={"page": 2, "size": 3}, headers=_headers(_token())
            )
        ).json()
        assert first["total"] == second["total"] == 4
        assert len(first["items"]) == 3 and len(second["items"]) == 1
        assert {r["id"] for r in first["items"]}.isdisjoint({r["id"] for r in second["items"]})

        too_big = await client.get(_url(org.id), params={"size": 201}, headers=_headers(_token()))
        page_zero = await client.get(_url(org.id), params={"page": 0}, headers=_headers(_token()))
        assert too_big.status_code == 422
        assert page_zero.status_code == 422

    async def test_an_unknown_organization_is_404_and_writes_no_audit_row(
        self, client: AsyncClient, admin_session: AsyncSession
    ) -> None:
        response = await client.get(_url(uuid.uuid4()), headers=_headers(_token()))
        assert response.status_code == 404
        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(AuditLog.action == "organization.members_viewed")
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_the_read_is_audited_and_attributed_to_the_tokens_own_sub(
        self, client: AsyncClient, admin_session: AsyncSession, org: Organization
    ) -> None:
        operator_id = uuid.uuid4()
        response = await client.get(_url(org.id), headers=_headers(_token(sub=str(operator_id))))
        assert response.status_code == 200

        rows = (
            (
                await admin_session.execute(
                    select(AuditLog).where(
                        AuditLog.organization_id == org.id,
                        AuditLog.action == "organization.members_viewed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_id == operator_id
        assert rows[0].acting_as == f"operator:{operator_id}"


class TestMemberCountsInternalRead:
    """`GET /internal/v1/organizations/member-counts` — console's Users column, in one query."""

    URL = "/internal/v1/organizations/member-counts"

    @staticmethod
    def _as(caller: str) -> dict[str, str]:
        return {
            "X-Internal-Token": mint_service_token(settings(), audience="account", caller=caller)
        }

    async def test_it_needs_a_service_token(self, client: AsyncClient) -> None:
        assert (await client.get(self.URL)).status_code == 401

    async def test_a_caller_other_than_console_is_refused(self, client: AsyncClient) -> None:
        assert (await client.get(self.URL, headers=self._as("billing"))).status_code == 401

    async def test_counts_each_organization_and_excludes_removed(
        self,
        client: AsyncClient,
        admin_session: AsyncSession,
        org: Organization,
        other_org: Organization,
        people: dict[str, Membership],
    ) -> None:
        async with admin_session.begin():
            await services.add_member(
                admin_session,
                org_id=other_org.id,
                email="stranger@other.example",
                full_name="Sam Stranger",
                actor_user_id=None,
                role_codes=["MEMBER"],
            )
            await admin_session.execute(
                update(Membership)
                .where(Membership.id == people["outsider"].id)
                .values(status="removed")
            )

        response = await client.get(self.URL, headers=self._as("console"))
        assert response.status_code == 200, response.text
        counts = {row["organization_id"]: row["count"] for row in response.json()["counts"]}
        assert counts[str(org.id)] == 3, "the removed membership is history, not a seat"
        assert counts[str(other_org.id)] == 1

    async def test_an_organization_with_no_members_is_absent_not_zero(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(self.URL, headers=self._as("console"))
        assert response.status_code == 200
        assert str(org.id) not in {row["organization_id"] for row in response.json()["counts"]}

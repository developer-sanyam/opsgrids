"""The service-to-service route console's org-detail composite calls:
`GET /admin/directory/organizations/{id}`.

The one read into account-service's directory data console is allowed to make for this —
never `/internal/v1`, which authorizes no individual caller at all (ADR-0003 §2,
`app/api/admin.py`'s module docstring). Since MINOR 8 of the security review, this is a
**service-token** route, not a bearer one: console asserts the calling operator's identity
(`sub`, `operator_roles`, `amr`)
in a token addressed to account (`aud="account"`, `caller="console"`) rather than forwarding the
operator's own bearer token, and this service re-checks the assertion itself. Properties:

1. **Only console may call this** — a service token from anywhere else is refused, whatever it
   asserts.
2. **The assertion is re-checked, not merely trusted** — missing/wrong role, or no completed
   second factor, is refused even though the token itself is genuine and from console.
3. **Cross-tenant by design** — the read runs on the directory session and finds an organization
   regardless of which "tenant" would otherwise be bound, because there is no principal at all.
4. **The read is audited**, bound to the target organization, attributed to the asserted operator.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import AuditLog, Organization
from platform_core.internal_auth import mint_service_token

pytestmark = pytest.mark.integration


def _token(
    *,
    caller: str = "console",
    audience: str = "account",
    operator_sub: uuid.UUID | None = None,
    roles: list[str] | None = None,
    amr: list[str] | None = None,
) -> str:
    claims: dict[str, object] = {}
    if operator_sub is not None:
        claims["operator_sub"] = str(operator_sub)
    if roles is not None:
        claims["operator_roles"] = roles
    if amr is not None:
        claims["operator_amr"] = amr
    return mint_service_token(settings(), audience=audience, caller=caller, claims=claims)


def _headers(**kwargs: object) -> dict[str, str]:
    return {"X-Internal-Token": _token(**kwargs)}  # type: ignore[arg-type]


class TestAdminDirectoryRoute:
    async def test_console_reads_the_organizations_directory_record(
        self, client: AsyncClient, org: Organization
    ) -> None:
        operator_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(operator_sub=operator_id, roles=["owner"], amr=["pwd", "otp"]),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(org.id)
        assert body["slug"] == org.slug

    async def test_no_token_at_all_is_refused(self, client: AsyncClient, org: Organization) -> None:
        response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
        assert response.status_code == 401

    async def test_a_service_token_not_addressed_to_account_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(
                audience="billing", operator_sub=uuid.uuid4(), roles=["owner"], amr=["otp"]
            ),
        )
        assert response.status_code == 401

    async def test_a_service_token_from_a_caller_other_than_console_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(
                caller="billing", operator_sub=uuid.uuid4(), roles=["owner"], amr=["otp"]
            ),
        )
        assert response.status_code == 401

    async def test_a_token_with_no_operator_assertion_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}", headers=_headers()
        )
        assert response.status_code == 401

    async def test_a_role_outside_the_read_set_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(operator_sub=uuid.uuid4(), roles=[], amr=["pwd", "otp"]),
        )
        assert response.status_code == 403

    async def test_an_asserted_session_with_no_completed_second_factor_is_refused(
        self, client: AsyncClient, org: Organization
    ) -> None:
        """The assertion is re-checked, not merely trusted: even a genuine console-issued
        token is refused if it asserts a session that never proved a second factor."""
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(operator_sub=uuid.uuid4(), roles=["owner"], amr=["pwd"]),
        )
        assert response.status_code == 403

    async def test_every_operator_role_may_read(
        self, client: AsyncClient, org: Organization
    ) -> None:
        for roles in (["owner"], ["billing_ops"], ["support"]):
            response = await client.get(
                f"/api/v1/admin/directory/organizations/{org.id}",
                headers=_headers(operator_sub=uuid.uuid4(), roles=roles, amr=["pwd", "otp"]),
            )
            assert response.status_code == 200, roles

    async def test_an_unknown_organization_is_404(self, client: AsyncClient) -> None:
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{uuid.uuid4()}",
            headers=_headers(operator_sub=uuid.uuid4(), roles=["owner"], amr=["pwd", "otp"]),
        )
        assert response.status_code == 404

    async def test_the_read_is_audited_and_attributed_to_the_asserted_operator(
        self, client: AsyncClient, session, org: Organization
    ) -> None:
        operator_id = uuid.uuid4()
        response = await client.get(
            f"/api/v1/admin/directory/organizations/{org.id}",
            headers=_headers(operator_sub=operator_id, roles=["owner"], amr=["pwd", "otp"]),
        )
        assert response.status_code == 200

        rows = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == org.id, AuditLog.action == "organization.viewed"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_id == operator_id
        assert rows[0].acting_as == f"operator:{operator_id}"

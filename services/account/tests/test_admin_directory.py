"""The operator staff route console's org-detail composite calls:
`GET /admin/directory/organizations/{id}`.

The one staff read into account-service's directory data, and the one route console is allowed
to call for it — never `/internal/v1`, which authorizes no individual caller at all (ADR-0003
§2, `app/api/admin.py`'s module docstring). Three properties:

1. **Audience boundary** — a tenant (`app`-audience) token, or none, is refused with 404, the
   same answer an unknown route gives.
2. **Role matrix** — every operator role may read (ADR-0003 addendum: org read is open to all
   three), so there is no role-mismatch case here the way catalog's writes have one.
3. **Cross-tenant by design** — the read runs on the directory session and finds an organization
   regardless of which "tenant" the caller might otherwise be bound to, because an operator is
   bound to none.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.main import app
from app.models import Organization
from platform_core.auth import Principal, get_principal

pytestmark = pytest.mark.integration


def _operator(*, staff: bool = True, roles: list[str] | None = None) -> Principal:
    return Principal(
        sub=uuid.uuid4(),
        organization_id=uuid.UUID(int=0),
        audience="operator" if staff else "app",
        roles=roles if roles is not None else ["support"],
    )


class TestAdminDirectoryRoute:
    async def test_an_operator_reads_the_organizations_directory_record(
        self, client: AsyncClient, org: Organization
    ) -> None:
        app.dependency_overrides[get_principal] = lambda: _operator()
        try:
            response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(org.id)
        assert body["slug"] == org.slug

    async def test_a_product_audience_token_gets_404_not_403(
        self, client: AsyncClient, org: Organization
    ) -> None:
        app.dependency_overrides[get_principal] = lambda: _operator(staff=False)
        try:
            response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 404

    async def test_an_unauthenticated_caller_is_rejected(
        self, client: AsyncClient, org: Organization
    ) -> None:
        response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
        assert response.status_code == 401

    async def test_every_operator_role_may_read(
        self, client: AsyncClient, org: Organization
    ) -> None:
        for roles in (["owner"], ["billing_ops"], ["support"]):
            app.dependency_overrides[get_principal] = lambda roles=roles: _operator(roles=roles)
            try:
                response = await client.get(f"/api/v1/admin/directory/organizations/{org.id}")
            finally:
                app.dependency_overrides.pop(get_principal, None)
            assert response.status_code == 200, roles

    async def test_an_unknown_organization_is_404(self, client: AsyncClient) -> None:
        app.dependency_overrides[get_principal] = lambda: _operator()
        try:
            response = await client.get(f"/api/v1/admin/directory/organizations/{uuid.uuid4()}")
        finally:
            app.dependency_overrides.pop(get_principal, None)
        assert response.status_code == 404

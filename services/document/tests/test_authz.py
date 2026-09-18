"""Negative authorization on every route, and cross-tenant isolation under real RLS.

ADR-0003 §3's rule, applied per endpoint: a permission *name* without enough scope is not
access, and a UI gate is not a control. Every route in this service carries a test that it
refuses the caller it should — and another organization's rows are invisible to the list, the
detail and the delete alike, even when the ids are known.

Reading another tenant's *object* is deliberately absent from this file, because it is not a
violation: every object in the bucket is world-readable by design, so "tenant A fetched tenant
B's image" is the same event as "a member of the public fetched it" (ADR-0009 §3). The three
things that **are** violations — list, overwrite and delete — are what is tested here and in
`test_assets.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset
from tests.helpers import READ, WRITE, make_image, principal_for

pytestmark = pytest.mark.integration


async def _seed_asset(
    admin_session: AsyncSession, org: uuid.UUID, *, size_bytes: int = 1234
) -> Asset:
    """Write one asset directly on the BYPASSRLS role, for an organization the caller is not in."""
    asset = Asset(
        id=uuid.uuid4(),
        organization_id=org,
        content_type="image/png",
        size_bytes=size_bytes,
        width=10,
        height=10,
        checksum_sha256="0" * 64,
        original_filename="theirs.png",
        storage_key="",
    )
    asset.storage_key = f"{org}/{asset.id}"
    admin_session.add(asset)
    await admin_session.commit()
    return asset


# --------------------------------------------------------------------- endpoint-level authz


class TestEndpointAuthorization:
    async def test_unauthenticated_upload_is_rejected(self, client) -> None:
        response = await client.post(
            "/api/v1/documents/assets", files={"file": ("a.png", make_image("PNG"), "image/png")}
        )
        assert response.status_code == 401

    async def test_unauthenticated_list_is_rejected(self, client) -> None:
        assert (await client.get("/api/v1/documents/assets")).status_code == 401

    async def test_unauthenticated_detail_is_rejected(self, client) -> None:
        response = await client.get(f"/api/v1/documents/assets/{uuid.uuid4()}")
        assert response.status_code == 401

    async def test_unauthenticated_delete_is_rejected(self, client) -> None:
        response = await client.delete(f"/api/v1/documents/assets/{uuid.uuid4()}")
        assert response.status_code == 401

    async def test_upload_requires_the_write_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("a.png", make_image("PNG"), "image/png")},
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_list_requires_the_read_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{WRITE}:ORG"])):
            response = await client.get("/api/v1/documents/assets")
        assert response.status_code == 403

    async def test_detail_requires_the_read_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [])):
            response = await client.get(f"/api/v1/documents/assets/{uuid.uuid4()}")
        assert response.status_code == 403

    async def test_delete_requires_the_write_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        """Read is not enough to remove. There is no separate `delete` permission — anyone who
        can upload can also take an image down — but read alone cannot (ADR-0009 §4)."""
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.delete(f"/api/v1/documents/assets/{uuid.uuid4()}")
        assert response.status_code == 403

    async def test_purge_needs_no_more_than_write_but_still_needs_write(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.delete(f"/api/v1/documents/assets/{uuid.uuid4()}?purge=true")
        assert response.status_code == 403


class TestTheScopeFloor:
    """Both permissions are ceilinged **and** defaulted at ORG in the catalogue, so a narrower
    grant should not exist. The route floors them anyway: the ceiling is catalogue data a later
    edit can widen, and the floor is code in front of the query."""

    @pytest.mark.parametrize("scope", ["OWN", "TEAM", "LOCATION"])
    async def test_a_narrower_grant_is_refused_on_the_list(
        self, client, as_principal, org_id: uuid.UUID, scope: str
    ) -> None:
        with as_principal(principal_for(org_id, [f"{READ}:{scope}"])):
            response = await client.get("/api/v1/documents/assets")
        assert response.status_code == 403
        assert "ORG scope" in response.json()["detail"]

    @pytest.mark.parametrize("scope", ["OWN", "TEAM", "LOCATION"])
    async def test_a_narrower_grant_is_refused_on_upload(
        self, client, as_principal, org_id: uuid.UUID, scope: str
    ) -> None:
        with as_principal(principal_for(org_id, [f"{WRITE}:{scope}"])):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("a.png", make_image("PNG"), "image/png")},
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- cross-tenant


class TestCrossTenantIsolation:
    async def test_another_organizations_asset_is_invisible_to_the_list(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        await _seed_asset(admin_session, other_org)
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get("/api/v1/documents/assets")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        # The usage figure must not leak the other tenant's consumption either.
        assert body["usage"]["bytes_used"] == 0

    async def test_another_organizations_asset_is_a_404_not_a_403(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """404, never 403: confirming that an id exists in *some* organization is itself a
        disclosure, and a 403 would confirm it."""
        theirs = await _seed_asset(admin_session, other_org)
        with as_principal(principal_for(org_id, [f"{READ}:ORG"])):
            response = await client.get(f"/api/v1/documents/assets/{theirs.id}")
        assert response.status_code == 404

    async def test_deleting_another_organizations_asset_is_refused_and_touches_nothing(
        self,
        client,
        as_principal,
        admin_session,
        store,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """The delete primitive this ADR is most careful about. The caller names a *row id*,
        the row is fetched under RLS, and there is no row — so nothing is looked up in the
        bucket at all, and no key is ever in the caller's hands to name directly."""
        theirs = await _seed_asset(admin_session, other_org)
        store.objects[theirs.storage_key] = (b"their bytes", "image/png")

        with as_principal(principal_for(org_id, [f"{WRITE}:ORG"])):
            response = await client.delete(f"/api/v1/documents/assets/{theirs.id}?purge=true")
        assert response.status_code == 404
        assert store.deleted == []
        assert theirs.storage_key in store.objects

        await admin_session.refresh(theirs)
        assert theirs.deleted_at is None
        assert theirs.purged_at is None

    async def test_row_level_security_hides_the_row_from_the_app_role_itself(
        self, session: AsyncSession, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """RLS is the net, not the filter — so prove the net exists independently of the query.

        This runs the *unfiltered* select on the tenant-bound app role. Without a policy it
        would return the other organization's row; with one it returns nothing, silently, which
        is exactly why the application must also filter and why `assert_tenant` exists.
        """
        theirs = await _seed_asset(admin_session, other_org)

        async with session.begin():
            bound = (
                await session.execute(text("SELECT current_setting('app.organization_id')"))
            ).scalar_one()
            assert bound == str(org_id)
            rows = (await session.execute(select(Asset))).scalars().all()
        assert rows == []

        # And the row really is there, seen by a role that bypasses the policy.
        assert (await admin_session.get(Asset, theirs.id)) is not None

    async def test_row_level_security_refuses_a_write_into_another_tenant(
        self, session: AsyncSession, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        """`WITH CHECK`, not just `USING`. Without it a tenant could insert a row belonging to
        somebody else — invisible to them, fully visible to the victim."""
        from sqlalchemy.exc import DBAPIError

        asset = Asset(
            id=uuid.uuid4(),
            organization_id=other_org,
            content_type="image/png",
            size_bytes=1,
            width=1,
            height=1,
            checksum_sha256="0" * 64,
            storage_key=f"{other_org}/x",
        )
        with pytest.raises(DBAPIError):
            async with session.begin():
                session.add(asset)

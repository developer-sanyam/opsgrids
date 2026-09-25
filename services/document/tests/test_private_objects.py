"""Private documents, end to end against real PostgreSQL with the private bucket faked.

The public asset suite (`test_assets.py`, `test_authz.py`) covers the world-readable half of
ADR-0009. This is the private half (§2, Q1): a payslip, a contract, an ID scan — presigned
PUT/POST for the upload, a verified `complete`, a short-lived signed GET after authorization,
and a delete that only works before anything is attached.

What is invisible to a happy-path check and worth its own test here specifically: `complete`
never trusts what `initiate` declared (a lying `Content-Type` is refused even though the
presigned POST's own condition let the bytes through), scope defaults to OWN rather than ORG
(the opposite ladder from the public assets), and cross-tenant / cross-owner reads are a 404,
never a 403.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, PrivateObject
from tests.helpers import (
    FAKE_PDF_BYTES,
    PDF_BYTES,
    PRIVATE_READ,
    PRIVATE_WRITE,
    principal_for,
)

pytestmark = pytest.mark.integration

BOTH_OWN = [f"{PRIVATE_READ}:OWN", f"{PRIVATE_WRITE}:OWN"]


def _own_principal(org_id: uuid.UUID, subject_id: uuid.UUID):
    """`subject_id` doubles as both `sub` and `subject_ref` — this test suite always tests one
    actor at a time acting on their own upload, so the two identities coincide by construction
    here even though they are distinct concepts in general (`helpers.principal_for`'s own note).
    """
    return principal_for(org_id, BOTH_OWN, user_id=subject_id, subject_ref=subject_id)


async def _initiate(
    client, as_principal, org_id, user_id, *, purpose="payroll.payslip", size=len(PDF_BYTES)
):
    with as_principal(_own_principal(org_id, user_id)):
        return await client.post(
            "/api/v1/documents/private-objects/initiate",
            json={
                "filename": "payslip.pdf",
                "content_type": "application/pdf",
                "size_bytes": size,
                "purpose": purpose,
            },
        )


async def _initiate_and_seed(
    client, as_principal, private_store, org_id, user_id, data: bytes = PDF_BYTES
):
    response = await _initiate(client, as_principal, org_id, user_id, size=len(data))
    assert response.status_code == 201
    body = response.json()
    row_id = uuid.UUID(body["id"])
    key = f"{org_id}/payroll/{row_id}"
    private_store.seed_upload(key, data)
    return row_id


class TestInitiate:
    async def test_returns_a_presigned_post_and_a_pending_row(
        self, client, as_principal, session: AsyncSession, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        response = await _initiate(client, as_principal, org_id, actor_id)
        assert response.status_code == 201
        body = response.json()
        assert body["upload_url"]
        assert "key" in body["upload_fields"]
        assert body["upload_fields"]["Content-Type"] == "application/pdf"

        row = await session.get(PrivateObject, uuid.UUID(body["id"]))
        assert row is not None
        assert row.status == "pending"
        assert row.owner_service == "payroll"
        assert row.uploaded_by_user_id == actor_id

    async def test_an_oversized_declaration_is_413(
        self, client, as_principal, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        response = await _initiate(client, as_principal, org_id, actor_id, size=999_999_999)
        assert response.status_code == 413
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_a_malformed_purpose_is_422(
        self, client, as_principal, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        response = await _initiate(client, as_principal, org_id, actor_id, purpose="not-a-purpose")
        assert response.status_code == 422

    async def test_unknown_fields_are_rejected(
        self, client, as_principal, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.post(
                "/api/v1/documents/private-objects/initiate",
                json={
                    "filename": "x.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 10,
                    "purpose": "payroll.payslip",
                    "visibility": "private",
                },
            )
        assert response.status_code == 422


class TestComplete:
    async def test_a_real_pdf_is_verified_and_stored(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "stored"
        assert body["content_type"] == "application/pdf"
        assert body["size_bytes"] == len(PDF_BYTES)
        assert body["checksum_sha256"]

        row = await session.get(PrivateObject, row_id)
        assert row is not None
        assert row.status == "stored"
        assert row.completed_at is not None

    async def test_a_lying_content_type_is_refused_415(
        self,
        client,
        as_principal,
        private_store,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Declared `application/pdf` at `initiate`; the bytes that actually land in the bucket
        are HTML. The presigned POST's own `Content-Type` condition constrains what the client
        *declared*, never what the bytes are — `complete`'s sniff is the only check that catches
        this, and it must."""
        row_id = await _initiate_and_seed(
            client, as_principal, private_store, org_id, actor_id, data=FAKE_PDF_BYTES
        )
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
        assert response.status_code == 415
        assert response.headers["content-type"].startswith("application/problem+json")
        # The rejected object must not be left behind under a key a row still claims.
        key = f"{org_id}/payroll/{row_id}"
        assert key not in private_store.objects

    async def test_completing_before_the_upload_arrives_is_422(
        self, client, as_principal, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        response = await _initiate(client, as_principal, org_id, actor_id)
        row_id = uuid.UUID(response.json()["id"])
        with as_principal(_own_principal(org_id, actor_id)):
            complete_response = await client.post(
                f"/api/v1/documents/private-objects/{row_id}/complete"
            )
        assert complete_response.status_code == 422

    async def test_completing_twice_is_idempotent(
        self, client, as_principal, private_store, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        with as_principal(_own_principal(org_id, actor_id)):
            first = await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
            second = await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

    async def test_an_expired_upload_window_is_refused(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        async with session.begin():
            row = await session.get(PrivateObject, row_id)
            assert row is not None
            row.upload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
        assert response.status_code == 422


class TestDownload:
    async def test_get_issues_a_signed_url_and_audits_a_sensitive_read(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        with as_principal(_own_principal(org_id, actor_id)):
            await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
            response = await client.get(f"/api/v1/documents/private-objects/{row_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["download_url"].startswith("https://")
        assert body["download_url_expires_at"]

        audits = (
            (
                await session.execute(
                    AuditLog.__table__.select().where(AuditLog.action == "entity.sensitive_read")
                )
            )
            .mappings()
            .all()
        )
        assert any(a["entity_id"] == row_id for a in audits)

    async def test_a_pending_object_has_nothing_to_download(
        self, client, as_principal, org_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        response = await _initiate(client, as_principal, org_id, actor_id)
        row_id = response.json()["id"]
        with as_principal(_own_principal(org_id, actor_id)):
            get_response = await client.get(f"/api/v1/documents/private-objects/{row_id}")
        assert get_response.status_code == 404


class TestDelete:
    async def test_delete_removes_the_object_and_the_row(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        key = f"{org_id}/payroll/{row_id}"
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.delete(f"/api/v1/documents/private-objects/{row_id}")
        assert response.status_code == 204
        assert key not in private_store.objects
        assert await session.get(PrivateObject, row_id) is None

    async def test_an_attached_object_cannot_be_deleted(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, actor_id)
        async with session.begin():
            row = await session.get(PrivateObject, row_id)
            assert row is not None
            row.attached_at = datetime.now(UTC)
            row.owner_aggregate_id = uuid.uuid4()
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.delete(f"/api/v1/documents/private-objects/{row_id}")
        assert response.status_code == 409


# --------------------------------------------------------------------- authorization


class TestEndpointAuthorization:
    async def test_unauthenticated_initiate_is_401(self, client) -> None:
        response = await client.post(
            "/api/v1/documents/private-objects/initiate",
            json={
                "filename": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes": 10,
                "purpose": "payroll.payslip",
            },
        )
        assert response.status_code == 401

    async def test_initiate_requires_the_write_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{PRIVATE_READ}:OWN"])):
            response = await client.post(
                "/api/v1/documents/private-objects/initiate",
                json={
                    "filename": "x.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 10,
                    "purpose": "payroll.payslip",
                },
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_get_requires_the_read_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{PRIVATE_WRITE}:OWN"])):
            response = await client.get(f"/api/v1/documents/private-objects/{uuid.uuid4()}")
        assert response.status_code == 403

    async def test_delete_requires_the_write_permission(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [f"{PRIVATE_READ}:OWN"])):
            response = await client.delete(f"/api/v1/documents/private-objects/{uuid.uuid4()}")
        assert response.status_code == 403

    async def test_a_caller_with_no_grant_at_all_is_403(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, [])):
            response = await client.get(f"/api/v1/documents/private-objects/{uuid.uuid4()}")
        assert response.status_code == 403


class TestOwnScopeAndCrossTenant:
    async def test_one_members_own_grant_does_not_reach_a_colleagues_upload(
        self,
        client,
        as_principal,
        private_store,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
        other_org: uuid.UUID,
    ) -> None:
        """A second uploader (`colleague_id`) in the *same* organization proves the OWN filter
        itself (`owner_subject_ref`), rather than tenant isolation (already covered below)."""
        colleague_id = uuid.uuid4()
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, colleague_id)
        with as_principal(_own_principal(org_id, actor_id)):
            response = await client.get(f"/api/v1/documents/private-objects/{row_id}")
        assert response.status_code == 404

    async def test_an_org_scoped_admin_reaches_every_uploader(
        self,
        client,
        as_principal,
        private_store,
        session: AsyncSession,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        member_id = uuid.uuid4()
        row_id = await _initiate_and_seed(client, as_principal, private_store, org_id, member_id)
        with as_principal(_own_principal(org_id, member_id)):
            await client.post(f"/api/v1/documents/private-objects/{row_id}/complete")
        with as_principal(
            principal_for(
                org_id,
                [f"{PRIVATE_READ}:ORG", f"{PRIVATE_WRITE}:ORG"],
                user_id=actor_id,
            )
        ):
            response = await client.get(f"/api/v1/documents/private-objects/{row_id}")
        assert response.status_code == 200

    async def test_another_organizations_object_is_a_404_not_a_403(
        self,
        client,
        as_principal,
        admin_session,
        org_id: uuid.UUID,
        other_org: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        theirs = PrivateObject(
            id=uuid.uuid4(),
            organization_id=other_org,
            storage_key=f"{other_org}/payroll/x",
            purpose="payroll.payslip",
            owner_service="payroll",
            status="stored",
            declared_content_type="application/pdf",
            declared_size_bytes=10,
            content_type="application/pdf",
            size_bytes=10,
            checksum_sha256="0" * 64,
            uploaded_by_user_id=actor_id,
            upload_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        admin_session.add(theirs)
        await admin_session.commit()

        with as_principal(principal_for(org_id, [f"{PRIVATE_READ}:ORG"], user_id=actor_id)):
            response = await client.get(f"/api/v1/documents/private-objects/{theirs.id}")
        assert response.status_code == 404

    async def test_row_level_security_hides_another_tenants_row_from_the_app_role(
        self, session: AsyncSession, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        from sqlalchemy import select

        theirs = PrivateObject(
            id=uuid.uuid4(),
            organization_id=other_org,
            storage_key=f"{other_org}/payroll/x",
            purpose="payroll.payslip",
            owner_service="payroll",
            declared_content_type="application/pdf",
            declared_size_bytes=10,
            upload_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        admin_session.add(theirs)
        await admin_session.commit()

        async with session.begin():
            rows = (await session.execute(select(PrivateObject))).scalars().all()
        assert rows == []
        assert (await admin_session.get(PrivateObject, theirs.id)) is not None

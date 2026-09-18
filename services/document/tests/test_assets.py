"""The four routes, end to end against real PostgreSQL with the object store faked.

What this suite is for, specifically: the properties that are invisible to a happy-path check
and expensive to discover later — the **order** of the two writes an upload performs, that a
soft delete leaves the object alone while freeing quota, that a purge does the opposite, and
that the size limit holds against a client that lies about `Content-Length`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Asset, AuditLog
from tests.helpers import READ, WRITE, make_image, principal_for

pytestmark = pytest.mark.integration

BOTH = [f"{READ}:ORG", f"{WRITE}:ORG"]


async def _upload(client, as_principal, org_id: uuid.UUID, raw: bytes, name: str = "hero.jpg"):
    with as_principal(principal_for(org_id, BOTH)):
        return await client.post(
            "/api/v1/documents/assets", files={"file": (name, raw, "image/jpeg")}
        )


class TestUpload:
    async def test_a_jpeg_is_stored_and_described(
        self, client, as_principal, store, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        response = await _upload(client, as_principal, org_id, make_image("JPEG", size=(800, 600)))
        assert response.status_code == 201
        body = response.json()

        asset_id = body["id"]
        assert body["public_path"] == f"/a/{org_id}/{asset_id}"
        assert body["public_url"].endswith(f"/{org_id}/{asset_id}")
        assert body["content_type"] == "image/jpeg"
        assert (body["width"], body["height"]) == (800, 600)
        assert body["original_filename"] == "hero.jpg"
        assert body["deleted_at"] is None and body["purged_at"] is None

        stored, content_type = store.objects[f"{org_id}/{asset_id}"]
        assert content_type == "image/jpeg"
        assert body["size_bytes"] == len(stored)

    async def test_the_declared_content_type_and_extension_are_ignored(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """A PNG sent as `logo.jpg` with `Content-Type: image/jpeg` is stored as a PNG.

        The route never consults either; the decoder decides, and the object's metadata — which
        is what S3 actually serves to a browser — carries what the decoder said.
        """
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("logo.jpg", make_image("PNG"), "image/jpeg")},
            )
        assert response.status_code == 201
        assert response.json()["content_type"] == "image/png"
        assert store.objects[response.json()["public_path"].removeprefix("/a/")][1] == "image/png"

    async def test_an_svg_is_refused_with_422_naming_what_is_accepted(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        from tests.helpers import SVG_BYTES

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("logo.svg", SVG_BYTES, "image/svg+xml")},
            )
        assert response.status_code == 422
        assert "JPEG, PNG or WebP" in response.json()["detail"]
        assert store.objects == {}

    async def test_a_decompression_bomb_is_refused(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        from tests.helpers import decompression_bomb

        response = await _upload(client, as_principal, org_id, decompression_bomb(), "bomb.png")
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "image_too_many_pixels"
        assert store.objects == {}

    async def test_an_unexpected_form_field_is_refused_rather_than_ignored(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """A client sending `visibility=private` must not get a silent public upload."""
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("a.png", make_image("PNG"), "image/png")},
                data={"visibility": "private"},
            )
        assert response.status_code == 422
        assert "visibility" in response.json()["detail"]
        assert store.objects == {}

    async def test_a_body_with_no_file_part_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post("/api/v1/documents/assets", data={"nope": "1"})
        assert response.status_code == 422

    async def test_an_empty_file_part_is_refused(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets", files={"file": ("a.png", b"", "image/png")}
            )
        assert response.status_code == 422

    async def test_an_audit_row_is_written_in_the_same_transaction(
        self, client, as_principal, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        response = await _upload(client, as_principal, org_id, make_image("JPEG"))
        asset_id = uuid.UUID(response.json()["id"])

        async with session.begin():
            row = (
                await session.execute(select(AuditLog).where(AuditLog.entity_id == asset_id))
            ).scalar_one()
        assert row.action == "asset.uploaded"
        assert row.after["storage_key"] == f"{org_id}/{asset_id}"
        assert row.after["original_filename"] == "hero.jpg"


class TestTheSizeLimit:
    """Enforced twice, because `Content-Length` is client-supplied and a chunked upload does
    not have to send one at all (ADR-0009 §5)."""

    async def test_an_oversized_upload_is_refused_on_content_length(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        cfg = settings()
        oversized = b"\xff" * (cfg.asset_max_upload_bytes + 1024 * 1024)
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets",
                files={"file": ("big.jpg", oversized, "image/jpeg")},
            )
        assert response.status_code == 413
        assert store.objects == {}

    async def test_an_oversized_upload_is_refused_when_it_lies_about_its_length(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """The same body sent chunked, with no `Content-Length` at all. The declared-length
        check cannot fire, and the stream counter has to — which is the entire reason there
        are two checks rather than one.
        """
        cfg = settings()
        boundary = "----opsgridsboundary"
        head = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        payload = b"\xff" * (cfg.asset_max_upload_bytes + 1024 * 1024)

        async def _chunks():
            yield head
            for offset in range(0, len(payload), 64 * 1024):
                yield payload[offset : offset + 64 * 1024]
            yield tail

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.post(
                "/api/v1/documents/assets",
                content=_chunks(),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        assert "content-length" not in {k.lower() for k in response.request.headers}
        assert response.status_code == 413
        assert store.objects == {}


class TestWriteOrder:
    """ADR-0009 §9: object first, row second, and the orphan that deliberately chooses."""

    async def test_a_failed_object_write_leaves_no_row_and_returns_503(
        self, client, as_principal, store, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        store.fail_put = True
        response = await _upload(client, as_principal, org_id, make_image("JPEG"))
        assert response.status_code == 503

        async with session.begin():
            assert (
                await session.execute(select(func.count()).select_from(Asset))
            ).scalar_one() == 0
            assert (
                await session.execute(select(func.count()).select_from(AuditLog))
            ).scalar_one() == 0

    async def test_a_failed_row_write_leaves_an_orphan_object_and_no_row(
        self,
        client,
        as_principal,
        store,
        session: AsyncSession,
        org_id: uuid.UUID,
        monkeypatch,
    ) -> None:
        """The failure this ordering deliberately chooses.

        An orphan object is invisible, unreachable without the key, and sweepable. The
        alternative ordering's failure — a library row whose image is permanently broken — is
        visible, confusing, and self-healing never.
        """
        from app import services

        def _explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("row write failed after the object was written")

        monkeypatch.setattr(services, "record_audit", _explode)

        with pytest.raises(RuntimeError):
            await _upload(client, as_principal, org_id, make_image("JPEG"))

        assert len(store.objects) == 1, "the object was written before the row failed"
        async with session.begin():
            assert (
                await session.execute(select(func.count()).select_from(Asset))
            ).scalar_one() == 0


class TestTheLibraryListing:
    async def test_it_is_newest_first_and_paginated(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        for index in range(3):
            assert (
                await _upload(client, as_principal, org_id, make_image("PNG"), f"{index}.png")
            ).status_code == 201

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.get("/api/v1/documents/assets?size=2")
        body = response.json()
        assert response.status_code == 200
        assert body["total"] == 3
        assert body["pages"] == 2
        assert len(body["items"]) == 2
        assert [item["original_filename"] for item in body["items"]] == ["2.png", "1.png"]

    async def test_the_page_size_is_capped_by_the_server(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        """There is no way to ask this endpoint for the whole library."""
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.get("/api/v1/documents/assets?size=5000")
        assert response.status_code == 422

    async def test_it_carries_the_organizations_usage(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        upload = await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")
        with as_principal(principal_for(org_id, BOTH)):
            body = (await client.get("/api/v1/documents/assets")).json()
        cfg = settings()
        assert body["usage"]["bytes_used"] == upload.json()["size_bytes"]
        assert body["usage"]["asset_count"] == 1
        assert body["usage"]["bytes_limit"] == cfg.asset_quota_bytes
        assert body["usage"]["asset_count_limit"] == cfg.asset_quota_count


class TestDeletion:
    async def test_soft_delete_leaves_the_object_and_frees_quota(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """ "Delete" in a merchant's head means "take it out of my library", not "break my live
        store" — so the object is untouched and the public URL keeps working."""
        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")).json()
        key = created["public_path"].removeprefix("/a/")

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.delete(f"/api/v1/documents/assets/{created['id']}")
        assert response.status_code == 200
        assert response.json()["deleted_at"] is not None
        assert response.json()["purged_at"] is None

        assert key in store.objects, "the object must survive a library removal"
        assert store.deleted == []

        with as_principal(principal_for(org_id, BOTH)):
            listing = (await client.get("/api/v1/documents/assets")).json()
        assert listing["total"] == 0
        assert listing["usage"]["bytes_used"] == 0, "soft delete frees quota immediately"

        with as_principal(principal_for(org_id, BOTH)):
            detail = await client.get(f"/api/v1/documents/assets/{created['id']}")
        assert detail.status_code == 404

    async def test_purge_removes_the_object_immediately(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """The case the soft path cannot serve: the wrong file is already world-readable, and
        "wait 30 days" is not an answer."""
        created = (
            await _upload(client, as_principal, org_id, make_image("PNG"), "oops.png")
        ).json()
        key = created["public_path"].removeprefix("/a/")

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.delete(f"/api/v1/documents/assets/{created['id']}?purge=true")
        assert response.status_code == 200
        assert response.json()["purged_at"] is not None
        assert key not in store.objects
        assert store.deleted == [key]

    async def test_an_already_soft_deleted_asset_can_still_be_purged(
        self, client, as_principal, store, org_id: uuid.UUID
    ) -> None:
        """The merchant who removed it an hour ago and has just realised it is still public.

        If purge could only reach live rows, the one case it was built for would be the one it
        could not serve.
        """
        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "id.png")).json()
        key = created["public_path"].removeprefix("/a/")

        with as_principal(principal_for(org_id, BOTH)):
            await client.delete(f"/api/v1/documents/assets/{created['id']}")
            assert key in store.objects
            response = await client.delete(f"/api/v1/documents/assets/{created['id']}?purge=true")
        assert response.status_code == 200
        assert key not in store.objects

    async def test_delete_is_idempotent_and_writes_one_audit_row(
        self, client, as_principal, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")).json()
        asset_id = uuid.UUID(created["id"])

        with as_principal(principal_for(org_id, BOTH)):
            first = await client.delete(f"/api/v1/documents/assets/{asset_id}")
            second = await client.delete(f"/api/v1/documents/assets/{asset_id}")
        assert first.status_code == second.status_code == 200
        assert first.json()["deleted_at"] == second.json()["deleted_at"]

        async with session.begin():
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "asset.removed")
                )
            ).scalar_one()
        assert count == 1, "a retry is not a second act"

    async def test_the_audit_row_distinguishes_a_purge_from_a_removal(
        self, client, as_principal, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")).json()
        with as_principal(principal_for(org_id, BOTH)):
            await client.delete(f"/api/v1/documents/assets/{created['id']}?purge=true")

        async with session.begin():
            row = (
                await session.execute(select(AuditLog).where(AuditLog.action == "asset.removed"))
            ).scalar_one()
        assert row.after["purge"] is True
        assert row.after["purged_at"] is not None
        assert row.before["deleted_at"] is None

    async def test_a_failed_object_delete_leaves_purged_at_null_for_the_sweep_to_retry(
        self, client, as_principal, store, session: AsyncSession, org_id: uuid.UUID
    ) -> None:
        """A row claiming the object is gone while it is still world-readable is the one lie
        this table must never tell."""
        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")).json()
        store.fail_delete = True

        with as_principal(principal_for(org_id, BOTH)):
            response = await client.delete(f"/api/v1/documents/assets/{created['id']}?purge=true")
        assert response.status_code == 503

        async with session.begin():
            asset = (await session.execute(select(Asset))).scalar_one()
        assert asset.purged_at is None
        assert asset.deleted_at is None

    async def test_deleting_an_unknown_id_is_a_404(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(principal_for(org_id, BOTH)):
            response = await client.delete(f"/api/v1/documents/assets/{uuid.uuid4()}")
        assert response.status_code == 404


class TestQuota:
    async def test_over_quota_is_a_422_that_names_the_limit(
        self, client, as_principal, store, org_id: uuid.UUID, monkeypatch
    ) -> None:
        """422 and not 402: the subscription entitles the product, and this is a domain rule
        inside an entitled product (ADR-0004 §5)."""
        cfg = settings()
        monkeypatch.setattr(cfg, "asset_quota_bytes", 1)

        response = await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")
        assert response.status_code == 422
        assert "storage limit" in response.json()["detail"]
        assert store.objects == {}, "nothing is written when the quota refuses"

    async def test_the_asset_count_ceiling_is_enforced(
        self, client, as_principal, org_id: uuid.UUID, monkeypatch
    ) -> None:
        cfg = settings()
        monkeypatch.setattr(cfg, "asset_quota_count", 1)

        assert (
            await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")
        ).status_code == 201
        second = await _upload(client, as_principal, org_id, make_image("PNG"), "b.png")
        assert second.status_code == 422
        assert "limit of 1 images" in second.json()["detail"]

    async def test_a_soft_deleted_asset_stops_counting(
        self, client, as_principal, org_id: uuid.UUID, monkeypatch
    ) -> None:
        cfg = settings()
        monkeypatch.setattr(cfg, "asset_quota_count", 1)

        created = (await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")).json()
        with as_principal(principal_for(org_id, BOTH)):
            await client.delete(f"/api/v1/documents/assets/{created['id']}")

        assert (
            await _upload(client, as_principal, org_id, make_image("PNG"), "b.png")
        ).status_code == 201


class TestTheRateLimit:
    async def test_the_upload_route_is_limited_per_organization(
        self, client, as_principal, org_id: uuid.UUID, monkeypatch
    ) -> None:
        """The only route in this service that does CPU work is the only one with a limit in
        front of it (ADR-0009 §11)."""
        cfg = settings()
        monkeypatch.setattr(cfg, "asset_upload_rate_limit_per_minute", 1)

        assert (
            await _upload(client, as_principal, org_id, make_image("PNG"), "a.png")
        ).status_code == 201
        second = await _upload(client, as_principal, org_id, make_image("PNG"), "b.png")
        assert second.status_code == 429
        assert second.headers["Retry-After"] == "60"

    async def test_the_read_routes_are_not_limited(
        self, client, as_principal, org_id: uuid.UUID, monkeypatch
    ) -> None:
        cfg = settings()
        monkeypatch.setattr(cfg, "asset_upload_rate_limit_per_minute", 1)
        with as_principal(principal_for(org_id, BOTH)):
            for _ in range(5):
                assert (await client.get("/api/v1/documents/assets")).status_code == 200

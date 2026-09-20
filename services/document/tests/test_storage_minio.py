"""The round trip against the real object store. Needs `make infra`.

A green unit suite that has never talked to object storage is not evidence this works. Every
other test in this directory uses `FakeObjectStore`, which proves the service's own logic and
proves **nothing** about the four things only a real bucket can answer:

1. boto3 is configured such that a `PutObject` actually lands (path-style addressing, the
   endpoint override, the signature version, the credentials).
2. The object comes back to an **anonymous** GET, byte for byte, with no credential and no
   signature — the single property ADR-0009 exists to deliver.
3. It comes back with the `Content-Type` the decoder chose, because that metadata is what a
   browser uses to render it and it is the only place the format is recorded (the key has no
   extension).
4. The anonymous policy grants `GetObject` and **not** `ListBucket`. Without that asymmetry the
   tenancy argument in ADR-0009 §3 collapses: an unguessable key is only unguessable if there
   is no listing to walk. This is worth asserting rather than assuming — `mc anonymous set
   download`, the obvious way to set the policy up, grants `ListBucket` too.

Skipped rather than failed when MinIO is not reachable, so `make test` on a machine with no
infrastructure stays green.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.main import app
from app.storage import ObjectStore, get_object_store
from platform_core.cache import close_redis, init_redis
from platform_core.db import get_session
from tests.helpers import READ, WRITE, make_image, make_image_with_gps, principal_for

pytestmark = pytest.mark.integration

BOTH = [f"{READ}:ORG", f"{WRITE}:ORG"]


def _minio_reachable() -> bool:
    cfg = settings()
    if not cfg.s3_endpoint_url:
        return False
    try:
        httpx.get(f"{cfg.s3_endpoint_url}/minio/health/live", timeout=2.0)
    except Exception:
        return False
    return True


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _minio_reachable(), reason="MinIO is not running (make infra)"),
]


@pytest.fixture
def real_store() -> ObjectStore:
    """The genuine article, pointed at whatever `.env` configures."""
    return ObjectStore(settings())


@pytest_asyncio.fixture
async def minio_client(
    session: AsyncSession, real_store: ObjectStore
) -> AsyncIterator[AsyncClient]:
    """The same wiring as the `client` fixture, with the fake store swapped for the real one."""
    init_redis(settings().redis_url)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_object_store] = lambda: real_store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await close_redis()


class TestTheRealBucket:
    async def test_an_uploaded_jpeg_comes_back_from_an_anonymous_get(
        self, minio_client, as_principal, real_store, org_id: uuid.UUID
    ) -> None:
        """Upload through the endpoint, fetch the public URL with no credential, compare bytes.

        This is the whole feature in one assertion: a browser with no `Authorization` header,
        no cookie and no signature gets the image, and the reference it used carries no expiry.
        """
        raw = make_image_with_gps("JPEG")
        with as_principal(principal_for(org_id, BOTH)):
            response = await minio_client.post(
                "/api/v1/documents/assets",
                files={"file": ("storefront-hero.jpg", raw, "image/jpeg")},
            )
        assert response.status_code == 201, response.text
        body = response.json()
        asset_id = body["id"]

        try:
            # The URL shape, derived from two ids and nothing else.
            expected = f"{settings().public_asset_base_url.rstrip('/')}/{org_id}/{asset_id}"
            assert body["public_url"] == expected
            assert body["public_path"] == f"/a/{org_id}/{asset_id}"

            async with httpx.AsyncClient(timeout=10.0) as anon:
                fetched = await anon.get(body["public_url"])

            assert fetched.status_code == 200
            assert fetched.headers["content-type"] == "image/jpeg"
            assert len(fetched.content) == body["size_bytes"]

            # Byte-for-byte what we stored — and *not* what was uploaded, because the EXIF the
            # fixture carries did not survive the re-encode.
            assert fetched.content != raw
            assert b"OpsGrids Test Camera" not in fetched.content

            # Nothing about the request was signed: no query string, no auth header.
            assert "?" not in body["public_url"]
        finally:
            with as_principal(principal_for(org_id, BOTH)):
                await minio_client.delete(f"/api/v1/documents/assets/{asset_id}?purge=true")

    async def test_a_purge_makes_the_public_url_404(
        self, minio_client, as_principal, org_id: uuid.UUID
    ) -> None:
        """The takedown path, against the real bucket: after `?purge=true` the object is gone
        and the URL stops resolving. This is the only remedy a merchant has for publishing the
        wrong file, so "it returns 404 now" has to be true rather than intended."""
        with as_principal(principal_for(org_id, BOTH)):
            created = (
                await minio_client.post(
                    "/api/v1/documents/assets",
                    files={"file": ("oops.png", make_image("PNG"), "image/png")},
                )
            ).json()

        async with httpx.AsyncClient(timeout=10.0) as anon:
            assert (await anon.get(created["public_url"])).status_code == 200

            with as_principal(principal_for(org_id, BOTH)):
                removed = await minio_client.delete(
                    f"/api/v1/documents/assets/{created['id']}?purge=true"
                )
            assert removed.status_code == 200
            assert removed.json()["purged_at"] is not None

            assert (await anon.get(created["public_url"])).status_code == 404

    async def test_a_soft_delete_leaves_the_real_object_fetchable(
        self, minio_client, as_principal, org_id: uuid.UUID
    ) -> None:
        """A published store that references a removed image keeps rendering — against the real
        bucket, not just against a dictionary."""
        with as_principal(principal_for(org_id, BOTH)):
            created = (
                await minio_client.post(
                    "/api/v1/documents/assets",
                    files={"file": ("hero.webp", make_image("WEBP"), "image/webp")},
                )
            ).json()

        try:
            with as_principal(principal_for(org_id, BOTH)):
                removed = await minio_client.delete(f"/api/v1/documents/assets/{created['id']}")
            assert removed.status_code == 200
            assert removed.json()["purged_at"] is None

            async with httpx.AsyncClient(timeout=10.0) as anon:
                still_there = await anon.get(created["public_url"])
            assert still_there.status_code == 200
            assert still_there.headers["content-type"] == "image/webp"
        finally:
            with as_principal(principal_for(org_id, BOTH)):
                await minio_client.delete(f"/api/v1/documents/assets/{created['id']}?purge=true")


class TestTheAnonymousPolicy:
    async def test_the_bucket_cannot_be_listed_anonymously(self) -> None:
        """`s3:GetObject` yes, `s3:ListBucket` no. Without this the unguessable key is
        guessable by enumeration and ADR-0009 §3's tenancy argument does not hold.

        `mc anonymous set download` — the obvious way to configure the bucket — grants
        `ListBucket` as well, which is why `tools/dev-bootstrap/compose.yml` writes the policy
        out by hand and why this assertion exists.
        """
        cfg = settings()
        base = cfg.public_asset_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as anon:
            listing = await anon.get(f"{base}/?list-type=2")
        assert listing.status_code == 403

    async def test_an_unknown_key_is_a_404_not_a_403(self) -> None:
        """Which is itself the proof that `GetObject` is granted: a bucket with no anonymous
        read at all answers 403 for a missing key, because it will not say whether it exists."""
        cfg = settings()
        base = cfg.public_asset_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as anon:
            missing = await anon.get(f"{base}/{uuid.uuid4()}/{uuid.uuid4()}")
        assert missing.status_code == 404

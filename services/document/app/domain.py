"""Pure image validation and normalisation. No I/O, no session, no S3, no settings.

This is ADR-0009 §5 in one function. Every rule here exists because of a specific attack or
a specific silent ruin, and the comments name which.

**The bytes decide.** The declared `Content-Type` and the filename extension are both ignored
for the decision — a file that lies about its type never gets the chance to. Pillow decodes,
and the stored content type is derived from what the decoder says the image is.

**Everything is re-encoded**, which is what makes the rule above structural rather than a
check somebody has to remember. The round trip strips EXIF (including the GPS coordinates a
merchant's phone photo routinely carries), neutralises polyglot files (a thing that is
simultaneously a valid JPEG and a valid HTML document does not survive it), and bounds the
object at 2560 px on the long edge.

**The decompression bomb is the attack a byte limit does not cover**, and it is the one that
gets forgotten: a 5 MiB PNG can decode to tens of gigabytes of pixels. `Image.MAX_IMAGE_PIXELS`
is set explicitly below, because Pillow's default behaviour at that threshold is a *warning* —
worse than either alternative — and the size is checked off the header, before the decode
completes, rather than after.

**One honest cost, named rather than hidden.** PNG and WebP are re-encoded losslessly, so a
noisy photographic PNG can come out of the round trip *larger* than it went in — bounded at
roughly 26 MB by the 2560 px cap, and counted against the quota at its real stored size. The
alternative is lossy re-encoding of a format merchants choose precisely because it is not, so
the amplification is accepted and the quota absorbs it.
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import uuid
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

#: 24 MP, about 6000x4000 — a legal 7000x7000 PNG (49 MP, under the old 50 MP ceiling) measured
#: ~489 MB peak RSS in this function against a 320m container `mem_limit`: decoding a PNG that
#: size fully into memory (Pillow has no partial-decode path for PNG — see the JPEG-only
#: `draft()` note below), resizing it, and re-encoding it, all before the original buffer is
#: freed. Lowered here rather than only capped at the API layer, because the bomb check below
#: runs off the image's declared dimensions before a single pixel is decoded — a request this
#: size now fails fast with a 4xx naming the limit instead of nearly OOMing the process. Set
#: **explicitly** rather than left at Pillow's default, and checked by this module as well:
#: Pillow raises `DecompressionBombError` only at twice this value and merely warns at it, so
#: relying on the library alone means an over-limit image decodes with a logged warning nobody
#: reads.
MAX_IMAGE_PIXELS = 24_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

#: Re-encoding is the only CPU-and-memory-heavy work on any request path in this service
#: (module docstring, ADR-0009 §11). Two uploads decoding a large image at once roughly doubles
#: the peak RSS of one — the OOM this module exists to avoid is a function of concurrency as
#: much as of any single image's size. A semaphore of 1 serialises re-encodes within a process
#: (each API process already runs several, per `services/document/app/api/routes.py`'s
#: `anyio.to_thread.run_sync`), trading a little latency under concurrent uploads for a peak
#: RSS that does not scale with how many merchants happen to upload in the same second. Plain
#: `threading.Semaphore`, not `anyio`'s: the thread pool `run_sync` dispatches to is real OS
#: threads, and this must serialise across those threads, not across async tasks in one of them.
_REENCODE_SEMAPHORE = threading.Semaphore(1)

#: More than any storefront hero needs. Bounds the buyer's download and the re-encode cost.
MAX_LONG_EDGE = 2560

#: The trade is a re-encoded JPEG being very slightly softer than the original.
JPEG_QUALITY = 85

#: Pillow's format name → the content type that gets stored and served. The map *is* the
#: accept-list: a format absent from it is refused, so adding a format is one entry and a
#: matching encode branch, and forgetting the branch is a `KeyError` at import-time review
#: rather than a surprise in production.
ACCEPTED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

ACCEPTED_DESCRIPTION = "JPEG, PNG or WebP"


class ImageRejected(Exception):
    """The bytes are not something this service will store.

    A value, not a surprise: `code` is a stable string the frontend can branch on, and
    `message` is safe to show a merchant verbatim — it names what was accepted and never
    echoes anything the uploader supplied.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """What actually gets stored. The uploader's bytes are not in here."""

    data: bytes
    content_type: str
    width: int
    height: int
    checksum_sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def _open(raw: bytes) -> Image.Image:
    """Decode the header only, and refuse anything that is not one of the three formats.

    `Image.open` is lazy — it reads enough to know the format and the size and stops — which
    is exactly what the bomb check below needs: a decision before the pixels are expanded.
    """
    try:
        image = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError as exc:
        # SVG lands here, permanently and by construction: Pillow has no SVG decoder, so the
        # refusal is not a rule somebody could relax by editing a list.
        raise ImageRejected(
            "unsupported_image",
            f"That file is not an image this service can read. Upload {ACCEPTED_DESCRIPTION}.",
        ) from exc
    except Image.DecompressionBombError as exc:
        raise ImageRejected(
            "image_too_many_pixels",
            f"That image has too many pixels. The limit is {MAX_IMAGE_PIXELS // 1_000_000} "
            "megapixels.",
        ) from exc
    except Exception as exc:  # a truncated or malformed file of an otherwise known format
        raise ImageRejected(
            "unsupported_image",
            f"That file could not be read as an image. Upload {ACCEPTED_DESCRIPTION}.",
        ) from exc

    if image.format not in ACCEPTED_FORMATS:
        raise ImageRejected(
            "unsupported_image_format",
            f"That image format is not supported. Upload {ACCEPTED_DESCRIPTION}.",
        )
    return image


def _refuse_bombs_and_animation(image: Image.Image) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ImageRejected("unsupported_image", "That image has no pixels.")
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageRejected(
            "image_too_many_pixels",
            f"That image is {width}x{height}, which is over the "
            f"{MAX_IMAGE_PIXELS // 1_000_000} megapixel limit.",
        )
    # Animated WebP is refused for the same reason GIF is not accepted at all: the re-encode
    # would silently keep one frame, and refusing is louder than ruining.
    if getattr(image, "n_frames", 1) > 1:
        raise ImageRejected(
            "animated_image",
            "Animated images are not supported. Upload a single-frame image.",
        )


def _encode(image: Image.Image, fmt: str) -> bytes:
    """Re-encode to `fmt`. This is the step that strips EXIF and kills polyglots."""
    buffer = io.BytesIO()
    if fmt == "JPEG":
        # JPEG carries no alpha. Flattening onto white rather than letting Pillow raise is
        # the only branch here that changes pixels the merchant can see, and it only fires
        # for an image whose declared format was already JPEG — i.e. effectively never.
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    elif fmt == "PNG":
        image.save(buffer, format="PNG", optimize=True)
    else:  # WEBP
        image.save(buffer, format="WEBP", lossless=True)
    return buffer.getvalue()


def process_upload(raw: bytes) -> ProcessedImage:
    """Validate, downscale and re-encode. Raises `ImageRejected`; never raises anything else.

    Synchronous and CPU-bound by nature — Pillow is C holding the GIL for most of a
    150-300 ms round trip on a 5 MiB JPEG. The caller runs it in a thread pool
    (`anyio.to_thread.run_sync`), because this is the only CPU work on any request path in
    this service and it must not be the reason `GET /assets` is slow (ADR-0009 §11).
    """
    image = _open(raw)
    fmt = image.format or ""
    _refuse_bombs_and_animation(image)

    try:
        # `draft()` asks the decoder to produce a version close to this size *as it decodes*,
        # rather than decoding at full resolution and discarding most of it on the resize
        # below. It only has an effect for JPEG (and MPO) — Pillow's PNG and WebP plugins have
        # no partial-decode path, which is exactly why the lower `MAX_IMAGE_PIXELS` above
        # carries the rest of the bound for those two formats. Must run before anything below
        # touches a pixel, or the decoder has already committed to full resolution.
        if fmt == "JPEG":
            image.draft("RGB", (MAX_LONG_EDGE, MAX_LONG_EDGE))
        # Orientation is applied *before* the metadata is dropped, and in place: the
        # alternative (`image = ImageOps.exif_transpose(image) or image`) holds the original
        # full-size buffer alive until the rotated copy is fully built, doubling the peak for
        # exactly the images this function most needs to be careful with. Most images have no
        # orientation tag needing correction, in which case this never forces a decode at all;
        # when one does, it decodes at the reduced size `draft()` already asked for.
        ImageOps.exif_transpose(image, in_place=True)
        with _REENCODE_SEMAPHORE:
            image.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            data = _encode(image, fmt)
    except ImageRejected:
        raise
    except Exception as exc:
        # A file that passes the header check and then fails mid-decode is truncated or
        # corrupt. It is the uploader's problem, not a 500.
        raise ImageRejected(
            "unsupported_image",
            f"That image could not be processed. Upload {ACCEPTED_DESCRIPTION}.",
        ) from exc

    width, height = image.size
    return ProcessedImage(
        data=data,
        content_type=ACCEPTED_FORMATS[fmt],
        width=width,
        height=height,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
    )


def storage_key(organization_id: object, asset_id: object) -> str:
    """`{organization_id}/{asset_id}`, both UUIDs, **no extension**.

    The format travels in the object's `Content-Type` metadata, which is what S3 serves, and
    leaving the extension out is what makes the public URL derivable from two ids alone.

    The organization id is in the key for **operations, not isolation** (ADR-0009 §3): a
    prefix delete is a tenant erasure, a per-prefix cost report answers "what does this
    customer cost us", and during an incident an object's tenant is recoverable from the key
    without the database. Isolation is the row policy, and the reason that is enough is that
    every object in this bucket is world-readable anyway.
    """
    return f"{organization_id}/{asset_id}"


def public_path(organization_id: object, asset_id: object) -> str:
    """The root-relative reference a merchant's store document stores (ADR-0009 §6).

    **Not an absolute URL**, because an absolute URL bakes the hostname into merchant data:
    moving MinIO → AWS, renaming the bucket or putting a CDN in front would then be a data
    migration over customer-authored content, to change a hostname.

    The leading `/a` is a sentinel meaning "this is one of our assets", not a path segment of
    the object key — the frontend's `resolveImageSrc` drops it and joins the rest onto
    `ASSET_BASE`, so `/a/{org}/{asset}` resolves to `{ASSET_BASE}/{org}/{asset}` and matches
    `storage_key` exactly.
    """
    return f"/a/{storage_key(organization_id, asset_id)}"


def parse_storage_key(key: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """The inverse of `storage_key`: `{organization_id}/{asset_id}` back into two UUIDs.

    Used only by the orphan sweep (ADR-0009 §9 addendum), which lists the bucket rather than
    a row and has to recover both ids from the key alone to check whether any row claims it.
    Returns `None` rather than raising on a key this service did not write — an object a
    future service shares the bucket with, or an operator's `mc cp`, must be left alone
    rather than crash the sweep or be treated as this service's own orphan.
    """
    parts = key.split("/")
    if len(parts) != 2:
        return None
    try:
        return uuid.UUID(parts[0]), uuid.UUID(parts[1])
    except ValueError:
        return None


# ============================================================================================
# Private objects (ADR-0009 §2, Q1). No re-encoding — a payslip PDF is not decoded and
# rewritten the way an image is — but the same governing rule holds: **the bytes decide.** A
# presigned PUT/POST hands the client a URL that writes straight to the bucket, so nothing here
# ever inspects the bytes before they land there; `complete` is what reads them back and is the
# only place `content_type` becomes a fact rather than a claim.
# ============================================================================================

#: `{product}.{name}`, both lowercase snake segments — e.g. `payroll.payslip`,
#: `hrms.id_scan`. The first segment becomes `owner_service` (`PrivateObject.owner_service`):
#: a caller names *what kind of thing* this is, and the product it groups under falls out of
#: that name rather than being a second field a caller could get out of sync with the first.
PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

#: Verified by magic bytes, never by the declared `Content-Type` or a filename extension — the
#: same rule §5 applies to images, applied to a wider, still-closed set. A private object is
#: served with `Content-Disposition: attachment` (ADR-0009 Q1's Caddy route) rather than
#: rendered, so the polyglot risk §5 re-encodes away for public images is smaller here, but
#: "smaller" is not "absent": PDF readers execute embedded JavaScript, so this is a closed
#: allow-list, not "anything that is not an image".
PRIVATE_ACCEPTED_CONTENT_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp")


class PurposeRejected(Exception):
    """`purpose` is not `{product}.{name}`. A value, not a surprise — same shape as
    `ImageRejected`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_purpose(purpose: str) -> tuple[str, str]:
    """`"payroll.payslip"` -> `("payroll", "payslip")`. Raises `PurposeRejected` otherwise."""
    if not PURPOSE_PATTERN.match(purpose):
        raise PurposeRejected(
            'purpose must look like "product.name" (lowercase letters, digits and '
            'underscores only), e.g. "payroll.payslip".'
        )
    owner_service, _, _name = purpose.partition(".")
    return owner_service, purpose


def private_storage_key(organization_id: object, owner_service: str, object_id: object) -> str:
    """`{organization_id}/{owner_service}/{object_id}`. Never accepted from a caller — every
    route addresses a private object by row id; the row is fetched under RLS and the key is
    read off it, exactly as `storage_key` documents for the public bucket."""
    return f"{organization_id}/{owner_service}/{object_id}"


def sniff_private_content_type(raw: bytes) -> str | None:
    """The real type of `raw`, decided from its first bytes, or `None` if it matches nothing
    this service accepts for a private object. Never trusts a declared `Content-Type` or a
    filename — the entire reason `complete` re-reads the object instead of taking the
    presigned upload's own headers at face value.

    A deliberately small, closed set: the three image formats §5 already validates, plus PDF
    (payslips, contracts) and a handful of common office/archive containers whose signature is
    unambiguous. Nothing here is decoded past its header — this is a type check, not a
    validity check; a truncated or corrupt PDF still sniffs as `application/pdf` and is the
    uploader's problem, not this function's.
    """
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return None

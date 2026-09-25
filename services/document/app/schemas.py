"""Request and response contracts for document.

This module is the OpenAPI surface: a change here is a change to the published contract,
and the frontend generates its client from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


class AssetOut(BaseModel):
    """One asset in the merchant's library.

    Note what is absent: the **object key**. It is derivable from `public_path`, so hiding it
    would be theatre — but naming it `storage_key` in a response is how a later contributor
    gets the idea that a route might accept one, and no route in this service ever will
    (ADR-0009 §3).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: **This is the value to store**, in a section setting or anywhere else that outlives
    #: the session. Root-relative on purpose: the host is the thing that changes over the
    #: next three years — MinIO, then AWS, then a CDN — and abstracting it means changing one
    #: runtime config value instead of rewriting every merchant's store document
    #: (ADR-0009 §6). Resolve it with `resolveImageSrc`, never by hand.
    public_path: str = Field(examples=["/a/8f1c.../3b9d..."])
    #: The absolute address **for preview only**. Never persist it: it embeds the current
    #: host, which is exactly what `public_path` exists to keep out of merchant data. It is
    #: returned so the editor can render an uploaded image immediately, before the
    #: storefront's `ASSET_BASE` wiring is in place.
    public_url: str
    #: Derived from what the decoder said the bytes were, never from what the request claimed.
    content_type: str
    #: Of the stored object, after downscale and re-encode — so it is routinely smaller than
    #: the file the merchant chose, and for a lossless PNG it can be larger.
    size_bytes: int
    width: int
    height: int
    checksum_sha256: str
    original_filename: str | None
    uploaded_by_user_id: uuid.UUID | None
    created_at: datetime
    #: Removed from the library. **The object is untouched and the public URL keeps working**
    #: — a published store that references it keeps rendering (ADR-0009 §8).
    deleted_at: datetime | None
    #: The object has been deleted from the bucket and the public URL now 404s.
    purged_at: datetime | None


class AssetUsageOut(BaseModel):
    """What the organization is consuming against the hard ceiling (ADR-0009 §7).

    Returned alongside the library listing so the picker can show "1.2 GB of 2 GB used"
    without a second round trip, and so an over-quota refusal is never the first time a
    merchant hears about the limit.
    """

    bytes_used: int
    bytes_limit: int
    asset_count: int
    asset_count_limit: int


class AssetPage(BaseModel):
    """The library listing. Paginated, newest first, soft-deleted rows excluded."""

    items: list[AssetOut]
    total: int
    page: int
    size: int
    pages: int
    usage: AssetUsageOut


# ============================================================================================
# Private objects (ADR-0009 §2, Q1)
# ============================================================================================


class PrivateObjectInitiateIn(BaseModel):
    """What a caller declares before a byte is sent. Every field here is a **claim**:
    `complete` re-derives `content_type` and `size_bytes` from the object itself and refuses a
    mismatch rather than trusting this body for anything but sizing the presigned upload."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    #: `"{product}.{name}"`, e.g. `"payroll.payslip"`. The first segment becomes
    #: `owner_service` and the key's own second path segment.
    purpose: str = Field(min_length=3, max_length=128, examples=["payroll.payslip"])


class PrivateObjectInitiateOut(BaseModel):
    """The presigned upload. `upload_url` and `upload_fields` are submitted **as a
    `multipart/form-data` POST**, `upload_fields` first and the file itself last — the shape
    `generate_presigned_post` requires. Nothing here is persisted by the caller past the upload:
    the object id is what `complete`, `GET` and `DELETE` address afterwards."""

    id: uuid.UUID
    upload_url: str
    upload_fields: dict[str, str]
    upload_expires_at: datetime


class PrivateObjectOut(BaseModel):
    """One private object's metadata. Never the bytes, and — outside `download_url` below —
    never anything that resolves to them without going through this service again."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    purpose: str
    owner_service: str
    content_type: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    original_filename: str | None
    uploaded_by_user_id: uuid.UUID | None
    created_at: datetime
    completed_at: datetime | None
    attached_at: datetime | None


class PrivateObjectDownloadOut(PrivateObjectOut):
    """`GET /private-objects/{id}` — the metadata plus a fresh, short-lived signed GET. Never
    cached and never reused past its own `download_url_expires_at`: a second `GET` issues a
    second URL, because authorization is re-checked on every call, not once per object."""

    download_url: str
    download_url_expires_at: datetime

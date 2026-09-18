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

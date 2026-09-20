"""Writes for document.

Business rules, audit rows and outbox rows. Nothing here opens or commits a transaction: a
route with a principal takes its session from `get_tenant_session`, which opens the request's
one transaction and binds it to the caller's organization first (ADR-0002 §1), so the state
change and its audit entry commit or roll back together.

**Write order is the one decision in this module that is not local** (ADR-0009 §9). An upload
is two writes that cannot be one transaction — an object into S3, a row into Postgres — and
the order chooses which failure to have:

* object first, and a failed object write rolls the transaction back: no row, retryable 503;
* row-insert failure after a successful object write leaves an **orphan object** —
  unreferenced, world-readable at an unguessable key, invisible, and sweepable.

The orphan is strictly the less harmful failure. A row without an object is an entry in the
merchant's library whose image is permanently broken, visible, confusing, and self-healing
never. Orphan objects are a normal expected state here, not an anomaly; nothing alerts on one.

**Deletion is two verbs behind one endpoint** (ADR-0009 §8), and the reason is structural: this
service cannot know what references an asset and never will. The store document lives in a
storefront service that does not exist yet, and when it does it will own a different schema
that ADR-0000 §10 forbids reading across. A reference check is impossible by construction, so
"delete" means "take it out of my library" and leaves the object alone, and `purge=True` is the
explicit, audited way to make a world-readable object stop being one.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import Settings
from app.models import Asset, AuditLog
from app.storage import ObjectStore
from platform_core.audit import record_audit
from platform_core.auth import ScopeContext
from platform_core.errors import BusinessRuleProblem
from platform_core.logging import get_logger
from platform_core.models import utcnow

log = get_logger("document.services")


async def create_asset(
    session: AsyncSession,
    store: ObjectStore,
    ctx: ScopeContext,
    *,
    image: domain.ProcessedImage,
    original_filename: str | None,
    cfg: Settings,
    request: Request | None = None,
) -> Asset:
    """Store one processed image and record it. Object first, row second.

    `image` arrives already decoded, downscaled, re-encoded and checksummed — the route does
    that off the event loop before calling here, so nothing in this function touches the
    uploader's bytes as anything but an opaque blob.
    """
    consumed = await selectors.usage(session, ctx)
    _refuse_over_quota(consumed, incoming_bytes=image.size_bytes, cfg=cfg)

    asset_id = uuid.uuid4()
    key = domain.storage_key(ctx.organization_id, asset_id)

    # Object first (ADR-0009 §9). A failure here raises UpstreamUnavailableProblem, the
    # request's transaction rolls back, and nothing at all was written.
    await store.put(
        key,
        image.data,
        content_type=image.content_type,
        organization_id=ctx.organization_id,
    )

    asset = Asset(
        id=asset_id,
        organization_id=ctx.organization_id,
        storage_key=key,
        content_type=image.content_type,
        size_bytes=image.size_bytes,
        width=image.width,
        height=image.height,
        checksum_sha256=image.checksum_sha256,
        original_filename=original_filename,
        uploaded_by_user_id=ctx.principal.sub,
    )
    session.add(asset)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="asset.uploaded",
        entity="asset",
        entity_id=asset_id,
        after={
            "storage_key": key,
            "content_type": image.content_type,
            "size_bytes": image.size_bytes,
            "width": image.width,
            "height": image.height,
            "original_filename": original_filename,
        },
        request=request,
    )
    log.info(
        "asset.uploaded",
        organization_id=str(ctx.organization_id),
        asset_id=str(asset_id),
        content_type=image.content_type,
        size_bytes=image.size_bytes,
    )
    return asset


def _refuse_over_quota(consumed: selectors.Usage, *, incoming_bytes: int, cfg: Settings) -> None:
    """The hard per-organization ceiling, from configuration (ADR-0009 §7).

    422 and not 402: per ADR-0004 §5, 402 means the *subscription* does not entitle the
    product, and it does — this is a domain rule inside an entitled product. When storage is
    priced this becomes a `catalog` Feature read through the existing entitlement path, and
    *then* exceeding it can legitimately be a 402 with an upgrade offer.

    The message names the limit and the current usage, because "over quota" with no numbers
    is a support ticket.
    """
    if consumed.asset_count + 1 > cfg.asset_quota_count:
        raise BusinessRuleProblem(
            f"This organization has reached its limit of {cfg.asset_quota_count} images. "
            f"Remove some from the library to free space."
        )
    if consumed.bytes_used + incoming_bytes > cfg.asset_quota_bytes:
        raise BusinessRuleProblem(
            f"This organization has used {consumed.bytes_used} bytes of its "
            f"{cfg.asset_quota_bytes}-byte image storage limit, and this image needs "
            f"{incoming_bytes} more. Remove some images to free space."
        )


async def remove_asset(
    session: AsyncSession,
    store: ObjectStore,
    ctx: ScopeContext,
    asset: Asset,
    *,
    purge: bool,
    request: Request | None = None,
) -> Asset:
    """Soft-delete an asset, and on `purge` take the object down immediately.

    Idempotent in both directions, because a client can retry a DELETE and a merchant can
    double-click: a row that is already in the requested state is returned unchanged, with no
    second audit row — an audit trail that records every retry as an act is a trail nobody
    reads.

    On the purge path the object is deleted **before** the row is updated, mirroring the
    upload's ordering for the same reason. If the row update then fails, `purged_at` stays
    null and the retention sweep retries the delete; S3 returns 204 for a key that is already
    gone, so the retry is free. The opposite order would leave a row claiming the object is
    gone while it is still world-readable, which is the one lie this table must never tell.
    """
    was_deleted = asset.deleted_at
    was_purged = asset.purged_at
    if was_deleted is not None and (was_purged is not None or not purge):
        return asset

    if purge:
        await store.delete(asset.storage_key, organization_id=ctx.organization_id)

    now = utcnow()
    if asset.deleted_at is None:
        asset.deleted_at = now
    if purge:
        asset.purged_at = now

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="asset.removed",
        entity="asset",
        entity_id=asset.id,
        before={"deleted_at": was_deleted, "purged_at": was_purged},
        after={
            "deleted_at": asset.deleted_at,
            "purged_at": asset.purged_at,
            # An immediate takedown and a soft removal are different acts, and the trail has
            # to distinguish them (ADR-0009 §14).
            "purge": purge,
        },
        request=request,
    )
    log.info(
        "asset.removed",
        organization_id=str(ctx.organization_id),
        asset_id=str(asset.id),
        purge=purge,
    )
    return asset

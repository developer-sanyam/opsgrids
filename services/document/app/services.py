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
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select
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


async def db_now(session: AsyncSession) -> datetime:
    """One clock, and it is the database's — the same amendment ADR-0006 §8 makes for the
    approval sweep, applied here for the retention window's own re-check: `deleted_at` and
    `purged_at` are `server_default=func.now()`/application-set timestamps written by
    Postgres, so comparing them against a value the sweep process's own host clock produced
    is a heisenbug waiting on ordinary clock skew, not a correctness property."""
    return (await session.execute(select(func.now()))).scalar_one()


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """What one sweep action did to one asset. `purged=False` covers every skip — already
    purged, undeleted, not yet past the window, or the row is held by someone else — and the
    sweep's own report counts them together as a skip; nothing here distinguishes which,
    because none of them is an error."""

    purged: bool


async def purge_asset(
    session: AsyncSession,
    store: ObjectStore,
    *,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
    retention: timedelta,
) -> PurgeResult:
    """One purge, in the sweep's own tenant-bound transaction (`app/cron.py`'s
    `PurgeSweep`, ADR-0009 §8). Mirrors `remove_asset`'s purge branch exactly — object
    first, row second — because it is the identical write-order decision, triggered by a
    clock instead of a request.

    1. Lock the row `FOR UPDATE SKIP LOCKED` (`selectors.get_asset_for_purge_sweep`) — held
       means another replica's sweep pass, or an ordinary `?purge=true` request
       (`remove_asset`), is already acting on this asset; skip rather than queue behind it.
       The query itself already filters `deleted_at IS NOT NULL AND purged_at IS NULL`, so a
       `None` here also covers "already purged" and "undeleted" without a second check.
    2. Re-check the retention window under the lock, against this transaction's **own**,
       deliberately later `now()` (`db_now`) — never the `cutoff` discovery computed, which
       is possibly stale by however long discovery's batch took to drain. An asset undeleted
       in that gap already failed step 1's filter; an asset whose `deleted_at` has not
       actually crossed the window yet (a clock skew between the discovery and action
       connections, vanishingly unlikely on one database but not provably impossible) is
       caught here instead of purged early.
    3. Delete the object **first**. `store.delete` treats a missing key as success (S3
       `DeleteObject` on a missing key returns 204), so a retry of a purge that got this far
       last tick and then failed at step 4 costs nothing. If this raises, it propagates: the
       caller's `session_scope` transaction rolls back, `purged_at` stays null, and the next
       tick retries — a row claiming the object is gone while it may still be world-readable
       is the one lie this table must never tell (`remove_asset`'s own docstring).
    4. Set `purged_at` to this transaction's `now()` and write the audit row
       (`acting_as="document.purge_sweep"`). No outbox row: ADR-0009 §7 already refuses to
       publish an asset event with no consumer, and a sweep-triggered purge is not a
       different act from a `?purge=true` one in that respect — `remove_asset` stages
       nothing either.
    """
    asset = await selectors.get_asset_for_purge_sweep(session, organization_id, asset_id)
    if asset is None:
        return PurgeResult(purged=False)

    now = await db_now(session)
    if asset.deleted_at is None or asset.deleted_at > now - retention:
        return PurgeResult(purged=False)

    await store.delete(asset.storage_key, organization_id=organization_id)

    asset.purged_at = now
    record_audit(
        session,
        AuditLog,
        organization_id=organization_id,
        actor_id=None,
        acting_as="document.purge_sweep",
        action="asset.purged",
        entity="asset",
        entity_id=asset.id,
        before={"purged_at": None},
        after={"purged_at": asset.purged_at, "deleted_at": asset.deleted_at},
        reason=f"retention window of {retention.days} days elapsed",
    )
    log.info(
        "asset.purged",
        organization_id=str(organization_id),
        asset_id=str(asset.id),
        deleted_at=asset.deleted_at.isoformat(),
    )
    return PurgeResult(purged=True)

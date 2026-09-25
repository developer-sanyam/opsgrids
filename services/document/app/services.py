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

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import Settings
from app.models import Asset, AuditLog, PrivateObject
from app.storage import ObjectStore, PrivateObjectStore
from platform_core.audit import record_audit, record_sensitive_read
from platform_core.auth import ScopeContext
from platform_core.errors import (
    BusinessRuleProblem,
    ConflictProblem,
    PayloadTooLargeProblem,
    ProblemError,
    ValidationProblem,
)
from platform_core.logging import get_logger
from platform_core.models import utcnow

log = get_logger("document.services")


class UnsupportedMediaTypeProblem(ProblemError):
    """415. The bytes `complete` read back are not one of `domain.PRIVATE_ACCEPTED_CONTENT_TYPES`,
    or do not match what was declared at `initiate` — either way the client's remedy is "send a
    different file", which is what a distinct type from 422 signals."""

    status_code = 415
    problem_type = "unsupported-media-type"
    title = "Unsupported file type"


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


# ================================================================================================
# Private objects (ADR-0009 §2, Q1)
# ================================================================================================


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """What `create_upload_post` handed back, typed rather than a loose dict past this point."""

    url: str
    fields: dict[str, str]


async def initiate_private_object(
    session: AsyncSession,
    private_store: PrivateObjectStore,
    ctx: ScopeContext,
    *,
    filename: str,
    content_type: str,
    size_bytes: int,
    purpose: str,
    cfg: Settings,
    request: Request | None = None,
) -> tuple[PrivateObject, PresignedUpload]:
    """Row first, presign second — the opposite order from the public upload, and for the
    matching reason: nothing has been written to the bucket yet, so there is no orphan *object*
    to worry about. What this creates is a `pending` row that may never be completed, which
    `complete`, `get_private_object` and `remove_private_object` all treat as "does not exist
    yet" rather than as a real object with a lifecycle of its own (see the model's docstring).

    `size_bytes` and `content_type` here are the client's **declared** values, used only to
    size the presigned POST's own `content-length-range` condition and to pick the form field
    S3 will check on the way in — never trusted as the stored fact. `complete` re-derives both.
    """
    if size_bytes > cfg.private_object_max_upload_bytes:
        raise PayloadTooLargeProblem(
            f"That file is larger than the "
            f"{cfg.private_object_max_upload_bytes // (1024 * 1024)} MiB limit for this kind "
            "of upload."
        )

    try:
        owner_service, normalised_purpose = domain.parse_purpose(purpose)
    except domain.PurposeRejected as exc:
        raise ValidationProblem(
            exc.message,
            errors=[{"field": "purpose", "code": "invalid_purpose", "message": exc.message}],
        ) from exc

    object_id = uuid.uuid4()
    key = domain.private_storage_key(ctx.organization_id, owner_service, object_id)
    expires_at = utcnow() + timedelta(seconds=cfg.private_object_upload_ttl_seconds)

    presigned = private_store.create_upload_post(
        key,
        content_type=content_type,
        max_bytes=cfg.private_object_max_upload_bytes,
        expires_in=cfg.private_object_upload_ttl_seconds,
    )

    row = PrivateObject(
        id=object_id,
        organization_id=ctx.organization_id,
        storage_key=key,
        purpose=normalised_purpose,
        owner_service=owner_service,
        status="pending",
        declared_content_type=content_type,
        declared_size_bytes=size_bytes,
        original_filename=filename,
        uploaded_by_user_id=ctx.principal.sub,
        owner_subject_ref=ctx.subject_ref,
        upload_expires_at=expires_at,
    )
    session.add(row)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="private_object.upload_initiated",
        entity="private_object",
        entity_id=object_id,
        after={
            "storage_key": key,
            "purpose": normalised_purpose,
            "declared_content_type": content_type,
            "declared_size_bytes": size_bytes,
            "original_filename": filename,
        },
        request=request,
    )
    log.info(
        "private_object.upload_initiated",
        organization_id=str(ctx.organization_id),
        object_id=str(object_id),
        owner_service=owner_service,
    )
    return row, PresignedUpload(url=str(presigned["url"]), fields=dict(presigned["fields"]))


async def complete_private_object(
    session: AsyncSession,
    private_store: PrivateObjectStore,
    ctx: ScopeContext,
    row: PrivateObject,
    *,
    cfg: Settings,
    request: Request | None = None,
) -> PrivateObject:
    """Verify the object that actually landed in the bucket and only then mark the row `stored`.

    Nothing the client said at `initiate` is trusted here — not the declared size, not the
    declared type, not even that an object exists yet. Three checks, in the order that fails
    cheapest first:

    1. **Existence and size**, from `HEAD` — cheap, and catches "the browser never finished
       the POST" without reading a byte.
    2. **The TTL**, against this row's own `upload_expires_at` — a client cannot resurrect an
       expired presign by finally finishing the upload after this route already refused it once
       and the caller retried.
    3. **The real type**, from the bytes themselves (`GET`, then
       `domain.sniff_private_content_type`) — a presigned POST's `Content-Type` condition
       (`app/storage.py`) constrains what the bucket *accepted*, not what the bytes actually
       decode as; nothing stops a client sending
       an HTML payload declared as `application/pdf` and having S3 accept it on the header
       alone. This is the same "the bytes decide" rule §5 applies to images, and it is the one
       check here that is not simply re-confirming what the presign already enforced.

    A failure past step 1 deletes the object from the bucket before raising: an unverified
    object under a key this row already claims must not be left behind for a later `GET` to
    treat as trustworthy just because a row exists. Best-effort — logged, not re-raised, so a
    transient storage failure on the cleanup path does not mask the validation error the caller
    actually needs to see.
    """
    if row.status == "stored":
        return row  # idempotent: a retried `complete` of an already-completed row is a no-op

    now = utcnow()
    if now > row.upload_expires_at:
        raise ValidationProblem(
            "This upload's window has expired. Start a new upload with POST .../initiate."
        )

    head = await private_store.head(row.storage_key)
    if head is None:
        raise ValidationProblem(
            "No file has reached storage yet for this upload. Finish the presigned POST first."
        )
    actual_size, _etag = head
    if actual_size > cfg.private_object_max_upload_bytes:
        await _best_effort_delete(private_store, row.storage_key)
        raise PayloadTooLargeProblem(
            f"That file is larger than the "
            f"{cfg.private_object_max_upload_bytes // (1024 * 1024)} MiB limit for this kind "
            "of upload."
        )

    body = await private_store.get(row.storage_key, max_bytes=cfg.private_object_max_upload_bytes)
    real_type = domain.sniff_private_content_type(body)
    if real_type is None:
        await _best_effort_delete(private_store, row.storage_key)
        raise UnsupportedMediaTypeProblem(
            "That file is not one of the accepted types "
            f"({', '.join(domain.PRIVATE_ACCEPTED_CONTENT_TYPES)})."
        )

    checksum = hashlib.sha256(body).hexdigest()

    row.status = "stored"
    row.content_type = real_type
    row.size_bytes = actual_size
    row.checksum_sha256 = checksum
    row.completed_at = now

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="private_object.uploaded",
        entity="private_object",
        entity_id=row.id,
        after={
            "storage_key": row.storage_key,
            "content_type": real_type,
            "size_bytes": actual_size,
            "checksum_sha256": checksum,
        },
        request=request,
    )
    log.info(
        "private_object.uploaded",
        organization_id=str(ctx.organization_id),
        object_id=str(row.id),
        content_type=real_type,
        size_bytes=actual_size,
    )
    return row


async def _best_effort_delete(private_store: PrivateObjectStore, key: str) -> None:
    """Clean up a rejected upload's object. Failures are logged, never raised: the validation
    problem the caller triggered this for is what must reach them, not a storage hiccup on the
    cleanup path. An object left behind here is an orphan in a bucket nothing lists publicly and
    nothing here yet sweeps — named as follow-up work, not a live risk to a caller's data."""
    try:
        await private_store.delete(key)
    except ProblemError as exc:
        log.error("private_object.cleanup_failed", key=key, error=str(exc)[:200])


async def build_download(
    session: AsyncSession,
    private_store: PrivateObjectStore,
    ctx: ScopeContext,
    row: PrivateObject,
    *,
    cfg: Settings,
    request: Request | None = None,
) -> tuple[str, datetime]:
    """A fresh presigned GET for an already-`stored` object, and the sensitive-read audit row
    that issuing it requires (CLAUDE.md: unmasking PII needs its own permission and an audit row
    every time — a private object is PII by construction, so every download URL issued is one of
    these reads, not only the ones a human happens to open)."""
    expires_at = utcnow() + timedelta(seconds=cfg.private_object_download_ttl_seconds)
    url = private_store.presign_download(
        row.storage_key,
        filename=row.original_filename,
        expires_in=cfg.private_object_download_ttl_seconds,
    )
    record_sensitive_read(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        entity="private_object",
        entity_id=row.id,
        fields=["download_url"],
        request=request,
    )
    log.info(
        "private_object.download_issued",
        organization_id=str(ctx.organization_id),
        object_id=str(row.id),
    )
    return url, expires_at


async def remove_private_object(
    session: AsyncSession,
    private_store: PrivateObjectStore,
    ctx: ScopeContext,
    row: PrivateObject,
    *,
    request: Request | None = None,
) -> None:
    """Hard delete: the object from the bucket, then the row. Unlike an `Asset`, a private
    object has no grace window and no forensic reason to survive — nothing outside this service
    can reference it yet (§8's whole argument for a soft delete does not apply until an attach
    step exists), and once `attached_at` is set a future attach step will refuse this path
    entirely rather than needing one here to grow a purge sweep of its own.

    Object first, matching every other destructive write in this service: if the object delete
    fails, the row survives for a retry rather than a row claiming a still-live object is gone.
    """
    if row.attached_at is not None:
        raise ConflictProblem(
            "This file has already been attached and can no longer be deleted here."
        )

    await private_store.delete(row.storage_key)

    record_audit(
        session,
        AuditLog,
        organization_id=ctx.organization_id,
        actor_id=ctx.principal.sub,
        action="private_object.deleted",
        entity="private_object",
        entity_id=row.id,
        before={"status": row.status, "storage_key": row.storage_key},
        request=request,
    )
    log.info(
        "private_object.deleted",
        organization_id=str(ctx.organization_id),
        object_id=str(row.id),
    )
    await session.delete(row)

"""HTTP surface for document — the platform's **public asset store** (ADR-0009).

Fronted by `app-gateway` at `/api/v1/documents`, with **no `product` key** in the route table:
document is a platform surface every customer has, the same shape as `/api/v1/members`, and it
must work for a customer who has bought nothing yet. No gateway change was needed for this
feature, which is a large part of why `document` is the right owner.

────────────────────────────────────────────────────────────────────────────────────────────
**The one rule a later contributor is most likely to break for convenience.**

*The object key is never accepted from a caller.* Every route here addresses an asset by its
**row id**; the row is fetched under row-level security in the request's tenant-bound
transaction; the key is read off the row. A route that took `?key=` would be a cross-tenant
delete primitive that RLS cannot see, because RLS does not know what a bucket is. There is no
convenience worth that, and no admin path that needs it.
────────────────────────────────────────────────────────────────────────────────────────────

Two further things this service does **not** do, stated because its name invites the opposite
assumption. It never serves image bytes — the bucket does, directly to the browser, with no
credential and no expiry — and it offers no way to store a private object, so the failure mode
for a confidential file is "the feature is missing", not "the payslip went somewhere
surprising" (ADR-0009 §2).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

# Starlette's `UploadFile`, not FastAPI's. FastAPI subclasses it, so an isinstance check
# against the subclass is False for what the parser actually produces — which reads as
# "there was no file part" on every valid upload.
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app import domain, selectors, services
from app.config import Settings, settings
from app.models import SCHEMA, Asset, PrivateObject
from app.schemas import (
    AssetOut,
    AssetPage,
    AssetUsageOut,
    PrivateObjectDownloadOut,
    PrivateObjectInitiateIn,
    PrivateObjectInitiateOut,
    PrivateObjectOut,
    ServiceInfo,
)
from app.storage import ObjectStore, PrivateObjectStore, get_object_store, get_private_object_store
from platform_core.auth import Scope, ScopeContext, require
from platform_core.cache import enforce_rate_limit
from platform_core.db import get_tenant_session
from platform_core.errors import (
    NotFoundProblem,
    ProblemError,
    ValidationProblem,
)
from platform_core.logging import get_logger
from platform_core.pagination import PageQuery

log = get_logger("document.routes")

router = APIRouter(tags=["document"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
StoreDep = Annotated[ObjectStore, Depends(get_object_store)]
PrivateStoreDep = Annotated[PrivateObjectStore, Depends(get_private_object_store)]

#: Both permissions are ceilinged **and** defaulted at ORG in `app/catalogue.py`, so OWN, TEAM
#: and LOCATION are unrepresentable rather than merely ungranted. The `minimum=Scope.ORG` floor
#: here is therefore redundant today, and is written anyway: the ceiling is catalogue data that
#: a later edit can widen, and the floor is code in front of the query (ADR-0009 §4).
ReadCtx = Annotated[ScopeContext, Depends(require("core.content.asset.read", minimum=Scope.ORG))]
WriteCtx = Annotated[ScopeContext, Depends(require("core.content.asset.write", minimum=Scope.ORG))]

#: **Floored at OWN, not ORG** — the ceiling is ORG (`app/catalogue.py`), but the floor here is
#: the narrowest rung that means anything for a private document: an uploader acting on their
#: own object. `ScopeContext.apply`/`assert_can_touch` are what actually narrow an ORG grant
#: down to nothing extra and an OWN grant down to "mine" — this floor only keeps out a
#: permission with no scope at all.
PrivateReadCtx = Annotated[
    ScopeContext, Depends(require("core.content.private_document.read", minimum=Scope.OWN))
]
PrivateWriteCtx = Annotated[
    ScopeContext, Depends(require("core.content.private_document.write", minimum=Scope.OWN))
]

#: The multipart envelope — boundaries, part headers, the filename — costs a little more than
#: the file itself, so the stream counter is allowed this much slack over the file limit before
#: it aborts. Generous by two orders of magnitude relative to a real envelope, and still three
#: orders below anything that would trouble the process.
MULTIPART_ENVELOPE_ALLOWANCE = 8 * 1024

#: Exactly one file part, and one field's worth of slack for a client that sends a stray
#: `filename` alongside it — which is then refused by name below, rather than ignored.
MAX_FILE_PARTS = 2
MAX_FIELD_PARTS = 4


class PayloadTooLargeProblem(ProblemError):
    """413. Its own type because the client's remedy is specific: send a smaller file."""

    status_code = 413
    problem_type = "payload-too-large"
    title = "File is too large"


async def _rate_limit_uploads(ctx: WriteCtx) -> None:
    """Per organization, per minute. Declared as a dependency rather than called inline so a
    test can override it, and so it runs **after** authorization — an unauthorized caller
    should not be able to consume another organization's budget by naming it.

    The upload route is the only route in this service that does CPU work, and ADR-0009 §11
    names the re-encode as the first thing to saturate (roughly four concurrent uploads per
    API process). This is the cheap bound in front of that.
    """
    cfg = settings()
    await enforce_rate_limit(
        f"document.asset.upload:{ctx.organization_id}",
        limit=cfg.asset_upload_rate_limit_per_minute,
        window_seconds=60,
    )


async def _rate_limit_private_uploads(ctx: PrivateWriteCtx) -> None:
    """Same shape as `_rate_limit_uploads`: per organization, per minute, after authorization —
    this route hands out a credential-bearing presigned URL, which is exactly the kind of thing
    worth bounding even though it does no CPU work of its own."""
    cfg = settings()
    await enforce_rate_limit(
        f"document.private_object.upload:{ctx.organization_id}",
        limit=cfg.private_object_upload_rate_limit_per_minute,
        window_seconds=60,
    )


def _refuse_declared_length(request: Request, cfg: Settings) -> None:
    """The first of the two size checks (ADR-0009 §5): `Content-Length`, before reading a byte.

    Client-supplied, so it is a courtesy rather than a control — an honest client gets a fast,
    cheap refusal instead of uploading five megabytes to be told no. The control is
    `_counted_stream` below, which does not trust anything the client said.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError as exc:
        raise ValidationProblem("Content-Length is not a number") from exc
    if declared > cfg.asset_max_upload_bytes + MULTIPART_ENVELOPE_ALLOWANCE:
        raise PayloadTooLargeProblem(
            f"That file is larger than the {cfg.asset_max_upload_bytes // (1024 * 1024)} MiB limit."
        )


async def _counted_stream(request: Request, limit: int) -> AsyncGenerator[bytes, None]:
    """The second size check, and the real one: a hard byte counter over the live stream.

    `Content-Length` is client-supplied and a chunked upload need not send one at all, so the
    only trustworthy limit is the one that counts what actually arrives — and aborts mid-body
    rather than after the whole thing has been spooled to disk.
    """
    seen = 0
    async for chunk in request.stream():
        seen += len(chunk)
        if seen > limit:
            raise PayloadTooLargeProblem(
                f"That file is larger than the {limit // (1024 * 1024)} MiB limit."
            )
        yield chunk


async def _parse_single_file(request: Request, cfg: Settings) -> tuple[bytes, str | None]:
    """Parse the multipart body into exactly one file part, and reject anything else.

    The body is parsed here rather than declared as an `UploadFile` parameter for one reason
    that matters: FastAPI reads the request body *before* it solves dependencies, so a
    size check expressed as a dependency would run after five megabytes had already been
    read. Doing it by hand puts authorization, the rate limit and both size checks in front
    of the parse, which is the order they are worth anything in.

    Unknown parts are refused rather than ignored — a client sending `visibility=private`
    must not get a silent public upload.
    """
    _refuse_declared_length(request, cfg)
    limit = cfg.asset_max_upload_bytes + MULTIPART_ENVELOPE_ALLOWANCE

    parser = MultiPartParser(
        request.headers,
        _counted_stream(request, limit),
        max_files=MAX_FILE_PARTS,
        max_fields=MAX_FIELD_PARTS,
    )
    try:
        form: FormData = await parser.parse()
    except MultiPartException as exc:
        raise ValidationProblem(
            "The upload must be a multipart/form-data body with one `file` part."
        ) from exc

    try:
        unexpected = sorted(set(form.keys()) - {"file"})
        if unexpected:
            raise ValidationProblem(
                f"Unexpected form field(s): {', '.join(unexpected)}. "
                "Send exactly one `file` part and nothing else."
            )
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise ValidationProblem("Send exactly one `file` part containing the image.")
        data = await upload.read()
        filename = upload.filename
    finally:
        await form.close()

    if len(data) > cfg.asset_max_upload_bytes:
        raise PayloadTooLargeProblem(
            f"That file is larger than the {cfg.asset_max_upload_bytes // (1024 * 1024)} MiB limit."
        )
    if not data:
        raise ValidationProblem("The `file` part is empty.")
    return data, filename


def _to_out(asset: Asset, store: ObjectStore) -> AssetOut:
    return AssetOut(
        id=asset.id,
        public_path=domain.public_path(asset.organization_id, asset.id),
        public_url=store.public_url(asset.storage_key),
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        checksum_sha256=asset.checksum_sha256,
        original_filename=asset.original_filename,
        uploaded_by_user_id=asset.uploaded_by_user_id,
        created_at=asset.created_at,
        deleted_at=asset.deleted_at,
        purged_at=asset.purged_at,
    )


@router.get("/document/info", response_model=ServiceInfo, summary="What this service is")
async def info() -> ServiceInfo:
    return ServiceInfo(service=settings().service_name, schema_name=SCHEMA)


#: The published contract for the upload body. Written out because the body is parsed by hand
#: (see `_parse_single_file`) rather than declared as a parameter, and this module is the
#: OpenAPI surface the frontend generates its client from.
_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "additionalProperties": False,
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": (
                                "One JPEG, PNG or WebP image, at most 5 MiB. The declared "
                                "content type and the filename extension are both ignored: "
                                "the bytes are decoded to decide what this is, and re-encoded "
                                "before storage."
                            ),
                        }
                    },
                },
            }
        },
    }
}


@router.post(
    "/documents/assets",
    response_model=AssetOut,
    status_code=201,
    summary="Upload an image to the organization's library",
    dependencies=[Depends(_rate_limit_uploads)],
    openapi_extra=_UPLOAD_BODY,
)
async def upload_asset(
    request: Request,
    session: SessionDep,
    store: StoreDep,
    ctx: WriteCtx,
) -> AssetOut:
    """`core.content.asset.write`, floored at ORG.

    **The uploaded image is world-readable by anyone who has its address, forever, until it is
    deleted.** That is a decision, not a leak (ADR-0009 §1) — a storefront is a shopfront — but
    it is one the merchant has to be told about *on the control*, not in a help page, because
    a supplier price list or a customer's ID scan dropped into an image slot has been
    published. `DELETE …?purge=true` is the only remedy and it is after the fact.

    The response's `public_path` is what belongs in a store document. `public_url` is for
    preview and must not be persisted.
    """
    cfg = settings()
    data, filename = await _parse_single_file(request, cfg)

    # Pillow is synchronous C and holds the GIL for most of a 150-300 ms round trip on a
    # 5 MiB JPEG. Off the event loop, because this is the only CPU work on any request path in
    # this service and it must not be the reason `GET /assets` is slow (ADR-0009 §11).
    try:
        image = await anyio.to_thread.run_sync(domain.process_upload, data)
    except domain.ImageRejected as exc:
        # A value, not a surprise: the message names what is accepted and echoes nothing the
        # uploader supplied.
        raise ValidationProblem(
            exc.message, errors=[{"field": "file", "code": exc.code, "message": exc.message}]
        ) from exc

    asset = await services.create_asset(
        session,
        store,
        ctx,
        image=image,
        original_filename=filename,
        cfg=cfg,
        request=request,
    )
    # Flushed so the server defaults (`created_at`) are populated before the response is
    # built. `get_tenant_session` still owns the commit.
    await session.flush()
    return _to_out(asset, store)


@router.get(
    "/documents/assets",
    response_model=AssetPage,
    summary="The organization's image library",
)
async def list_assets(
    session: SessionDep,
    store: StoreDep,
    ctx: ReadCtx,
    params: PageQuery,
) -> AssetPage:
    """`core.content.asset.read`, floored at ORG. Newest first, soft-deleted rows excluded.

    Paginated, and the page size is capped by the server rather than by the client
    (`page_params`, max 200) — there is no way to ask this endpoint for the whole library.

    Carries the organization's storage usage alongside the page, so the picker can show how
    much room is left and an over-quota refusal is never the first time a merchant hears about
    the limit.
    """
    cfg = settings()
    rows, total = await selectors.list_assets(session, ctx, params)
    consumed = await selectors.usage(session, ctx)
    return AssetPage(
        items=[_to_out(row, store) for row in rows],
        total=total,
        page=params.page,
        size=params.size,
        pages=max(1, -(-total // params.size)) if total else 0,
        usage=AssetUsageOut(
            bytes_used=consumed.bytes_used,
            bytes_limit=cfg.asset_quota_bytes,
            asset_count=consumed.asset_count,
            asset_count_limit=cfg.asset_quota_count,
        ),
    )


@router.get(
    "/documents/assets/{asset_id}",
    response_model=AssetOut,
    summary="One image's details",
)
async def get_asset(
    asset_id: uuid.UUID,
    session: SessionDep,
    store: StoreDep,
    ctx: ReadCtx,
) -> AssetOut:
    """`core.content.asset.read`, floored at ORG. Metadata, **not bytes** — this service never
    serves image bytes; the bucket does.

    An asset in another organization is a 404 and never a 403: confirming that an id exists in
    *some* organization is itself a disclosure.
    """
    asset = await selectors.get_asset(session, ctx, asset_id)
    if asset is None:
        raise NotFoundProblem("No such image")
    return _to_out(asset, store)


@router.delete(
    "/documents/assets/{asset_id}",
    response_model=AssetOut,
    summary="Remove an image from the library, or take it down immediately",
)
async def delete_asset(
    asset_id: uuid.UUID,
    session: SessionDep,
    store: StoreDep,
    ctx: WriteCtx,
    request: Request,
    purge: Annotated[
        bool,
        Query(
            description=(
                "Delete the stored object immediately. The public URL starts returning 404 "
                "and any published store referencing this image shows a broken image."
            )
        ),
    ] = False,
) -> AssetOut:
    """`core.content.asset.write`, floored at ORG. Two verbs behind one endpoint (ADR-0009 §8).

    **Default — remove from the library.** Sets `deleted_at`. The image disappears from the
    picker and stops counting against the quota, **the object is not touched, and the public
    URL keeps working**, so a published store that references it keeps rendering. "Delete" in a
    merchant's head means "take it out of my library", not "break my live store", and those two
    readings differ by an outage nobody asked for. A sweep purges the object after the
    retention window; until then, undelete is free.

    **`?purge=true` — take it down now.** The object is deleted from the bucket immediately.
    This exists for the one case the soft path cannot serve: a merchant who has just put a
    customer's ID scan or an internal price list into a world-readable bucket. "Wait 30 days"
    is not an answer to that, and neither is an operator ticket. Same permission, explicit
    flag, audit row carrying the flag.

    **This service cannot tell you whether the image is in use**, and will not pretend to: the
    store document lives in a service that does not exist yet and will own a schema this one
    is forbidden to read. The editor's confirmation has to say so.

    Idempotent: a repeat of the same request returns the same row and writes no second audit.
    """
    asset = await selectors.get_asset_for_removal(session, ctx, asset_id)
    if asset is None:
        raise NotFoundProblem("No such image")
    asset = await services.remove_asset(session, store, ctx, asset, purge=purge, request=request)
    await session.flush()
    return _to_out(asset, store)


# ================================================================================================
# Private objects (ADR-0009 §2, Q1) — a payslip, a signed contract, an ID scan.
#
# Same chokepoint rule as the public assets above: **the object key is never accepted from a
# caller.** Every route addresses a private object by its row id, fetched under RLS with the
# caller's scope applied (`selectors.get_private_object`); the key is read off the row.
# ================================================================================================


def _private_to_out(row: PrivateObject) -> PrivateObjectOut:
    return PrivateObjectOut(
        id=row.id,
        status=row.status,
        purpose=row.purpose,
        owner_service=row.owner_service,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        checksum_sha256=row.checksum_sha256,
        original_filename=row.original_filename,
        uploaded_by_user_id=row.uploaded_by_user_id,
        created_at=row.created_at,
        completed_at=row.completed_at,
        attached_at=row.attached_at,
    )


@router.post(
    "/documents/private-objects/initiate",
    response_model=PrivateObjectInitiateOut,
    status_code=201,
    summary="Start a private document upload",
    dependencies=[Depends(_rate_limit_private_uploads)],
)
async def initiate_private_object(
    body: PrivateObjectInitiateIn,
    session: SessionDep,
    private_store: PrivateStoreDep,
    ctx: PrivateWriteCtx,
    request: Request,
) -> PrivateObjectInitiateOut:
    """`core.content.private_document.write`, floored at OWN.

    Returns a presigned `multipart/form-data` POST: submit `upload_fields` as form fields, the
    file itself as the last part named `file`, directly to `upload_url` — never through this
    API, and never with any credential this service holds. Nothing is stored, listable, or
    downloadable until `POST .../{id}/complete` succeeds; a client that never calls it has
    uploaded nothing that any other route here will acknowledge.
    """
    cfg = settings()
    row, presigned = await services.initiate_private_object(
        session,
        private_store,
        ctx,
        filename=body.filename,
        content_type=body.content_type,
        size_bytes=body.size_bytes,
        purpose=body.purpose,
        cfg=cfg,
        request=request,
    )
    await session.flush()
    return PrivateObjectInitiateOut(
        id=row.id,
        upload_url=presigned.url,
        upload_fields=presigned.fields,
        upload_expires_at=row.upload_expires_at,
    )


@router.post(
    "/documents/private-objects/{object_id}/complete",
    response_model=PrivateObjectOut,
    summary="Confirm a private document upload",
)
async def complete_private_object(
    object_id: uuid.UUID,
    session: SessionDep,
    private_store: PrivateStoreDep,
    ctx: PrivateWriteCtx,
    request: Request,
) -> PrivateObjectOut:
    """`core.content.private_document.write`, floored at OWN.

    Re-reads the object directly from storage — real size, real checksum, real type from the
    bytes — and refuses (415/422/413-shaped `BusinessRuleProblem`) rather than trusting anything
    the `initiate` call declared. Idempotent: completing an already-`stored` row returns it
    unchanged.
    """
    row = await selectors.get_private_object(session, ctx, object_id)
    if row is None:
        raise NotFoundProblem("No such upload")
    cfg = settings()
    row = await services.complete_private_object(
        session, private_store, ctx, row, cfg=cfg, request=request
    )
    await session.flush()
    return _private_to_out(row)


@router.get(
    "/documents/private-objects/{object_id}",
    response_model=PrivateObjectDownloadOut,
    summary="A private document's metadata and a signed download URL",
)
async def get_private_object(
    object_id: uuid.UUID,
    session: SessionDep,
    private_store: PrivateStoreDep,
    ctx: PrivateReadCtx,
    request: Request,
) -> PrivateObjectDownloadOut:
    """`core.content.private_document.read`, floored at OWN.

    Authorization happens twice in one call, and both are load-bearing: `require(...)` checks
    the permission exists, and `selectors.get_private_object` narrows *which rows* an OWN grant
    actually reaches, exactly as `ScopeContext.apply` is built to. A cross-tenant id and an
    id belonging to a different uploader under an OWN grant are both a 404, never a 403 —
    confirming existence is itself a disclosure.

    A `pending` row (upload not yet completed) is also a 404: there is nothing to download yet,
    and a `pending` row's `storage_key` may point at an object that failed verification.

    Issuing the download URL is a sensitive read and is audited every time this is called, not
    only when a human happens to open the link (CLAUDE.md: unmasking PII needs its own
    permission and writes an audit row every time).
    """
    row = await selectors.get_private_object(session, ctx, object_id)
    if row is None or row.status != "stored":
        raise NotFoundProblem("No such document")
    cfg = settings()
    download_url, expires_at = await services.build_download(
        session, private_store, ctx, row, cfg=cfg, request=request
    )
    await session.flush()
    out = _private_to_out(row)
    return PrivateObjectDownloadOut(
        **out.model_dump(),
        download_url=download_url,
        download_url_expires_at=expires_at,
    )


@router.delete(
    "/documents/private-objects/{object_id}",
    status_code=204,
    summary="Remove a private document before it is attached to anything",
)
async def delete_private_object(
    object_id: uuid.UUID,
    session: SessionDep,
    private_store: PrivateStoreDep,
    ctx: PrivateWriteCtx,
    request: Request,
) -> None:
    """`core.content.private_document.write`, floored at OWN.

    Hard delete — object then row — and refused (409) once a future attach step has set
    `attached_at`. There is no `?purge=` flag here: unlike a public asset, a private object has
    no grace window to opt out of, because nothing outside this service can reference it while
    it is unattached.
    """
    row = await selectors.get_private_object(session, ctx, object_id)
    if row is None:
        raise NotFoundProblem("No such document")
    await services.remove_private_object(session, private_store, ctx, row, request=request)
    await session.flush()

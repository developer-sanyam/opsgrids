"""HTTP surface for quality (plan Phase 4), mounted behind the gateway's `/api/v1` prefix.

Every route declares its permission and is floored at ORG: an inspection is a shared operational
object — there is no useful "the inspections I personally raised" reading of whether stock may be
released. `approve` and `reject` are dangerous in the catalogue because they are the steps that
move stock out of quality hold. A row outside the caller's organization is a 404, never a 403.

Reads go through `selectors`, writes through `services`; a router never builds its own query. Every
mutating route accepts an `Idempotency-Key`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Inspection, QcTemplate
from app.quality import selectors, services
from app.quality.schemas import (
    DispositionIn,
    InspectionIn,
    InspectionNoteIn,
    InspectionOut,
    InspectionResultsIn,
    QcTemplateIn,
    QcTemplateOut,
    QcTemplatePatch,
    RejectIn,
    ReleaseIn,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.db import get_tenant_session
from platform_core.errors import NotFoundProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(tags=["operations-quality"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
Entitled = Depends(require_product("operations"))

QcRead = Annotated[
    ScopeContext, Depends(require("operations.quality.inspection.read", minimum=Scope.ORG))
]
QcInspect = Annotated[
    ScopeContext, Depends(require("operations.quality.inspection.inspect", minimum=Scope.ORG))
]
QcApprove = Annotated[
    ScopeContext, Depends(require("operations.quality.inspection.approve", minimum=Scope.ORG))
]
QcReject = Annotated[
    ScopeContext, Depends(require("operations.quality.inspection.reject", minimum=Scope.ORG))
]

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description="Retrying with the same key replays the first result instead of acting "
        "again; reusing it for a different request is a 409.",
    ),
]


async def _template_out(
    session: AsyncSession, org_id: uuid.UUID, template: QcTemplate
) -> QcTemplateOut:
    await session.refresh(template)  # see `_inspection_out`
    params = await selectors.params_for_templates(session, org_id, [template.id])
    return selectors.template_out(template, params[template.id])


async def _template_or_404(
    session: AsyncSession, org_id: uuid.UUID, template_id: uuid.UUID, *, for_update: bool = False
) -> QcTemplate:
    template = await selectors.get_template(session, org_id, template_id, for_update=for_update)
    if template is None:
        raise NotFoundProblem("That QC template is not part of this organization")
    return template


async def _inspection_or_404(
    session: AsyncSession, org_id: uuid.UUID, inspection_id: uuid.UUID, *, for_update: bool = False
) -> Inspection:
    inspection = await selectors.get_inspection(
        session, org_id, inspection_id, for_update=for_update
    )
    if inspection is None:
        raise NotFoundProblem("That inspection is not part of this organization")
    return inspection


async def _inspection_out(
    session: AsyncSession, org_id: uuid.UUID, inspection: Inspection
) -> InspectionOut:
    # Every service call flushes before returning, and a later flush re-applies the table's
    # `onupdate` timestamp server-side, leaving `updated_at` expired — reload so serialising never
    # triggers a lazy load outside the greenlet.
    await session.refresh(inspection)
    return (await selectors.inspections_out(session, org_id, [inspection]))[0]


# ------------------------------------------------------------------ templates


@router.get(
    "/operations/quality/templates",
    response_model=Page[QcTemplateOut],
    summary="QC templates",
    dependencies=[Entitled],
)
async def list_templates(
    session: SessionDep,
    ctx: QcRead,
    params: PageQuery,
    kind: Annotated[str | None, Query(pattern="^(incoming|in-process|finished)$")] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    active: Annotated[bool | None, Query()] = None,
) -> Page[QcTemplateOut]:
    stmt = selectors.templates_stmt(ctx.organization_id, kind=kind, item_id=item_id, active=active)
    rows, total = await paginate(session, stmt, params)
    by_template = await selectors.params_for_templates(
        session, ctx.organization_id, [r.id for r in rows]
    )
    out = [selectors.template_out(r, by_template[r.id]) for r in rows]
    return Page.build(out, total=total, params=params)


@router.post(
    "/operations/quality/templates",
    response_model=QcTemplateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a QC template with its parameters",
    dependencies=[Entitled],
)
async def create_template(
    payload: QcTemplateIn,
    session: SessionDep,
    ctx: QcApprove,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> QcTemplateOut:
    template = await services.create_template(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    return await _template_out(session, ctx.organization_id, template)


@router.get(
    "/operations/quality/templates/{template_id}",
    response_model=QcTemplateOut,
    summary="One QC template",
    dependencies=[Entitled],
)
async def read_template(template_id: uuid.UUID, session: SessionDep, ctx: QcRead) -> QcTemplateOut:
    template = await _template_or_404(session, ctx.organization_id, template_id)
    return await _template_out(session, ctx.organization_id, template)


@router.patch(
    "/operations/quality/templates/{template_id}",
    response_model=QcTemplateOut,
    summary="Edit a QC template; a supplied parameter list replaces the old one",
    dependencies=[Entitled],
)
async def patch_template(
    template_id: uuid.UUID,
    payload: QcTemplatePatch,
    session: SessionDep,
    ctx: QcApprove,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> QcTemplateOut:
    # PATCH is naturally idempotent (same body, same end state); the header is accepted for
    # client uniformity and needs no stored key.
    template = await _template_or_404(session, ctx.organization_id, template_id, for_update=True)
    await services.update_template(session, ctx, template, payload, request=request)
    return await _template_out(session, ctx.organization_id, template)


@router.delete(
    "/operations/quality/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a QC template (inspections already judged keep their results)",
    dependencies=[Entitled],
)
async def delete_template(
    template_id: uuid.UUID,
    session: SessionDep,
    ctx: QcApprove,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> Response:
    template = await _template_or_404(session, ctx.organization_id, template_id, for_update=True)
    await services.delete_template(session, ctx, template, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ inspections


@router.get(
    "/operations/quality/inspections",
    response_model=Page[InspectionOut],
    summary="Inspections",
    dependencies=[Entitled],
)
async def list_inspections(
    session: SessionDep,
    ctx: QcRead,
    params: PageQuery,
    inspection_status: Annotated[
        str | None,
        Query(alias="status", pattern="^(PENDING|PASSED|FAILED|HOLD|REWORK|PARTIAL_PASS)$"),
    ] = None,
    kind: Annotated[str | None, Query(pattern="^(incoming|in-process|finished)$")] = None,
    item_id: Annotated[uuid.UUID | None, Query()] = None,
    lot_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[InspectionOut]:
    stmt = selectors.inspections_stmt(
        ctx.organization_id, status=inspection_status, kind=kind, item_id=item_id, lot_id=lot_id
    )
    rows, total = await paginate(session, stmt, params)
    out = await selectors.inspections_out(session, ctx.organization_id, rows)
    return Page.build(out, total=total, params=params)


@router.post(
    "/operations/quality/inspections",
    response_model=InspectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Open an inspection; by default moves the quantity AVAILABLE -> QUALITY_HOLD",
    dependencies=[Entitled],
)
async def create_inspection(
    payload: InspectionIn,
    session: SessionDep,
    ctx: QcInspect,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await services.create_inspection(
        session, ctx, payload, idempotency_key=idempotency_key, request=request
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.get(
    "/operations/quality/inspections/{inspection_id}",
    response_model=InspectionOut,
    summary="One inspection with its results",
    dependencies=[Entitled],
)
async def read_inspection(
    inspection_id: uuid.UUID, session: SessionDep, ctx: QcRead
) -> InspectionOut:
    inspection = await _inspection_or_404(session, ctx.organization_id, inspection_id)
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.put(
    "/operations/quality/inspections/{inspection_id}/results",
    response_model=InspectionOut,
    summary="Record readings (replace by parameter name); the server judges pass/fail",
    dependencies=[Entitled],
)
async def record_results(
    inspection_id: uuid.UUID,
    payload: InspectionResultsIn,
    session: SessionDep,
    ctx: QcInspect,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    # Replace-by-name is naturally idempotent; the header needs no stored key.
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.record_results(session, ctx, inspection, payload.results, request=request)
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/hold",
    response_model=InspectionOut,
    summary="Keep the stock on quality hold for a second look (PENDING -> HOLD)",
    dependencies=[Entitled],
)
async def hold_inspection(
    inspection_id: uuid.UUID,
    payload: InspectionNoteIn,
    session: SessionDep,
    ctx: QcInspect,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.hold(
        session,
        ctx,
        inspection,
        note=payload.note,
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/rework",
    response_model=InspectionOut,
    summary="Send the stock back for rework; it stays on quality hold",
    dependencies=[Entitled],
)
async def rework_inspection(
    inspection_id: uuid.UUID,
    payload: InspectionNoteIn,
    session: SessionDep,
    ctx: QcInspect,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.rework(
        session,
        ctx,
        inspection,
        note=payload.note,
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/reinspect",
    response_model=InspectionOut,
    summary="Reworked stock is ready to measure again (REWORK -> PENDING, readings cleared)",
    dependencies=[Entitled],
)
async def reinspect_inspection(
    inspection_id: uuid.UUID,
    payload: InspectionNoteIn,
    session: SessionDep,
    ctx: QcInspect,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.reinspect(
        session,
        ctx,
        inspection,
        note=payload.note,
        idempotency_key=idempotency_key,
        request=request,
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/release",
    response_model=InspectionOut,
    summary="Release held stock to AVAILABLE (PASSED), or part of it (PARTIAL_PASS)",
    dependencies=[Entitled],
)
async def release_inspection(
    inspection_id: uuid.UUID,
    payload: ReleaseIn,
    session: SessionDep,
    ctx: QcApprove,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.release(
        session, ctx, inspection, payload, idempotency_key=idempotency_key, request=request
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/reject",
    response_model=InspectionOut,
    summary="Reject held stock (FAILED): QUALITY_HOLD -> REJECTED or QUARANTINE",
    dependencies=[Entitled],
)
async def reject_inspection(
    inspection_id: uuid.UUID,
    payload: RejectIn,
    session: SessionDep,
    ctx: QcReject,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.reject(
        session, ctx, inspection, payload, idempotency_key=idempotency_key, request=request
    )
    return await _inspection_out(session, ctx.organization_id, inspection)


@router.post(
    "/operations/quality/inspections/{inspection_id}/disposition",
    response_model=InspectionOut,
    summary="Return rejected stock to the vendor, or scrap it",
    dependencies=[Entitled],
)
async def dispose_inspection(
    inspection_id: uuid.UUID,
    payload: DispositionIn,
    session: SessionDep,
    ctx: QcReject,
    request: Request,
    idempotency_key: IdempotencyKey = None,
) -> InspectionOut:
    inspection = await _inspection_or_404(
        session, ctx.organization_id, inspection_id, for_update=True
    )
    await services.dispose(
        session, ctx, inspection, payload, idempotency_key=idempotency_key, request=request
    )
    return await _inspection_out(session, ctx.organization_id, inspection)

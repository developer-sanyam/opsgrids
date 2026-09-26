"""WhatsApp template management (slice W2): read one, sync from Meta, create and submit, preview.

Mounted beside `engage.py`'s router under the same `/engage/whatsapp/templates` prefix, gated the
same way (the `engage` entitlement and Engage's own `whatsapp.template.*` permissions, floored at
ORG). The list route stays in `engage.py`, extended to `template_out`'s shape.

**Nothing here returns a credential.** The token is opened only inside `w2_services` for the
length of one Graph call.

**The two routes that call Meta do not hold a transaction across it.** `sync` and `create` bind
two short transactions of their own around the call, the idiom `verify_domain` uses for DNS: a
slow Meta must not pin a pool connection for the length of its timeout.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import w2_domain, w2_selectors, w2_services
from app.models import ProviderTemplate
from app.w2_schemas import (
    SyncOut,
    TemplateCreateIn,
    TemplateHistoryOut,
    TemplatePreviewIn,
    TemplatePreviewOut,
    TemplateVariableSpecOut,
    WhatsAppTemplateDetailOut,
    WhatsAppTemplateOut,
)
from platform_core.auth import Scope, ScopeContext, require, require_product
from platform_core.cache import enforce_rate_limit
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, get_tenant_session, set_tenant
from platform_core.errors import BusinessRuleProblem, NotFoundProblem, ValidationProblem

router = APIRouter(tags=["engage"])

SessionDep = Annotated[AsyncSession, Depends(get_tenant_session, scope="function")]
RawSessionDep = Annotated[AsyncSession, Depends(get_session)]
Entitled = Depends(require_product("engage"))

TemplateRead = Annotated[
    ScopeContext, Depends(require("engage.whatsapp.template.read", minimum=Scope.ORG))
]
TemplateWrite = Annotated[
    ScopeContext, Depends(require("engage.whatsapp.template.write", minimum=Scope.ORG))
]

#: Both routes below spend Meta's rate limit, which is per WABA and shared with everything else the
#: organization does through it. Per organization per minute; not configuration, because the
#: right number is Meta's, not a deployment's.
SYNC_PER_MINUTE = 6
CREATE_PER_MINUTE = 20


def graph_transport() -> httpx.AsyncBaseTransport | None:
    """The HTTP transport for Graph calls. `None` is the real network; tests override this
    dependency with an `httpx.MockTransport`."""
    return None


GraphTransportDep = Annotated[httpx.AsyncBaseTransport | None, Depends(graph_transport)]


# --------------------------------------------------------------------- assemblers


def variables_out(row: ProviderTemplate) -> list[TemplateVariableSpecOut]:
    """The placeholders, in the contract's `{index, name, kind, example}` shape, with the send
    path's mapping beside each when it lines up. A row registered by hand before specs existed
    has only the mapping, so its specs are derived from it."""
    mapping = list(row.variables or [])
    specs = list(row.variable_specs or [])
    if specs:
        paired = len(specs) == len(mapping)
        out = []
        for i, spec in enumerate(specs):
            m: dict[str, Any] = mapping[i] if paired else {}
            out.append(
                TemplateVariableSpecOut(
                    index=int(spec.get("index", i + 1)),
                    name=str(spec.get("name", "")),
                    kind=spec.get("kind", "text"),
                    example=str(spec.get("example", "")),
                    key=m.get("key"),
                    component=m.get("component"),
                    position=m.get("position"),
                    button_index=m.get("button_index"),
                    parameter_name=m.get("parameter_name"),
                )
            )
        return out
    return [
        TemplateVariableSpecOut(
            index=i + 1,
            name=str(m.get("parameter_name") or m.get("key") or i + 1),
            kind="url" if m.get("component") == "button" and m.get("kind") != "payload" else "text",
            example="",
            key=m.get("key"),
            component=m.get("component"),
            position=m.get("position"),
            button_index=m.get("button_index"),
            parameter_name=m.get("parameter_name"),
        )
        for i, m in enumerate(mapping)
    ]


def template_out(row: ProviderTemplate) -> WhatsAppTemplateOut:
    return WhatsAppTemplateOut(
        id=row.code,
        row_id=row.id,
        channel=row.channel,  # type: ignore[arg-type]
        name=row.provider_name,
        provider_name=row.provider_name,
        meta_template_id=row.provider_template_id,
        provider_template_id=row.provider_template_id,
        language=row.language,
        category=row.category,  # type: ignore[arg-type]
        parameter_format=row.parameter_format,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        quality=row.quality,  # type: ignore[arg-type]
        rejection_reason=row.status_reason,
        status_reason=row.status_reason,
        status_checked_at=row.status_checked_at,
        components=list(row.components or []),
        body_preview=row.preview_text,
        preview_text=row.preview_text,
        variables=variables_out(row),
        # Derived, so it can never disagree with `status`.
        sendable=row.status == "approved",
        submitted_at=row.submitted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def detail_out(row: ProviderTemplate) -> WhatsAppTemplateDetailOut:
    history = [
        TemplateHistoryOut(status=h["status"], at=h["at"], source=h["source"])
        for h in (row.history or [])
        if isinstance(h, dict) and {"status", "at", "source"} <= h.keys()
    ]
    return WhatsAppTemplateDetailOut(**template_out(row).model_dump(), history=history)


# --------------------------------------------------------------------- routes


@router.post(
    "/engage/whatsapp/templates/sync",
    response_model=SyncOut,
    summary="Pull the WABA's templates from Meta",
    dependencies=[Entitled],
)
async def sync_whatsapp_templates(
    session: RawSessionDep,
    ctx: TemplateWrite,
    request: Request,
    transport: GraphTransportDep,
) -> SyncOut:
    """Reads every template Meta holds for this organization's WABA and folds it into the rows:
    new ones created, changed ones updated, ones Meta no longer has removed (soft-deleted).

    Write permission although it reads Meta: it rewrites our rows, including their status."""
    await enforce_rate_limit(
        f"engage.wa_templates_sync:{ctx.organization_id}", limit=SYNC_PER_MINUTE, window_seconds=60
    )
    cfg: ServiceSettings = request.app.state.settings

    async def _bind() -> None:
        await set_tenant(session, ctx.organization_id)
        if not cfg.is_production:
            await assert_tenant(session, ctx.organization_id)

    async with session.begin():
        await _bind()
        creds = await w2_services.load_credentials(session, ctx.organization_id)

    remote = await w2_services.fetch_remote(creds, transport=transport)

    async with session.begin():
        await _bind()
        result = await w2_services.apply_sync(
            session, ctx.organization_id, remote, actor_id=ctx.principal.sub, request=request
        )
    return SyncOut(
        synced=result.synced, created=result.created, updated=result.updated, removed=result.removed
    )


@router.post(
    "/engage/whatsapp/templates",
    response_model=WhatsAppTemplateDetailOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a template and submit it to Meta for review",
    dependencies=[Entitled],
)
async def create_whatsapp_template(
    body: TemplateCreateIn,
    session: RawSessionDep,
    ctx: TemplateWrite,
    request: Request,
    transport: GraphTransportDep,
) -> WhatsAppTemplateDetailOut:
    """Validates locally (every problem at once, per field, as a 422), submits to Meta, and
    stores the answer as `pending`. Meta's review then moves the status by webhook."""
    await enforce_rate_limit(
        f"engage.wa_templates_create:{ctx.organization_id}",
        limit=CREATE_PER_MINUTE,
        window_seconds=60,
    )
    cfg: ServiceSettings = request.app.state.settings
    components = body.components.model_dump(exclude_none=True)

    async def _bind() -> None:
        await set_tenant(session, ctx.organization_id)
        if not cfg.is_production:
            await assert_tenant(session, ctx.organization_id)

    async with session.begin():
        await _bind()
        creds = await w2_services.check_can_create(
            session,
            ctx,
            name=body.name,
            category=body.category,
            language=body.language,
            components=components,
        )

    accepted = await w2_services.submit_to_meta(
        creds,
        name=body.name,
        category=body.category,
        language=body.language,
        components=components,
        transport=transport,
    )

    async with session.begin():
        await _bind()
        row = await w2_services.record_created(
            session,
            ctx,
            name=body.name,
            category=body.category,
            language=body.language,
            components=components,
            meta_response=accepted,
            request=request,
        )
        return detail_out(row)


@router.get(
    "/engage/whatsapp/templates/{template_ref}",
    response_model=WhatsAppTemplateDetailOut,
    summary="One WhatsApp template, with its status history",
    dependencies=[Entitled],
)
async def get_whatsapp_template(
    template_ref: str, session: SessionDep, ctx: TemplateRead
) -> WhatsAppTemplateDetailOut:
    row = await w2_selectors.get_by_ref(session, ctx, template_ref)
    if row is None:
        raise NotFoundProblem("No such template")
    return detail_out(row)


@router.post(
    "/engage/whatsapp/templates/{template_ref}/preview",
    response_model=TemplatePreviewOut,
    summary="Render a template with sample values",
    dependencies=[Entitled],
)
async def preview_whatsapp_template(
    template_ref: str, body: TemplatePreviewIn, session: SessionDep, ctx: TemplateRead
) -> TemplatePreviewOut:
    """Read permission: it renders text and changes nothing. Strict — a placeholder with no value
    is a 422 naming it, not a preview with a hole in it."""
    row = await w2_selectors.get_by_ref(session, ctx, template_ref)
    if row is None:
        raise NotFoundProblem("No such template")
    components = list(row.components or [])
    if not components and row.preview_text:
        # Registered by hand before components were kept: its wording is the preview text.
        components = [{"type": "BODY", "text": row.preview_text}]
    if not components:
        raise BusinessRuleProblem("This template has no wording to preview.")
    try:
        rendered = w2_domain.render_preview(components, body.values)
    except w2_domain.PreviewError as exc:
        raise ValidationProblem(
            "Some variables have no value.",
            errors=[{"field": f"values.{m}", "message": "required"} for m in exc.missing],
        ) from exc
    return TemplatePreviewOut(**rendered)

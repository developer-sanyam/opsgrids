"""The internal API — service-to-service, never reachable from outside the cluster.

One route: `leads:export`, the read behind Engage's cold e-mail to leads (ADR-0038 addendum
2026-10-16). Notification builds the audience of a leads campaign from it, the same shape as
customer's `contacts:export` (C53). Lead keeps the key that opens the sealed address; notification
asks for the leads it may mail under a token addressed to lead by name.

**The caller's scope is the caller's to state.** A service token proves which service is calling and
which organization it speaks for, never which rep is behind it. Notification reads the actor's
`crm.lead.lead.read` grant from the principal and passes `owner_id` for an OWN reader; this route
applies it in the query. Every read of real addresses is audited once, with a count and never a
value.

Mounted at the root and excluded from the OpenAPI document.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import services
from app.models import AuditLog, Lead
from app.schemas import LeadExportItem, LeadExportOut
from platform_core.audit import record_audit
from platform_core.auth import require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import ForbiddenProblem, ValidationProblem
from platform_core.logging import get_logger

log = get_logger("lead.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_EXPORT_MAX_IDS = 100
_EXPORT_MAX_LIMIT = 500


def _require_caller(request: Request, *expected: str) -> None:
    caller = getattr(request.state, "service_caller", "unknown-service")
    if caller not in expected:
        raise ForbiddenProblem(f"{caller!r} may not call this endpoint")


def _require_org_claim(request: Request, organization_id: uuid.UUID) -> None:
    claims = getattr(request.state, "service_claims", None) or {}
    if claims.get("org") != str(organization_id):
        raise ForbiddenProblem("This token is not addressed to this organization")


@router.get(
    "/organizations/{organization_id}/leads:export",
    response_model=LeadExportOut,
    status_code=status.HTTP_200_OK,
    summary="Leads for Engage's cold e-mail audience (name, company, status, source, e-mail)",
)
async def export_leads(
    organization_id: uuid.UUID,
    session: SessionDep,
    request: Request,
    ids: str | None = None,
    lead_status: Annotated[str | None, Query(alias="status")] = None,
    source: str | None = None,
    owner_id: uuid.UUID | None = None,
    after_id: uuid.UUID | None = None,
    limit: int = 200,
    emails: bool = True,
) -> LeadExportOut:
    """Notification only, for a token whose `org` claim names this path's organization.
    `owner_id` is the OWN narrowing the caller resolved; `emails=false` returns ids, names and
    states only (the timeline's scope check needs no address, so it opens none and audits none).
    Keyset-paged on the lead id."""
    _require_caller(request, "notification")
    _require_org_claim(request, organization_id)
    if not 1 <= limit <= _EXPORT_MAX_LIMIT:
        raise ValidationProblem(f"limit must be between 1 and {_EXPORT_MAX_LIMIT}")
    wanted: list[uuid.UUID] | None = None
    if ids is not None:
        try:
            wanted = [uuid.UUID(part) for part in ids.split(",")]
        except ValueError:
            raise ValidationProblem("ids must be a comma-separated list of UUIDs") from None
        if len(wanted) > _EXPORT_MAX_IDS:
            raise ValidationProblem(f"ids is limited to {_EXPORT_MAX_IDS}")

    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        stmt = select(Lead).where(
            Lead.organization_id == organization_id, Lead.deleted_at.is_(None)
        )
        if wanted is not None:
            stmt = stmt.where(Lead.id.in_(wanted))
        if lead_status is not None:
            stmt = stmt.where(Lead.status == lead_status)
        if source is not None:
            stmt = stmt.where(Lead.source == source)
        if owner_id is not None:
            stmt = stmt.where(Lead.owner_id == owner_id)
        if after_id is not None:
            stmt = stmt.where(Lead.id > after_id)
        rows = list((await session.execute(stmt.order_by(Lead.id.asc()).limit(limit))).scalars())
        addresses = services.open_lead_emails(rows) if emails else {}
        if emails:
            record_audit(
                session,
                AuditLog,
                organization_id=organization_id,
                actor_id=None,
                acting_as=getattr(request.state, "service_caller", "unknown-service"),
                action="lead.exported_for_engage",
                entity="lead",
                after={"count": len(rows), "with_email": len(addresses)},
                request=request,
            )

    log.info("lead.export_for_engage.done", organization_id=str(organization_id), count=len(rows))
    return LeadExportOut(
        items=[
            LeadExportItem(
                lead_id=str(lead.id),
                full_name=lead.full_name,
                company=lead.company,
                email=addresses.get(lead.id),
                status=lead.status,
                source=lead.source,
                owner_id=str(lead.owner_id) if lead.owner_id else None,
            )
            for lead in rows
        ],
        next_after_id=str(rows[-1].id) if len(rows) == limit and wanted is None else None,
    )

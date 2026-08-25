"""The internal API — service-to-service, never reachable from outside the cluster.

E-invoice filing is not a button. The obligation is time-boxed — the reporting window is
tightening toward three days from the document date — so the thing that files is a scheduled
agent draining a queue, not a person remembering. These are the endpoints that agent calls, and
they exist now, with a stubbed portal behind them, so the agent has something real to be written
against rather than something to be designed alongside.

There is no person and no gateway-forwarded principal here — the caller is another service — so
these routes do not take `get_tenant_session`. Each binds the transaction to the **organization
named in the request body** as its first statement, exactly as `get_tenant_session` binds to a
principal's organization, so row-level security and every `WITH CHECK` apply to these writes too.

Guarded by a short-lived service token addressed to this service by name (`require_service_token`).
Mounted at the root and excluded from the OpenAPI document: not public API, and it must not appear
as though it were.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors, services
from app.deps import get_irp_client
from app.irp.base import IrpClient
from app.schemas import CancelFilingIn, EInvoiceFilingOut, FileInvoiceIn
from platform_core.auth import Principal, Scope, ScopeContext, require_service_token
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.logging import get_logger

log = get_logger("finance.internal")

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(require_service_token)],
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
IrpDep = Annotated[IrpClient, Depends(get_irp_client)]

#: The permission an internal caller writes under. Named rather than omitted: the audit and
#: outbox rows every filing writes need an actor and a scope, and a service acting on an
#: organization's behalf must be as identifiable in the trail as a person would be.
_FILING_PERMISSION = "finance.compliance.einvoice.read"


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the payload's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


def _service_ctx(organization_id: uuid.UUID) -> ScopeContext:
    """The scope context a service-to-service filing runs under.

    `sub` is a stable, all-zero UUID rather than a random one, so every audit row a filing agent
    writes is attributable to "the platform" and not to twenty ids nobody can join.
    """
    principal = Principal(
        sub=uuid.UUID(int=0),
        organization_id=organization_id,
        membership_id=None,
        perms=[f"{_FILING_PERMISSION}:ORG"],
        entitlements=["finance"],
    )
    return ScopeContext(principal=principal, scope=Scope.ORG, permission=_FILING_PERMISSION)


@router.post(
    "/einvoice/filings",
    response_model=EInvoiceFilingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register one document with the IRP and record the attempt",
)
async def file_invoice(
    payload: FileInvoiceIn, session: SessionDep, request: Request, client: IrpDep
) -> EInvoiceFilingOut:
    """File one invoice. **Always returns the appended row**, success or refusal alike.

    A portal rejection is a 201 carrying a `failed` filing, not a 4xx: the attempt happened, it is
    now part of the document's history, and the caller's job is to read the error code and decide
    — not to treat a recorded fact as a transport failure. The 4xx responses are reserved for the
    cases where nothing was attempted at all: an unknown invoice, a document that needs no IRN, an
    organization with no registration, one already registered.
    """
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ctx = _service_ctx(payload.organization_id)
        filing = await services.file_invoice(session, ctx, client, payload, request=request)
        result = selectors.to_filing_out(filing)
    log.info(
        "finance.einvoice.attempt",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        filing_status=result.status,
        error_code=result.error_code,
        portal=client.name,
    )
    return result


@router.post(
    "/einvoice/filings:cancel",
    response_model=EInvoiceFilingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Withdraw a registered IRN inside the portal's 24-hour window",
)
async def cancel_filing(
    payload: CancelFilingIn, session: SessionDep, request: Request, client: IrpDep
) -> EInvoiceFilingOut:
    """Cancel a registration. The withdrawal is a new row; the registration row it withdraws is
    left exactly as it was, so the history reads in order."""
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ctx = _service_ctx(payload.organization_id)
        filing = await services.cancel_filing(session, ctx, client, payload, request=request)
        result = selectors.to_filing_out(filing)
    log.info(
        "finance.einvoice.cancelled",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        filing_status=result.status,
        portal=client.name,
    )
    return result

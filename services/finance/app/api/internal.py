"""The internal API — service-to-service, never reachable from outside the cluster.

Neither compliance obligation this service carries is a button. E-invoicing is time-boxed — the
reporting window is tightening toward three days from the document date — so filing happens on
its own, driven by the event consumer; this route is what remains for the cases a person or a
sibling service names one document deliberately: a re-file after the document was corrected, a
cancellation inside the 24-hour window. Pulling the IMS ledger is the same shape from the other
direction: a periodic sync of what suppliers filed against this organization, which nobody sits
and waits for.

Both run against stubbed portals, so the state machines and the persistence are real and testable
today and only the wire is left.

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
from app.deps import get_eway_client, get_ims_client, get_irp_client
from app.eway.base import EwayClient
from app.ims.base import ImsClient
from app.irp.base import IrpClient
from app.schemas import (
    CancelEwayIn,
    CancelFilingIn,
    EInvoiceFilingOut,
    EwayBillOut,
    FileInvoiceIn,
    GenerateEwayIn,
    ImsSyncIn,
    ImsSyncOut,
)
from platform_core.auth import require_service_token
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
ImsDep = Annotated[ImsClient, Depends(get_ims_client)]
EwayDep = Annotated[EwayClient, Depends(get_eway_client)]

#: The scope context a service-to-service compliance write runs under. Defined in `app.services`
#: rather than here because the event consumer needs the same one, and a consumer that imported a
#: router would have grown a second HTTP surface by accident.
_service_ctx = services.service_ctx


async def _bind(session: AsyncSession, organization_id: uuid.UUID, request: Request) -> None:
    """Bind this transaction to the payload's organization — the internal analogue of the
    tenancy chokepoint. Must run as the first statement inside the transaction."""
    settings: ServiceSettings = request.app.state.settings
    await set_tenant(session, organization_id)
    if not settings.is_production:
        await assert_tenant(session, organization_id)


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


@router.post(
    "/eway/bills",
    response_model=EwayBillOut,
    status_code=status.HTTP_201_CREATED,
    summary="Raise an e-Way bill for one document's goods movement and record the attempt",
)
async def generate_eway_bill(
    payload: GenerateEwayIn, session: SessionDep, request: Request, client: EwayDep
) -> EwayBillOut:
    """Raise a bill. **Always returns the appended row**, success or refusal alike.

    Internal rather than principal-facing for the same reason filing is: the transport facts come
    from a dispatch system watching for a finalized consignment, not from somebody retyping a
    vehicle number into a finance screen. A portal rejection is a 201 carrying a `failed` row —
    the attempt happened and is now part of the document's history. The 4xx responses are
    reserved for the cases where nothing was attempted at all: an unknown document, a consignment
    under the threshold, an impossible distance, no registration, a bill already standing.
    """
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ctx = _service_ctx(payload.organization_id)
        bill = await services.generate_eway_bill(session, ctx, client, payload, request=request)
        result = selectors.to_eway_bill_out(bill)
    log.info(
        "finance.eway.attempt",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        eway_status=result.status,
        eway_bill_no=result.eway_bill_no,
        distance_km=result.distance_km,
        valid_until=result.valid_until.isoformat() if result.valid_until else None,
        error_code=result.error_code,
        portal=client.name,
    )
    return result


@router.post(
    "/eway/bills:cancel",
    response_model=EwayBillOut,
    status_code=status.HTTP_201_CREATED,
    summary="Withdraw a raised e-Way bill inside the portal's 24-hour window",
)
async def cancel_eway_bill(
    payload: CancelEwayIn, session: SessionDep, request: Request, client: EwayDep
) -> EwayBillOut:
    """Cancel a bill. The withdrawal is a new row; the row that raised it is left exactly as it
    was, so the history reads in order."""
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ctx = _service_ctx(payload.organization_id)
        bill = await services.cancel_eway_bill(session, ctx, client, payload, request=request)
        result = selectors.to_eway_bill_out(bill)
    log.info(
        "finance.eway.cancelled",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        eway_status=result.status,
        portal=client.name,
    )
    return result


@router.post(
    "/ims/sync",
    response_model=ImsSyncOut,
    status_code=status.HTTP_200_OK,
    summary="Pull one return period's supplier documents from IMS and reconcile them",
)
async def sync_ims(
    payload: ImsSyncIn, session: SessionDep, request: Request, client: ImsDep
) -> ImsSyncOut:
    """Fetch what suppliers filed against this organization, then work out what it means.

    Two writes in one transaction, and they stay separable on purpose: the ingest is idempotent on
    the portal's own row reference, so re-syncing a period creates nothing; the reconciliation is
    re-run every time, because the *answers* change when the purchase book does even though the
    supplier's documents have not. That is the ordinary case — the invoice did not change, our
    record of it did — and it is why re-running this is cheap and safe rather than something to be
    careful about.

    A 200 rather than a 201: a sync is a read of somebody else's ledger followed by a recomputation
    of our opinion about it, and on a second call it creates nothing at all.
    """
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ctx = _service_ctx(payload.organization_id)
        ingest = await services.ingest_ims_documents(
            session, ctx, client, period=payload.period, request=request
        )
        reconciled = await services.reconcile_ims_documents(session, ctx, period=payload.period)
        result = ImsSyncOut(
            period=payload.period,
            portal=client.name,
            fetched=ingest.fetched,
            created=ingest.created,
            reconciled=reconciled.reconciled,
            run_id=reconciled.run_id,
        )
    log.info(
        "finance.ims.synced",
        organization_id=str(payload.organization_id),
        period=payload.period,
        fetched=result.fetched,
        created=result.created,
        reconciled=result.reconciled,
        run_id=str(result.run_id),
        portal=client.name,
    )
    return result

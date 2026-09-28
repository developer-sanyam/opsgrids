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

One route since is shaped differently on purpose: `customers:resolve-for-crm-contact`, the
identity-bridge slice. `organization_id` travels in the **path**, not the body — the same shape
`services/customer`'s `leads:convert` and `owners:validate` take — because this route is
restricted to one named caller (`_require_caller`; operations, once a later slice wires it to
call `services.ensure_customer_for_crm_contact`) and to a token whose `org` claim names that same
path organization (`_require_org_claim`): a valid signature proves *which service* is calling,
never *which route it was meant for* or *which tenant*, and both are separate checks here, unlike
the compliance routes above which any service holding a token addressed to finance may call.
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
    CrmContactResolveIn,
    CrmContactResolveOut,
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
from platform_core.errors import ForbiddenProblem
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


def _require_caller(request: Request, *expected: str) -> None:
    """Restrict an internal route to a named allow-list of calling services, on top of the
    valid-signature check `require_service_token` already did. Same reasoning, and the same
    function shape, as `services/customer`'s own `api/internal.py`: a validly signed token from
    a service this route was never meant to serve is not a forged token — it is a service using
    a capability nobody meant to give it. `customers:resolve-for-crm-contact` in particular can
    create a billed party from a bare CRM contact id, which is operations' business to ask for
    on an order's behalf and nobody else's."""
    caller = getattr(request.state, "service_caller", "unknown-service")
    if caller not in expected:
        raise ForbiddenProblem(f"{caller!r} may not call this endpoint")


def _require_org_claim(request: Request, organization_id: uuid.UUID) -> None:
    """The token's own `org` claim must name the organization in the path. The signature and
    audience alone only prove *which service* minted the token, never *which organization* it
    was minted for — a service holding one valid token could otherwise replay it against any
    path it can construct. `_bind`'s `assert_tenant` catches a mismatch here too, but only
    outside production; this check is the one that holds everywhere. Same shape as
    `services/customer`'s own `_require_org_claim`."""
    claims = getattr(request.state, "service_claims", None) or {}
    if claims.get("org") != str(organization_id):
        raise ForbiddenProblem("This token is not addressed to this organization")


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

    Three acts, at most two transactions, and the portal call spans neither (D3): **claim**
    (`services.claim_manual_filing`, which raises every refusal above before anything is
    written), **call** (`services.call_irp_register`, no session), **record**
    (`services.record_manual_filing`). A process that dies between the call and the record
    leaves no filing row behind at all — see `services.stage_registration_attempt` — and
    `app/cron.py`'s filing-reclaim sweep is what notices and resumes such an invoice.
    """
    ctx = _service_ctx(payload.organization_id)
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        claim = await services.claim_manual_filing(session, ctx, payload)

    call_outcome = await services.call_irp_register_off_loop(client, claim.irp_payload)

    async with session.begin():
        await _bind(session, payload.organization_id, request)
        filing = await services.record_manual_filing(
            session,
            ctx,
            client.name,
            claim.invoice.id,
            call_outcome,
            at=claim.at,
            request=request,
        )
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
    left exactly as it was, so the history reads in order.

    Three acts, two short transactions, and the portal call spans neither (D3): **claim**
    (`services.claim_irn_cancellation` — every refusal, then a `cancelling` row carrying the
    reason, audited), **call** (`services.call_irp_cancel`, no session), **record**
    (`services.record_irn_cancellation`). Returns the newest row: `cancelled`, `failed` on a
    portal refusal, or the `cancelling` claim itself when the portal could not be reached — the
    withdrawal is then owed, and `app/cron.py`'s reclaim sweep finishes it.
    """
    ctx = _service_ctx(payload.organization_id)
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        claim = await services.claim_irn_cancellation(session, ctx, payload, request=request)

    outcome = await services.call_irp_cancel_off_loop(client, claim.facts)

    async with session.begin():
        await _bind(session, payload.organization_id, request)
        filing = await services.record_irn_cancellation(
            session, ctx, claim.facts, outcome, at=claim.facts.at, request=request
        )
        result = selectors.to_filing_out(filing)
    log.info(
        "finance.einvoice.cancel_attempt",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        filing_status=result.status,
        outcome=outcome.kind,
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
    under the threshold, an impossible distance, no registration, a bill already standing, a
    generation already in flight (409).

    Three acts, two short transactions, and the portal call spans neither (D3): **claim**
    (`services.claim_eway_generation`, a `submitting` row carrying the transport facts),
    **call** (`services.call_eway_generate`, no session, idempotent by the (document,
    transporter/vehicle part) key), **record** (`services.record_eway_generation`).
    """
    ctx = _service_ctx(payload.organization_id)
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        claim = await services.claim_eway_generation(session, ctx, payload, request=request)

    outcome = await services.call_eway_generate_off_loop(client, claim.payload)

    async with session.begin():
        await _bind(session, payload.organization_id, request)
        bill = await services.record_eway_generation(
            session, ctx, claim, outcome, portal=client.name, request=request
        )
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
    was, so the history reads in order.

    Claim (`services.claim_eway_cancellation`, a `cancelling` row), call
    (`services.call_eway_cancel`, no transaction), record (`services.record_eway_cancellation`)
    — the same split as `cancel_filing`, with the same answer on an outage: the `cancelling`
    claim, which the reclaim sweep finishes.
    """
    ctx = _service_ctx(payload.organization_id)
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        claim = await services.claim_eway_cancellation(session, ctx, payload, request=request)

    outcome = await services.call_eway_cancel_off_loop(client, claim)

    async with session.begin():
        await _bind(session, payload.organization_id, request)
        bill = await services.record_eway_cancellation(
            session, ctx, claim, outcome, request=request
        )
        result = selectors.to_eway_bill_out(bill)
    log.info(
        "finance.eway.cancel_attempt",
        invoice_id=str(payload.invoice_id),
        organization_id=str(payload.organization_id),
        eway_status=result.status,
        outcome=outcome.kind,
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

    The fetch runs with **no transaction open** (D3): a short read first
    (`services.claim_ims_sync` — is the period real, whose ledger), then the portal
    (`services.fetch_ims_period`), then one short transaction for both writes. They stay
    separable on purpose: the ingest is idempotent on the portal's own row reference, so
    re-syncing a period creates nothing; the reconciliation is re-run every time, because the
    *answers* change when the purchase book does even though the supplier's documents have not.
    That is the ordinary case — the invoice did not change, our record of it did — and it is why
    re-running this is cheap and safe rather than something to be careful about.

    A 200 rather than a 201: a sync is a read of somebody else's ledger followed by a recomputation
    of our opinion about it, and on a second call it creates nothing at all. A portal outage is a
    503 and leaves nothing behind.
    """
    ctx = _service_ctx(payload.organization_id)
    async with session.begin():
        await _bind(session, payload.organization_id, request)
        target = await services.claim_ims_sync(session, ctx, period=payload.period)

    fetched = await services.fetch_ims_period_off_loop(client, target)

    async with session.begin():
        await _bind(session, payload.organization_id, request)
        ingest = await services.record_ims_fetch(
            session, ctx, fetched, period=payload.period, portal=client.name, request=request
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


@router.post(
    "/organizations/{organization_id}/customers:resolve-for-crm-contact",
    response_model=CrmContactResolveOut,
    status_code=status.HTTP_200_OK,
    summary="Resolve (or create) the billed party for a CRM contact",
)
async def resolve_customer_for_crm_contact(
    organization_id: uuid.UUID,
    payload: CrmContactResolveIn,
    session: SessionDep,
    request: Request,
) -> CrmContactResolveOut:
    """The identity-bridge slice's write: ensure this organization has a `finance.Customer` for
    a CRM contact (`services/customer`), creating one the first time and reusing it on every
    later call for the same `crm_contact_id`.

    Unlike every other route in this module, this handler does **not** wrap its work in
    `session.begin()` itself: `services.ensure_customer_for_crm_contact` owns its own short
    transactions, because a network call to customer-service sits between two of them (D3) and
    must run with neither open. Tenant-binding happens inside that function instead, once per
    transaction it opens (`services._bind_tenant`), rather than once here.

    Restricted to operations (`_require_caller`) — the only service with a CRM contact id and
    nowhere to send it, `operations.Order.customer_id` — and to a token whose `org` claim names
    this path's organization (`_require_org_claim`), the same pair `leads:convert` and
    `owners:validate` hold lead-service to on customer-service's own internal API.
    """
    _require_caller(request, "operations")
    _require_org_claim(request, organization_id)

    ctx = services.crm_bridge_ctx(organization_id)
    customer = await services.ensure_customer_for_crm_contact(
        session,
        ctx,
        payload.crm_contact_id,
        acting_as=getattr(request.state, "service_caller", "unknown-service"),
        request=request,
    )

    log.info(
        "finance.identity_bridge.resolved",
        organization_id=str(organization_id),
        crm_contact_id=str(payload.crm_contact_id),
        customer_id=str(customer.id),
    )
    return CrmContactResolveOut(customer_id=str(customer.id))

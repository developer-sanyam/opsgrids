"""Operator HTTP surface for billing — the staff credit-ledger and per-org invoice reads.

Fronted by the **operator** gateway at `/api/v1/admin/credit-ledger` and `/api/v1/admin/invoices`,
both `staff_only`: a non-staff caller is refused with the same 404 an unknown path gets, so the
existence of the operator surface is never confirmed to whoever is probing (ADR-0003 §2, mirrors
catalog's and console's admin routers).

* `platform_core.auth.require_operator(roles={"owner", "billing_ops"})` — the ADR-0003
  addendum's role matrix puts the credit ledger and invoices with `billing_ops`; `support` and any
  other role gets a plain 403 once the audience check has already passed.
* The reads run on the **directory session**: BYPASSRLS, SELECT-only on `credit_ledger` and
  `invoices` and nothing else (`opsgrids_billing_directory`), because the operator reads across
  every organization and the tenant-bound application role sees nothing without one already
  chosen.
* Paginated with the platform's ordinary offset `Page`, the same shape every other list
  endpoint in the codebase uses (`platform_core.pagination`) — there is no reason for this one
  list to invent a different contract.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import CreditLedgerEntryAdminOut, InvoiceAdminOut
from platform_core.auth import Principal, require_operator
from platform_core.db import get_directory_session, get_session
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(prefix="/admin", tags=["billing-admin"])

OperatorBilling = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]
#: The app-role session the per-org audit write runs on (bound to the target org inside
#: `services.record_operator_credit_ledger_read`). Not the directory session: that one is
#: SELECT-only.
AppSessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "/credit-ledger",
    response_model=Page[CreditLedgerEntryAdminOut],
    summary="The credit ledger across every organization, filtered and paginated",
)
async def admin_list_credit_ledger(
    session: DirectorySessionDep,
    audit_session: AppSessionDep,
    operator: OperatorBilling,
    params: PageQuery,
    request: Request,
    organization_id: Annotated[uuid.UUID | None, Query()] = None,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    entry_type: Annotated[str | None, Query()] = None,
) -> Page[CreditLedgerEntryAdminOut]:
    stmt = selectors.admin_credit_ledger_query(
        organization_id=organization_id, currency=currency, entry_type=entry_type
    )
    rows, total = await paginate(session, stmt, params)

    if organization_id is not None:
        # A read narrowed to one organization is a per-org read, not the platform overview —
        # audited the same way console's org-detail composite is (MINOR 11 of the security
        # review). Bypass-then-narrow: the ledger rows above already came from the BYPASSRLS
        # directory session; this is billing's own tenant-bound write for the audit row.
        await services.record_operator_credit_ledger_read(
            audit_session,
            organization_id=organization_id,
            actor_id=operator.sub,
            request=request,
        )

    return Page.build(
        [CreditLedgerEntryAdminOut.model_validate(r) for r in rows], total=total, params=params
    )


@router.get(
    "/invoices",
    response_model=Page[InvoiceAdminOut],
    summary="One organization's OpsGrids invoices — read-only, for the staff Billing tab",
)
async def admin_list_invoices(
    session: DirectorySessionDep,
    audit_session: AppSessionDep,
    operator: OperatorBilling,
    params: PageQuery,
    request: Request,
    organization_id: Annotated[uuid.UUID, Query()],
) -> Page[InvoiceAdminOut]:
    """Same gate, session and pagination as `/credit-ledger`; the difference is that the
    organization is **required** — there is no useful platform-wide invoice overview here, and a
    per-org read is what the account detail screen needs. Every call is audited (see
    `services.record_operator_invoices_read`). Read-only: nothing on this router writes an
    invoice, and the directory role could not if it tried (SELECT only)."""
    stmt = selectors.admin_invoices_query(organization_id=organization_id)
    rows, total = await paginate(session, stmt, params)

    await services.record_operator_invoices_read(
        audit_session,
        organization_id=organization_id,
        actor_id=operator.sub,
        request=request,
    )

    return Page.build([selectors.to_invoice_admin_out(r) for r in rows], total=total, params=params)

"""Operator HTTP surface for billing — the staff credit-ledger read.

Fronted by the **operator** gateway at `/api/v1/admin/credit-ledger`, `staff_only`: a non-staff
caller is refused with the same 404 an unknown path gives, so the existence of the operator
surface is never confirmed to whoever is probing (ADR-0003 §2, mirrors catalog's and console's
admin routers).

* `platform_core.auth.require_operator(roles={"owner", "billing_ops"})` — the ADR-0003
  addendum's role matrix puts the credit ledger with `billing_ops`; `support` and any other
  role gets a plain 403 once the audience check has already passed.
* The read runs on the **directory session**: BYPASSRLS, SELECT-only on `credit_ledger` alone
  (`opsgrids_billing_directory`), because the operator reads across every organization at once
  and the tenant-bound application role sees nothing without one already chosen.
* Paginated with the platform's ordinary offset `Page`, the same shape every other list
  endpoint in the codebase uses (`platform_core.pagination`) — there is no reason for this one
  list to invent a different contract.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.schemas import CreditLedgerEntryAdminOut
from platform_core.auth import Principal, require_operator
from platform_core.db import get_directory_session
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(prefix="/admin", tags=["billing-admin"])

OperatorBilling = Annotated[Principal, Depends(require_operator(roles={"owner", "billing_ops"}))]
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]


@router.get(
    "/credit-ledger",
    response_model=Page[CreditLedgerEntryAdminOut],
    summary="The credit ledger across every organization, filtered and paginated",
)
async def admin_list_credit_ledger(
    session: DirectorySessionDep,
    operator: OperatorBilling,
    params: PageQuery,
    organization_id: Annotated[uuid.UUID | None, Query()] = None,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    entry_type: Annotated[str | None, Query()] = None,
) -> Page[CreditLedgerEntryAdminOut]:
    stmt = selectors.admin_credit_ledger_query(
        organization_id=organization_id, currency=currency, entry_type=entry_type
    )
    rows, total = await paginate(session, stmt, params)
    return Page.build(
        [CreditLedgerEntryAdminOut.model_validate(r) for r in rows], total=total, params=params
    )

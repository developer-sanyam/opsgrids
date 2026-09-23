"""Operator HTTP surface for account-service — one organization's directory record.

Fronted by the **operator** gateway at `/api/v1/admin/directory`, `staff_only`: a non-staff
caller is refused with the same 404 an unknown path gives, so the existence of the operator
surface is never confirmed to whoever is probing (ADR-0003 §2, mirrors catalog's and console's
admin routers). This is the **only** staff read into account-service's directory data — the
composite console serves at `GET /admin/organizations/{id}` calls this route with the caller's
own forwarded token, never `/internal/v1`, which authorizes no individual caller at all and
would let any operator-audience caller read every organization on the platform
(`admin-gateway/routes.json`'s own warning about that router).

The read runs on the **directory session**: BYPASSRLS, SELECT-only, because the operator looks
*across* organizations and the tenant-bound application role sees nothing without one already
chosen (ADR-0002 §2). `organizations` carries no row-level security of its own — it is the
tenant, not scoped by one — so the bypass here buys nothing over the app role except the ability
to run with no principal-bound organization at all, which is exactly the shape a cross-tenant
read needs.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors
from app.schemas import OrganizationDirectoryOut
from platform_core.auth import Principal, require_operator
from platform_core.db import get_directory_session
from platform_core.errors import NotFoundProblem

router = APIRouter(prefix="/admin/directory", tags=["account-admin"])

#: Organization read is open to every operator role (ADR-0003 addendum): a support ticket
#: routinely starts with "what does this account look like" before anyone knows whether it is
#: a billing or a catalog question.
OperatorReadAny = Annotated[
    Principal, Depends(require_operator(roles={"owner", "billing_ops", "support"}))
]
DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationDirectoryOut,
    summary="One organization's directory record — staff-only, cross-tenant",
)
async def get_organization_for_operator(
    organization_id: uuid.UUID, session: DirectorySessionDep, operator: OperatorReadAny
) -> OrganizationDirectoryOut:
    org = await selectors.get_organization(session, organization_id)
    if org is None:
        raise NotFoundProblem("No such organization")
    return OrganizationDirectoryOut.model_validate(org)

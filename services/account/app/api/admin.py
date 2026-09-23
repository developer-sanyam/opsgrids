"""Operator HTTP surface for account-service — one organization's directory record.

**Service-to-service, not a bearer route.** This is the **only** staff read into
account-service's directory data — the composite console serves at
`GET /admin/organizations/{id}` calls it — and it is never listed in
`admin-gateway/routes.json`: console calls account-service directly on its own port, the same
shape auth-service and both gateways already use to reach account-service (ADR-0003 §2), and
never `/internal/v1`, which authorizes no individual caller at all and would let any
operator-audience caller read every organization on the platform.

Console composes its own operator's authorization (audience, role, second factor) through its
own `require_operator` before ever calling here, then **asserts** that operator's identity —
`sub`, `operator_roles`, `amr` — in a short-lived service token addressed to this service
(`aud="account"`, `caller="console"`), the same shape `platform_core.internal_auth` already
uses for every other service-to-service call. It does **not** forward the operator's own
bearer token onward: that credential has already done its one job (authorizing the request
that reached console), and there is no reason for a second service to become another place it
can leak. This route re-checks the asserted role and second-factor claims itself rather than
trusting console's word for it, and writes its own audit row for the read, bound to the target
organization — a read of one customer's directory record is exactly the kind of act that must
leave a trace on both sides of the call, not just console's.

The read runs on the **directory session**: BYPASSRLS, SELECT-only, because the operator looks
*across* organizations and the tenant-bound application role sees nothing without one already
chosen (ADR-0002 §2). `organizations` carries no row-level security of its own — it is the
tenant, not scoped by one — so the bypass here buys nothing over the app role except the ability
to run with no principal-bound organization at all, which is exactly the shape a cross-tenant
read needs.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.schemas import OrganizationDirectoryOut
from platform_core.auth import require_service_token
from platform_core.db import get_directory_session, session_scope
from platform_core.errors import ForbiddenProblem, NotFoundProblem, UnauthorizedProblem

router = APIRouter(prefix="/admin/directory", tags=["account-admin"])

#: Organization read is open to every operator role (ADR-0003 addendum): a support ticket
#: routinely starts with "what does this account look like" before anyone knows whether it is
#: a billing or a catalog question.
OPERATOR_ROLES_FOR_READ = frozenset({"owner", "billing_ops", "support"})

#: Only console composes this call — an assertion of someone else's identity is only as good
#: as who is allowed to make it. A service token from anywhere else naming operator claims is
#: not a call this route recognises, whatever it asserts.
TRUSTED_CALLER = "console"

DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]
ServiceTokenDep = Annotated[None, Depends(require_service_token)]


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationDirectoryOut,
    summary="One organization's directory record — service-to-service, asserted by console",
)
async def get_organization_for_operator(
    organization_id: uuid.UUID,
    request: Request,
    session: DirectorySessionDep,
    _service: ServiceTokenDep,
) -> OrganizationDirectoryOut:
    caller = getattr(request.state, "service_caller", None)
    if caller != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")

    claims: dict[str, Any] = getattr(request.state, "service_claims", {})
    operator_sub_raw = claims.get("operator_sub")
    if not operator_sub_raw:
        raise UnauthorizedProblem("Valid service token required")
    operator_amr: set[str] = set(claims.get("operator_amr") or [])
    if "otp" not in operator_amr:
        raise ForbiddenProblem("This operator session has not completed its second factor")
    operator_roles: set[str] = set(claims.get("operator_roles") or [])
    if not OPERATOR_ROLES_FOR_READ.intersection(operator_roles):
        raise ForbiddenProblem(
            "This action needs one of these operator roles: "
            + ", ".join(sorted(OPERATOR_ROLES_FOR_READ))
        )
    operator_sub = uuid.UUID(str(operator_sub_raw))

    org = await selectors.get_organization(session, organization_id)
    if org is None:
        raise NotFoundProblem("No such organization")

    # Bypass-then-narrow (ADR-0002, path 4, the same shape `accept_invitation`'s route uses):
    # the read just ran on the BYPASSRLS directory session, cross-tenant; the audit write
    # opens its own tenant-bound transaction on the application role, scoped to the
    # organization the read was about.
    async with session_scope(organization_id) as audit_session:
        await services.record_operator_directory_read(
            audit_session,
            organization_id=organization_id,
            actor_id=operator_sub,
            request=request,
        )
    return OrganizationDirectoryOut.model_validate(org)

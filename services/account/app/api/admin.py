"""Operator HTTP surface for account-service — one organization's directory record.

**Service-to-service reachability, bearer-verified authorization.** This is the **only** staff
read into account-service's directory data — the composite console serves at
`GET /admin/organizations/{id}` calls it — and it is never listed in
`admin-gateway/routes.json`: console calls account-service directly on its own port, the same
shape auth-service and both gateways already use to reach account-service (ADR-0003 §2), and
never `/internal/v1`, which authorizes no individual caller at all and would let any
operator-audience caller read every organization on the platform.

MINOR 7 of the 2026-09-24 security review reversed this route's original shape. It used to trust
console's own *assertion* of the calling operator's identity (`sub`, `operator_roles`, `amr`)
inside a service token — which meant a service token forged or replayed from anywhere holding
the shared internal-token secret could name **any** operator identity it liked, and this route
had no way to tell. Console now forwards the operator's own bearer access token verbatim in
`X-Operator-Token`, and this route verifies it the same way any operator-audience route
verifies one — via JWKS, checking `aud == "operator"` and `"otp" in amr` — rather than trusting
anyone's word for who is calling. The audit row is bound to the token's own `sub`, not a claim
console handed over. A missing, malformed, non-UUID, or otherwise-unverifiable token is a flat
401: this route does not distinguish *why* a token failed, the same enumeration-resistance
principle every other bearer check on the platform follows.

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

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import selectors, services
from app.config import settings
from app.schemas import OrganizationDirectoryOut
from platform_core.auth import OPERATOR_AUDIENCE, decode_token
from platform_core.db import get_directory_session, session_scope
from platform_core.errors import ForbiddenProblem, NotFoundProblem, UnauthorizedProblem

router = APIRouter(prefix="/admin/directory", tags=["account-admin"])

#: Organization read is open to every operator role (ADR-0003 addendum): a support ticket
#: routinely starts with "what does this account look like" before anyone knows whether it is
#: a billing or a catalog question.
OPERATOR_ROLES_FOR_READ = frozenset({"owner", "billing_ops", "support"})

DirectorySessionDep = Annotated[AsyncSession, Depends(get_directory_session)]

_MISSING_TOKEN = UnauthorizedProblem("A valid operator token is required")


def require_operator_token(request: Request) -> uuid.UUID:
    """Verify `X-Operator-Token` via JWKS and return the operator's own `sub`.

    Deliberately not `platform_core.auth.require_operator`: that dependency resolves a
    `Principal` from *this* request's own bearer token or forwarded gateway headers, and this
    route has neither — console is the caller, not the operator's browser. The token travels
    on its own header, verified exactly the same way `require_operator` verifies one, so a
    forged or stale service token can no longer stand in for an operator identity this route
    never actually checked.
    """
    token = request.headers.get("X-Operator-Token")
    if not token:
        raise _MISSING_TOKEN
    try:
        claims = decode_token(settings(), token, expected_type="access", audience=OPERATOR_AUDIENCE)
    except UnauthorizedProblem as exc:
        raise _MISSING_TOKEN from exc
    if "otp" not in (claims.get("amr") or []):
        raise ForbiddenProblem("This operator session has not completed its second factor")
    roles = set(claims.get("roles") or [])
    if not OPERATOR_ROLES_FOR_READ.intersection(roles):
        raise ForbiddenProblem(
            "This action needs one of these operator roles: "
            + ", ".join(sorted(OPERATOR_ROLES_FOR_READ))
        )
    try:
        return uuid.UUID(str(claims.get("sub")))
    except (TypeError, ValueError) as exc:
        raise _MISSING_TOKEN from exc


OperatorSubDep = Annotated[uuid.UUID, Depends(require_operator_token)]


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationDirectoryOut,
    summary="One organization's directory record — reachable only from console, bearer-verified",
)
async def get_organization_for_operator(
    organization_id: uuid.UUID,
    request: Request,
    session: DirectorySessionDep,
    operator_sub: OperatorSubDep,
) -> OrganizationDirectoryOut:
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

"""Operator HTTP surface for account-service — one organization's directory record.

**Two credentials, both required.** This is the **only** staff read into account-service's
directory data — the composite console serves at `GET /admin/organizations/{id}` calls it — and
it is never listed in `admin-gateway/routes.json`: console calls account-service directly on its
own port, the same shape auth-service and both gateways already use to reach account-service
(ADR-0003 §2), and never `/internal/v1`, which authorizes no individual caller at all and would
let any operator-audience caller read every organization on the platform.

1. **A service token from console** (`X-Internal-Token`, `aud="account"`, `iss="console"`) — *who
   is calling*. It is what keeps the operator's own bearer token, on its own, from being enough:
   a stolen or replayed operator access token presented straight to account, bypassing console
   entirely, is refused here because it does not arrive with console's service token.
2. **The operator's own access token** (`X-Operator-Token`), verified via JWKS — *on whose
   behalf*. MINOR 7 of the 2026-09-24 security review: console does not *assert* an identity (a
   service token can be minted by anything holding the shared secret, and naming any operator in
   one would have been believed); it forwards the token itself, and this route checks
   `aud == "operator"`, `"otp" in amr` and the role exactly as any operator-audience route does,
   and binds the audit row to the token's own `sub`. A missing, malformed, non-UUID or
   otherwise-unverifiable token is a flat 401 — no distinction between *why*.

m6 of the third-round review restored the service-token half that the second round dropped: with
only the operator token required, the operator's access token alone opened a cross-tenant read.

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
from app.schemas import OrganizationDirectoryOut, OrganizationMemberOut
from platform_core.auth import OPERATOR_AUDIENCE, decode_token, require_service_token
from platform_core.db import get_directory_session, session_scope
from platform_core.errors import ForbiddenProblem, NotFoundProblem, UnauthorizedProblem
from platform_core.pagination import Page, PageQuery, paginate

router = APIRouter(prefix="/admin/directory", tags=["account-admin"])

#: Organization read is open to every operator role (ADR-0003 addendum): a support ticket
#: routinely starts with "what does this account look like" before anyone knows whether it is
#: a billing or a catalog question.
OPERATOR_ROLES_FOR_READ = frozenset({"owner", "billing_ops", "support"})

#: Only console composes this call: a service token from anywhere else — however genuine — is
#: not a call this route recognises, whatever operator token rides along with it.
TRUSTED_CALLER = "console"

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


def require_console_service_token(request: Request) -> None:
    """The service-token half (m6): valid, addressed to account, and issued by console."""
    require_service_token(request)
    if getattr(request.state, "service_caller", None) != TRUSTED_CALLER:
        raise UnauthorizedProblem("Valid service token required")


ConsoleServiceDep = Annotated[None, Depends(require_console_service_token)]


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationDirectoryOut,
    summary="One organization's directory record — console's service token and the operator's own",
)
async def get_organization_for_operator(
    organization_id: uuid.UUID,
    request: Request,
    session: DirectorySessionDep,
    _console: ConsoleServiceDep,
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


@router.get(
    "/organizations/{organization_id}/members",
    response_model=Page[OrganizationMemberOut],
    summary="One organization's members — staff-only, email masked",
)
async def list_members_for_operator(
    organization_id: uuid.UUID,
    request: Request,
    session: DirectorySessionDep,
    _console: ConsoleServiceDep,
    operator_sub: OperatorSubDep,
    params: PageQuery,
) -> Page[OrganizationMemberOut]:
    """The Users tab and the Users column's per-org detail (ui/backend-data-2): id, name, a
    masked email, status, joined/last-active and roles by name. Unmasking is out of scope —
    there is no path here that ever returns the plaintext.

    Same two-credential and bypass-then-narrow shape as `get_organization_for_operator` above:
    the list itself runs on the BYPASSRLS directory session (there is no tenant to bind for a
    cross-tenant caller), and the read is audited in its own tenant-bound transaction afterward.
    404 first, same as the detail route, so a caller cannot page an organization that does not
    exist and learn something from the shape of the (empty) response instead.
    """
    org = await selectors.get_organization(session, organization_id)
    if org is None:
        raise NotFoundProblem("No such organization")

    stmt = selectors.organization_members_stmt(organization_id)
    rows, total = await paginate(session, stmt, params)
    items = [selectors.to_operator_member_out(m) for m in rows]

    async with session_scope(organization_id) as audit_session:
        await services.record_operator_members_read(
            audit_session,
            organization_id=organization_id,
            actor_id=operator_sub,
            request=request,
        )
    return Page.build(items, total=total, params=params)

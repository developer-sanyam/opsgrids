"""The internal API — service-to-service, never reachable from outside the cluster.

One endpoint today: the hierarchy backfill approval-service's one-time job reads through
(ADR-0006 §2.1). Employee is the only writer of reporting lines, and the ongoing supply is the
full hierarchy state every write already stages on `hrms.people.employee.onboarded` /
`.updated` / `.transitioned` (`app/services.py`). This is the bulk read for an organization
that already had employees before approval shipped, and it is nothing but a page over the same
state the events carry.

**What actually guards this endpoint, and what does not.** `require_service_token`
(`platform_core.auth`) — and the `verify_service_token` it calls — check only a token's
signature, audience, type and expiry; every other claim is decoded and discarded. That proves
*some* service holding the shared internal-token signing key made the call, never *which* one,
because that key is one value shared by every service in the platform
(`platform_core.internal_auth`). Combined with `organization_id` being a caller-supplied query
parameter that `set_tenant` binds the transaction to exactly as named, the previous version of
this module let any holder of that key page any tenant's full directory — this was proved, not
assumed: `tests/test_hierarchy.py::TestInternalHierarchyEndpoint::test_it_is_tenant_bound_not_a_
cross_tenant_listing` names a second organization and gets that organization's rows back, which
is precisely the cross-tenant read the previous docstring here claimed could not happen.

What this version adds, staying inside what one service can enforce without a
`libs/platform-core` change:

- **An `iss` allowlist** (`ALLOWED_INTERNAL_ISSUERS`). The token must name itself as one of the
  services this endpoint is documented to serve. This does **not** stop a holder of the shared
  signing key from lying about `iss` — the same secret signs and verifies, so a service that can
  prove *an* identity can prove *any* identity (`platform_core.internal_auth`'s own docstring is
  explicit about this trade). What it buys: this endpoint no longer answers a service token
  minted for some other purpose by mistake, and forging a working call now requires knowing to
  claim a specific identity rather than only holding the key.
- **An `org` claim, compared when present.** `mint_service_token`'s `claims=` argument already
  lets a caller bind extra facts into a token it mints; if the caller does, the `organization_id`
  asked for in the query string must be that value or the call is refused. This is **not yet
  mandatory** — `services/approval/app/employee_client.py`, the one real caller, does not bind it
  today, and this service cannot change that file or `libs/platform-core` to make binding it
  compulsory. Until it does, a caller that only clears the `iss` check is still trusted for
  whichever `organization_id` it names, same as before. See "What a complete fix still needs"
  below.
- **An audit row per page.** Every successful call now writes one `AuditLog` row, in the same
  transaction as the read, naming the organization read, the page's cursor and row count, and the
  caller's claimed `iss` — see `hierarchy()` for why `record_audit` rather than
  `record_sensitive_read`.

**What a complete fix still needs, precisely, and why it is out of this service's reach:**

1. **Asymmetric or per-service signing keys (or mTLS).** `iss` is only as trustworthy as the
   signature around it, and every service currently signs and verifies with the same HMAC
   secret (`platform_core.internal_auth`, `ServiceSettings.internal_service_token`). The `iss`
   allowlist here is a real narrowing but not a cryptographic guarantee: anything that already
   holds the shared secret can still mint a token claiming `iss="approval"`. The module docstring
   for `platform_core.internal_auth` already names this as the deliberate next step ("per-service
   keypairs or mTLS").
2. **A way to make the `org` claim mandatory without breaking the one real caller.** The
   mechanism (`mint_service_token(..., claims={...})`) already exists in `libs/platform-core` and
   needs no change; what is missing is `services/approval/app/employee_client.py` binding
   `claims={"org": str(organization_id)}` on every call. That file is outside this service's
   ownership boundary. Once it does, this endpoint can flip the `org` claim from "checked when
   present" to "required", closing the gap described in point 2 above for good.
3. **A replay ledger.** `platform_core.internal_auth`'s own docstring already records this as a
   known, deliberate omission: a captured token is valid for its full TTL against this endpoint,
   however many times it is replayed within that window. Closing it needs a `jti` ledger the
   callee can check, which is a `libs/platform-core` change (and a Redis dependency this endpoint
   does not otherwise have).

Guarded by a short-lived service token addressed to this service by name
(`require_service_token`'s underlying check, folded into `_verify_internal_caller` below so the
`iss` and `org` checks can run against the same decoded claims), applied to the whole router via
`dependencies=` so a route added here later cannot forget it. The organization is named in the
query string: this process connects as the NOBYPASSRLS app role, so the read only passes the
tenant policy when the transaction is bound to that organization — RLS still means one call can
never see two organizations' rows at once, which is what "there is no cross-tenant *listing*" is
actually about. It has never meant, and does not mean here, that naming a different organization
in the query string is refused; that is exactly the gap this module now narrows rather than
closes outright.

Mounted at the root, not under `/api/v1`, and `include_in_schema=False` on the whole router:
this is not public API and must not appear in the customer-facing OpenAPI document.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app import selectors
from app.models import AuditLog
from app.schemas import HierarchyNodeOut, HierarchyPage
from platform_core.audit import record_audit
from platform_core.config import ServiceSettings
from platform_core.db import assert_tenant, get_session, set_tenant
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import verify_service_token

#: ADR-0006 §2.1: approval's one-time hierarchy backfill is this endpoint's only legitimate
#: caller today. Narrower than "every service holding the internal-token key" — see the module
#: docstring for exactly what this does and does not defend against.
ALLOWED_INTERNAL_ISSUERS = frozenset({"approval"})

#: The claim a caller binds its authorized organization into, via `mint_service_token`'s
#: `claims=` argument. Compared when present, not yet required — see the module docstring's
#: "What a complete fix still needs".
ORG_CLAIM = "org"

SessionDep = Annotated[AsyncSession, Depends(get_session)]

#: ADR-0006 §2.1: 500 per page, capped at 1000 so a misbehaving caller cannot force an unbounded
#: query even by asking for it directly.
DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 1000


def _verify_internal_caller(request: Request) -> dict[str, Any]:
    """Decode the service token and refuse anything not addressed from an allowed caller.

    Replaces a bare `Depends(require_service_token)`: that dependency verifies signature,
    audience, type and expiry and returns nothing, which is enough to prove a call but not to
    check *who* is calling. This does the same verification directly (`verify_service_token`)
    and additionally refuses a token whose `iss` is not in `ALLOWED_INTERNAL_ISSUERS`, then
    returns the decoded claims so the route can compare the `org` claim against the query
    parameter it names.

    The failure message stays deliberately vague, on purpose the same one `require_service_token`
    uses: distinguishing "wrong issuer" from "expired" from "bad signature" tells whoever is
    probing which half of their guess was right.
    """
    settings: ServiceSettings = request.app.state.settings
    token = request.headers.get("X-Internal-Token")
    if not token:
        raise UnauthorizedProblem("Valid service token required")
    try:
        claims = verify_service_token(settings, token)
    except Exception as exc:  # PyJWTError and anything a malformed token provokes
        raise UnauthorizedProblem("Valid service token required") from exc
    if claims.get("iss") not in ALLOWED_INTERNAL_ISSUERS:
        raise UnauthorizedProblem("Valid service token required")
    return claims


#: `dependencies=` at the router level, not per route, so a route added here later cannot forget
#: it (the original design intent this module has always had). `_verify_internal_caller` is
#: still `Depends`-ed a second time, per route, to capture the decoded claims for the `org`
#: check — FastAPI caches a dependency's result per request, so this does not verify the token
#: twice.
router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    include_in_schema=False,
    dependencies=[Depends(_verify_internal_caller)],
)


@router.get(
    "/hierarchy",
    response_model=HierarchyPage,
    summary="Keyset page of one organization's full hierarchy state",
)
async def hierarchy(
    session: SessionDep,
    request: Request,
    organization_id: Annotated[uuid.UUID, Query()],
    caller_claims: Annotated[dict[str, Any], Depends(_verify_internal_caller)],
    cursor: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> HierarchyPage:
    """Every employee of `organization_id`, in `id` order, the same shape the events carry.

    The transaction is opened and bound to the named organization here, as its first statement,
    because a request driven by a service token carries no principal to take the ordinary
    `get_tenant_session` chokepoint from. Row-level security then does the isolation for what it
    can guarantee: one call never returns two organizations' rows mixed together, whichever
    organization it names. It does **not** mean naming a different organization than the caller
    is entitled to is refused by RLS alone — that is what `_verify_internal_caller`'s `iss`
    allowlist and (when the caller binds one) `org`-claim check are for, and why every page is
    now audited below.
    """
    claimed_org = caller_claims.get(ORG_CLAIM)
    if claimed_org is not None and str(claimed_org) != str(organization_id):
        # Same deliberately vague message as an unverifiable token — see
        # `_verify_internal_caller`'s docstring.
        raise UnauthorizedProblem("Valid service token required")

    settings: ServiceSettings = request.app.state.settings
    async with session.begin():
        await set_tenant(session, organization_id)
        if not settings.is_production:
            await assert_tenant(session, organization_id)
        rows = (
            (
                await session.execute(
                    selectors.hierarchy_stmt(organization_id, cursor=cursor, limit=limit)
                )
            )
            .scalars()
            .all()
        )

        # `record_audit`, not `record_sensitive_read`: the latter is shaped for one unmasked
        # field on one named entity (`entity_id` is required), and this is a bulk page over up
        # to `limit` employees at once, not a single one. `entity_id=None` and the row count,
        # cursor and caller identity in `after` are what make each page traceable to who read
        # it and how much, without echoing any employee's name or number into the audit trail
        # itself. Staged in the same transaction as the read, so a rolled-back page never leaves
        # an audit row claiming it happened.
        record_audit(
            session,
            AuditLog,
            organization_id=organization_id,
            actor_id=None,
            action="employee.hierarchy.exported",
            entity="employee_hierarchy_page",
            after={
                "caller_iss": caller_claims.get("iss"),
                "cursor": str(cursor) if cursor is not None else None,
                "count": len(rows),
                "limit": limit,
            },
            request=request,
        )

    items = [
        HierarchyNodeOut(
            employee_id=row.id,
            reporting_manager_id=row.reporting_manager_id,
            dotted_line_manager_id=row.dotted_line_manager_id,
            status=row.status,
            location_code=row.location_code,
            display_name=row.full_name,
            employee_no=row.employee_no,
            hierarchy_version=row.hierarchy_version,
        )
        for row in rows
    ]
    # A page shorter than `limit` is the last one. A page landing exactly on the boundary asks
    # for one more page, which comes back empty and terminates — see `HierarchyPage`.
    next_cursor = rows[-1].id if len(rows) == limit else None
    return HierarchyPage(items=items, next_cursor=next_cursor)

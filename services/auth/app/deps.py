"""Dependencies wiring the signing key and the directory into routes.

Both are process-wide singletons attached to `app.state` at startup, so a test can replace
either with `dependency_overrides` — the whole sign-in flow is worth exercising without a
second service running or a key on disk.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.config import settings
from app.directory import Directory
from app.entitlements import Entitlements
from app.keys import KeyPair
from app.oidc import IdpClient, StateStore
from platform_core.cache import get_redis, reserve_once
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import (
    ADMIN_HOP_HEADER,
    ADMIN_HOP_NONCE_TTL_SECONDS,
    canonical_query,
    verify_admin_hop,
)


def get_key(request: Request) -> KeyPair:
    return request.app.state.signing_key  # type: ignore[no-any-return]


def get_directory(request: Request) -> Directory:
    return request.app.state.directory  # type: ignore[no-any-return]


def get_entitlements(request: Request) -> Entitlements:
    """What this organization has bought, asked of product-service when a token is minted."""
    return request.app.state.entitlements  # type: ignore[no-any-return]


def get_idp(request: Request) -> IdpClient:
    """The identity-provider client. Overridden in tests by a fake provider."""
    return request.app.state.idp  # type: ignore[no-any-return]


def get_sso_state(request: Request) -> StateStore:
    """Pending SSO authorization requests, in Redis.

    Built per request rather than at startup because it is a thin wrapper over the
    process-wide client, and resolving that lazily keeps the store out of the lifespan's
    ordering constraints. Tests swap it wholesale like every other dependency here.
    """
    return StateStore(get_redis())


async def require_admin_hop(request: Request) -> None:
    """BLOCKER 2 of the 2026-09-24 security review: refuse an operator request that did not
    arrive with a fresh, correctly signed admin-gateway hop signature.

    Defence in depth on top of every gateway's route table refusing `/api/v1/auth/operator`
    outright (`Route.blocked` — see `platform_core.gateway`) — for the request that reaches
    this service some other way: a routing bug, a compromised sibling service replaying a
    captured call, a gateway that was never supposed to carry this prefix at all.

    The signature binds the method, path, canonical query and body digest, and carries a
    nonce this route reserves in Redis (`SET NX EX`, m2 of the third-round review): a captured
    header is refused when replayed with a different body or query, and refused when replayed
    verbatim. **Fails closed** — a Redis outage answers 503 rather than letting an unchecked
    replay through.

    A no-op when `admin_gateway_hop_key` is unset, which `Settings` only permits outside
    production/staging/uat (M1(a)): in a real deployment auth refuses to start without it, so
    "off" can only ever mean a development box that has not provisioned one.
    """
    cfg = settings()
    if cfg.admin_gateway_hop_key is None:
        return
    nonce = verify_admin_hop(
        request.headers.get(ADMIN_HOP_HEADER),
        request.method,
        request.url.path,
        key=cfg.admin_gateway_hop_key.get_secret_value(),
        query=canonical_query(request.query_params.multi_items()),
        body=await request.body(),
    )
    if nonce is None:
        raise UnauthorizedProblem("This request did not arrive through the operator gateway.")
    if not await reserve_once(f"admin-hop-nonce:{nonce}", ttl=ADMIN_HOP_NONCE_TTL_SECONDS):
        raise UnauthorizedProblem("This request did not arrive through the operator gateway.")


KeyDep = Annotated[KeyPair, Depends(get_key)]
DirectoryDep = Annotated[Directory, Depends(get_directory)]
EntitlementsDep = Annotated[Entitlements, Depends(get_entitlements)]
IdpDep = Annotated[IdpClient, Depends(get_idp)]
SsoStateDep = Annotated[StateStore, Depends(get_sso_state)]

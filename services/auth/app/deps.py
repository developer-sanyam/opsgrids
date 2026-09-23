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
from platform_core.cache import get_redis
from platform_core.errors import UnauthorizedProblem
from platform_core.internal_auth import ADMIN_HOP_HEADER, verify_admin_hop


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


def require_admin_hop(request: Request) -> None:
    """BLOCKER 2 of the 2026-09-24 security review: refuse an operator request that did not
    arrive with a fresh, correctly signed admin-gateway hop signature.

    Defence in depth on top of every gateway's route table refusing `/api/v1/auth/operator`
    outright (`Route.blocked` — see `platform_core.gateway`) — for the request that reaches
    this service some other way: a routing bug, a compromised sibling service replaying a
    captured call, a gateway that was never supposed to carry this prefix at all. A no-op when
    this deployment has not set `ADMIN_GATEWAY_HOP_KEY`: the signature is a belt this
    deployment chooses to wear, never the trousers, so an unconfigured key means "this defence
    is off", not a sign-in outage everywhere it has not yet been provisioned.
    """
    cfg = settings()
    if cfg.admin_gateway_hop_key is None:
        return
    if not verify_admin_hop(
        request.headers.get(ADMIN_HOP_HEADER),
        request.method,
        request.url.path,
        key=cfg.admin_gateway_hop_key.get_secret_value(),
    ):
        raise UnauthorizedProblem("This request did not arrive through the operator gateway.")


KeyDep = Annotated[KeyPair, Depends(get_key)]
DirectoryDep = Annotated[Directory, Depends(get_directory)]
EntitlementsDep = Annotated[Entitlements, Depends(get_entitlements)]
IdpDep = Annotated[IdpClient, Depends(get_idp)]
SsoStateDep = Annotated[StateStore, Depends(get_sso_state)]

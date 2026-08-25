"""Dependencies wiring the signing key and the directory into routes.

Both are process-wide singletons attached to `app.state` at startup, so a test can replace
either with `dependency_overrides` — the whole sign-in flow is worth exercising without a
second service running or a key on disk.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.directory import Directory
from app.entitlements import Entitlements
from app.keys import KeyPair
from app.oidc import IdpClient, StateStore
from platform_core.cache import get_redis


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


KeyDep = Annotated[KeyPair, Depends(get_key)]
DirectoryDep = Annotated[Directory, Depends(get_directory)]
EntitlementsDep = Annotated[Entitlements, Depends(get_entitlements)]
IdpDep = Annotated[IdpClient, Depends(get_idp)]
SsoStateDep = Annotated[StateStore, Depends(get_sso_state)]

"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.

An operations caller's `subject_ref` is their directory id: OWN scope reads the purchase orders
whose `raised_by_id` is that id. There is no per-service "employee" object here, so the helpers
take a bare `subject_ref` uuid.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator

from platform_core.auth import Principal, Scope, ScopeContext, get_principal


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    subject_ref: uuid.UUID | None = None,
    entitlements: list[str] | None = None,
    locations: list[str] | None = None,
    amr: list[str] | None = None,
    sub: uuid.UUID | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for an operations caller. `sub` is a fresh
    user unless given — pass one to be the same person across requests (idempotency keys replay
    only for the user who made them)."""
    return Principal(
        sub=sub or uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["operations"],
        locations=locations or [],
        amr=amr or [],
    )


def ctx_for(
    org_id: uuid.UUID,
    permission: str,
    scope: Scope,
    *,
    subject_ref: uuid.UUID | None = None,
    locations: list[str] | None = None,
    sub: uuid.UUID | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id,
        [f"{permission}:{scope.value}"],
        subject_ref=subject_ref,
        locations=locations,
        sub=sub,
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)


def same_caller(org_id: uuid.UUID, perms: list[str]) -> uuid.UUID:
    """One stable user per (organization, permission set), for helpers that retry as a person."""
    return uuid.uuid5(org_id, ",".join(sorted(perms)))


@contextlib.contextmanager
def as_principal(principal: Principal) -> Iterator[None]:
    """Override `get_principal` for the app under test so `client` calls act as `principal`."""
    from app.main import app

    app.dependency_overrides[get_principal] = lambda: principal
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_principal, None)

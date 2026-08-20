"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import uuid

from app.models import Membership
from platform_core.auth import Principal, Scope, ScopeContext


def principal_for(
    org_id: uuid.UUID,
    membership: Membership,
    perms: list[str],
    *,
    locations: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for this member."""
    return Principal(
        sub=membership.user_id,
        organization_id=org_id,
        membership_id=membership.id,
        subject_ref=membership.id,
        perms=perms,
        locations=locations or [],
    )


def ctx_for(
    org_id: uuid.UUID,
    membership: Membership,
    permission: str,
    scope: Scope,
    *,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id, membership, [f"{permission}:{scope.value}"], locations=locations
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)

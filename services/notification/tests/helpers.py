"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so importing
*from* it is fragile — this module is a plain import that works the same way everywhere.
"""

from __future__ import annotations

import uuid
from typing import Any

from platform_core.auth import Principal, Scope, ScopeContext


def principal_for(
    org_id: uuid.UUID,
    perms: list[str],
    *,
    entitlements: list[str] | None = None,
    amr: list[str] | None = None,
    subject_ref: uuid.UUID | None = None,
    membership_id: uuid.UUID | None = None,
) -> Principal:
    """The principal the gateway would have forwarded for a notification caller."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        subject_ref=subject_ref,
        membership_id=membership_id,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["notification"],
        amr=amr or [],
    )


def ctx_for(org_id: uuid.UUID, permission: str, scope: Scope) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


def envelope_for(
    org_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    *,
    event_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """One event as the relay would have put it on the wire.

    Only the fields the consumer actually reads are filled. Writing the whole envelope here
    would make the test assert the relay's shape rather than the consumer's behaviour.
    """
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "organization_id": str(org_id),
        "payload": payload,
    }

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
) -> Principal:
    """The principal the gateway would have forwarded for a notification caller."""
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        subject_ref=subject_ref,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["notification"],
        amr=amr or [],
    )


def ctx_for(org_id: uuid.UUID, permission: str, scope: Scope) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


class CapturingLog:
    """A drop-in for a module's `log` that remembers every call, so a test can assert on what a
    structlog call would otherwise only print — and, for the PII rules, on what it must not."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str, **fields: Any) -> None:
        self.calls.append((event, fields))

    debug = info = warning = error = exception = critical = _record

    def text(self) -> str:
        """Every event name and field value, as one string to search."""
        return " ".join(f"{event} {fields}" for event, fields in self.calls)


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

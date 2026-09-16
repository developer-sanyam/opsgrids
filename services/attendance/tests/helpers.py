"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from platform_core.auth import Principal, Scope, ScopeContext


class FakeRedis:
    """Enough Redis for the app factory's `redis=True` readiness check.

    This service does not bump a permission version, but the factory opens and pings Redis for
    any service that declares it, so the tests need a stand-in rather than an absent client.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def principal_for(
    org_id: uuid.UUID,
    subject_ref: uuid.UUID | None,
    perms: list[str],
    *,
    entitlements: list[str] | None = None,
    locations: list[str] | None = None,
    amr: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded.

    `subject_ref` is the caller's own employee id — the HRMS product's subject reference — which
    is what OWN scope resolves against, and which a self-service punch attributes the record to.
    A `None` subject_ref is an authenticated caller who is not an employee of the organization.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=None,
        subject_ref=subject_ref,
        perms=perms,
        entitlements=entitlements if entitlements is not None else ["hrms"],
        locations=locations or [],
        amr=amr or [],
    )


def ctx_for(
    org_id: uuid.UUID,
    subject_ref: uuid.UUID | None,
    permission: str,
    scope: Scope,
    *,
    locations: list[str] | None = None,
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(
        org_id, subject_ref, [f"{permission}:{scope.value}"], locations=locations
    )
    return ScopeContext(principal=principal, scope=scope, permission=permission)


#: A sentinel distinct from `None` — `approval_opened_envelope`/`approval_decided_envelope`
#: default `approver_name`/`decided_by_name` to this so the ordinary case (a caller who does not
#: pass the parameter at all) builds a payload with the key **absent**, exactly the shape a
#: pre-upgrade approval producer or a replay from before its change landed publishes. Passing
#: `approver_name=None`/`decided_by_name=None` explicitly instead builds a payload with the key
#: **present and null** — the "unassigned step" / "decider outside the hierarchy copy" shape —
#: and passing an actual string builds the ordinary populated case. All three are distinct
#: payload shapes `app.consumers._display_name` must treat alike (`None` either way, never
#: raised on); the tests that care which one they built assert against that function directly.
_UNSET: Any = object()


def approval_opened_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    request_id: uuid.UUID | None = None,
    source_ref: uuid.UUID,
    requester_employee_id: uuid.UUID | None = None,
    approver_employee_id: uuid.UUID | None = None,
    approver_name: str | None = _UNSET,
    acting_for_employee_id: uuid.UUID | None = None,
    request_type: str = "hrms.time.regularization",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """An envelope shaped the way `app/consumers.on_approval_request_opened` expects, per the
    `payload={...}` dict `services/approval/app/services.py`'s `open_regularization_request`
    publishes as `core.approval.request.opened` — no `opened_at` field (the envelope's own
    `occurred_at` is what stands in for it there). `approver_name` defaults to omitting the key
    from the built payload entirely — see `_UNSET`'s own docstring for the three distinct shapes
    a caller can ask for.
    """
    payload = {
        "organization_id": str(organization_id),
        "request_id": str(request_id or uuid.uuid4()),
        "request_type": request_type,
        "source_ref": str(source_ref),
        "requester_employee_id": str(requester_employee_id or uuid.uuid4()),
        "approver_employee_id": (str(approver_employee_id) if approver_employee_id else None),
        "acting_for_employee_id": (str(acting_for_employee_id) if acting_for_employee_id else None),
        "due_at": None,
    }
    if approver_name is not _UNSET:
        payload["approver_name"] = approver_name
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "core.approval.request.opened",
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "approval_request",
        "aggregate_id": payload["request_id"],
        "occurred_at": occurred_at or datetime.now(UTC).isoformat(),
        "producer": "approval",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": "opsgrids://contracts/core.approval.request.opened/v1",
        "payload": payload,
    }


def approval_decided_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    request_id: uuid.UUID | None = None,
    source_ref: uuid.UUID,
    outcome: str = "approved",
    request_type: str = "hrms.time.regularization",
    decided_by_employee_id: uuid.UUID | None = None,
    decided_by_name: str | None = _UNSET,
    acting_for_employee_id: uuid.UUID | None = None,
    comment: str | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """An envelope shaped the way `app/consumers.on_approval_request_decided` expects, per
    ADR-0006 §12: `core.approval.request.decided`, filtered in the handler on `request_type`.
    `decided_by_name` defaults to omitting the key from the built payload entirely — the same
    shape `approval_opened_envelope` uses for `approver_name`; see `_UNSET`'s own docstring.
    """
    payload = {
        "organization_id": str(organization_id),
        "request_id": str(request_id or uuid.uuid4()),
        "request_type": request_type,
        "source_ref": str(source_ref),
        "outcome": outcome,
        "decided_by_employee_id": str(decided_by_employee_id) if decided_by_employee_id else None,
        "acting_for_employee_id": (str(acting_for_employee_id) if acting_for_employee_id else None),
        "comment": comment,
        "decided_at": decided_at or datetime.now(UTC).isoformat(),
    }
    if decided_by_name is not _UNSET:
        payload["decided_by_name"] = decided_by_name
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": "core.approval.request.decided",
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "approval_request",
        "aggregate_id": payload["request_id"],
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "approval",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": "opsgrids://contracts/core.approval.request.decided/v1",
        "payload": payload,
    }

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
    """Enough Redis for the app factory's `redis=True` readiness check."""

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
    """The principal the gateway would have forwarded. `subject_ref` is the caller's own
    employee id — the HRMS product's subject reference."""
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
    request_type: str = "hrms.time.leave",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """An envelope shaped the way `app/consumers.on_approval_request_opened` expects — the same
    shape `attendance/tests/helpers.py::approval_opened_envelope` builds, retargeted at leave's
    own request type."""
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
    request_type: str = "hrms.time.leave",
    decided_by_employee_id: uuid.UUID | None = None,
    decided_by_name: str | None = _UNSET,
    acting_for_employee_id: uuid.UUID | None = None,
    comment: str | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """An envelope shaped the way `app/consumers.on_approval_request_decided` expects."""
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


def employee_hierarchy_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None = None,
    status: str = "active",
    hierarchy_version: int | None = 1,
    event_type: str = "hrms.people.employee.onboarded",
) -> dict[str, Any]:
    """An envelope shaped like employee-service's real full-state hierarchy payload (ADR-0006
    §2) — the same shape `app/consumers._directory_state` parses. `hierarchy_version=None`
    builds the "un-upgraded pod" shape the module docstring describes: the key is simply absent."""
    payload: dict[str, Any] = {
        "organization_id": str(organization_id),
        "employee_id": str(employee_id),
        "reporting_manager_id": str(reporting_manager_id) if reporting_manager_id else None,
        "status": status,
    }
    if hierarchy_version is not None:
        payload["hierarchy_version"] = hierarchy_version
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "organization_id": str(organization_id),
        "aggregate_type": "employee",
        "aggregate_id": str(employee_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "producer": "employee",
        "correlation_id": None,
        "causation_id": None,
        "actor": None,
        "schema_uri": f"opsgrids://contracts/{event_type}/v1",
        "payload": payload,
    }

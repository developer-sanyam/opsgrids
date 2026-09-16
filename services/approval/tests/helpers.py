"""Test helpers shared between conftest and the test modules.

Kept out of `conftest.py` because conftest is loaded by pytest rather than imported, so
importing *from* it is fragile — this module is a plain import that works the same way
everywhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from platform_core.auth import Principal, Scope, ScopeContext

READ = "core.approval.request.read"


def principal_for(
    org_id: uuid.UUID,
    subject_ref: uuid.UUID | None,
    perms: list[str],
    *,
    locations: list[str] | None = None,
) -> Principal:
    """The principal the gateway would have forwarded.

    `subject_ref` is the caller's own employee id — what OWN scope resolves against. A `None`
    subject_ref is an authenticated caller with no employee link, who (per ADR-0006 §6) can
    decide nothing and, here, owns no chain either. `locations` is what a LOCATION-scoped grant
    compares against (`ScopeContext.assert_can_touch`) — empty by default, the same "nothing to
    match" shape a caller with no assigned location actually has.
    """
    return Principal(
        sub=uuid.uuid4(),
        organization_id=org_id,
        membership_id=uuid.uuid4(),
        subject_ref=subject_ref,
        perms=perms,
        entitlements=["core"],
        locations=locations or [],
    )


def ctx_for(
    org_id: uuid.UUID, subject_ref: uuid.UUID | None, permission: str, scope: Scope
) -> ScopeContext:
    """A scope context as `require(...)` would have built it."""
    principal = principal_for(org_id, subject_ref, [f"{permission}:{scope.value}"])
    return ScopeContext(principal=principal, scope=scope, permission=permission)


def employee_onboarded_envelope(
    *,
    event_id: uuid.UUID | None = None,
    organization_id: uuid.UUID,
    employee_id: uuid.UUID,
    reporting_manager_id: uuid.UUID | None = None,
    dotted_line_manager_id: uuid.UUID | None = None,
    status: str = "active",
    display_name: str = "Ananya Rao",
    employee_no: str = "EMP0001",
    location_code: str | None = "MUM",
    hierarchy_version: int | None = 1,
    event_type: str = "hrms.people.employee.onboarded",
) -> dict:
    """An envelope shaped the way `app/consumers.py` expects, per ADR-0006 §2 / §12.

    `hierarchy_version=None` omits the field entirely (an un-upgraded employee pod), matching
    the payload shape `services/employee/app/services.py` publishes today.
    """
    payload = {
        "organization_id": str(organization_id),
        "employee_id": str(employee_id),
        "reporting_manager_id": str(reporting_manager_id) if reporting_manager_id else None,
        "dotted_line_manager_id": (str(dotted_line_manager_id) if dotted_line_manager_id else None),
        "status": status,
        "display_name": display_name,
        "employee_no": employee_no,
        "location_code": location_code,
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

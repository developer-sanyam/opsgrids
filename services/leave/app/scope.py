"""How the `hrms` product turns a scope rung into a filter over leave rows.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal; what each rung means is per
product (ADR-0000 §6). This is a third `hrms` process, alongside employee- and
attendance-service, and it answers scope for the rows it serves:

    OWN       -> LeaveRequest.employee_id           (the caller's own employee id is subject_ref)
    TEAM      -> LeaveRequest.reporting_manager_id   (a snapshot at apply time, see app/models.py)
    LOCATION  -> deferred (see below)

**LOCATION is deliberately not supported yet.** A leave request carries no location of its own —
unlike `attendance.AttendanceRecord`, which is attributed to the punch's location, a leave
request has nothing but the employee's own location, and that fact lives in employee-service,
not here (the same gap `attendance.scope`'s TEAM deferral describes, mirrored onto the other
rung). `location_column()` is `None`, and `ctx.apply`/`ctx.assert_can_touch` already raise the
standard `ForbiddenProblem` for a LOCATION-scoped caller — no default role template grants this
scope in this slice regardless (`app/catalogue.py`).
"""

from __future__ import annotations

from typing import Any

from app.models import LeaveRequest
from platform_core.auth import register_scope_resolver

PRODUCT = "hrms"


class LeaveScopeResolver:
    """The `ScopeResolver` for HRMS leave-request rows."""

    def owner_column(self) -> Any:
        return LeaveRequest.employee_id

    def team_column(self) -> Any | None:
        return LeaveRequest.reporting_manager_id

    def location_column(self) -> Any | None:
        return None


def register() -> None:
    """Register the resolver for the `hrms` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, LeaveScopeResolver)

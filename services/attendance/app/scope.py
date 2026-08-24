"""How the `hrms` product turns a scope rung into a filter over attendance rows.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product — that indirection is ADR-0000 §6. This is a separate process from
employee-service, so registering the `hrms` resolver here is independent and correct: each
process answers scope for the rows it serves. For attendance the mapping is:

    OWN       -> AttendanceRecord.employee_id   (the caller's own employee id is subject_ref)
    LOCATION  -> AttendanceRecord.location_code (compared against the caller's location codes)
    TEAM      -> deferred (see below)

**TEAM is deliberately not supported yet.** A manager seeing their team's attendance needs the
reporting hierarchy, which lives in employee-service (`employees.reporting_manager_id`) — it is
not a fact this service holds. It arrives here later as an employee-events projection; until
then `team_column()` is `None`, and `ctx.apply`/`ctx.assert_can_touch` already raise the
standard `ForbiddenProblem` for a TEAM-scoped caller against a resource with no team column.
"""

from __future__ import annotations

from typing import Any

from app.models import AttendanceRecord
from platform_core.auth import register_scope_resolver

PRODUCT = "hrms"


class AttendanceScopeResolver:
    """The `ScopeResolver` for HRMS attendance rows."""

    def owner_column(self) -> Any:
        return AttendanceRecord.employee_id

    def team_column(self) -> Any | None:
        # Deferred: the reporting hierarchy lives in employee-service, not here. A TEAM-scoped
        # caller is refused until the employee-events projection lands (see the module docstring).
        return None

    def location_column(self) -> Any | None:
        return AttendanceRecord.location_code


def register() -> None:
    """Register the resolver for the `hrms` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, AttendanceScopeResolver)

"""How the `hrms` product turns a scope rung into a filter over attendance rows.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product — that indirection is ADR-0000 §6. This is a separate process from
employee-service, so registering the `hrms` resolver here is independent and correct: each
process answers scope for the rows it serves. For attendance the mapping is:

    OWN       -> AttendanceRecord.employee_id        (the caller's own employee id is subject_ref)
    TEAM      -> TeamMembership.manager_employee_id  (the caller's current direct reports)
    LOCATION  -> AttendanceRecord.location_code      (the caller's location codes)

**TEAM lives on a projection, because the reporting line is not this service's fact.** A manager's
reports are `employees.reporting_manager_id` in employee-service; attendance may not read that
schema and does not call it on the read path. It keeps `team_membership` instead — one row per
employee, the manager they report to now — fed by the `hrms.people.employee.*` events and by a
one-shot backfill (`app/consumers.py`, `app/backfill.py`). `selectors.attendance_stmt` joins it for
a TEAM-scoped caller and hands `ctx.apply` the manager column, so the rule "a caller with no
employee id matches nothing" stays the platform's. Direct reports only, matching employee-service;
`exited` employees are not in anyone's team; see `models.TeamMembership` for why both.

**TEAM does not include the manager's own records.** `ScopeContext.apply` reads TEAM as "rows whose
manager is me" and employee-service's own TEAM read does the same, so attendance follows it rather
than quietly widening one rung of the ladder in one service. A person who holds `attendance.read`
at TEAM and needs their own day reads it through an OWN grant or an ORG one.
"""

from __future__ import annotations

from typing import Any

from app.models import AttendanceRecord, TeamMembership
from platform_core.auth import register_scope_resolver

PRODUCT = "hrms"


class AttendanceScopeResolver:
    """The `ScopeResolver` for HRMS attendance rows."""

    def owner_column(self) -> Any:
        return AttendanceRecord.employee_id

    def team_column(self) -> Any | None:
        # Not a column of the record: the selector joins `team_membership` and passes this
        # column to `ctx.apply` (see the module docstring).
        return TeamMembership.manager_employee_id

    def location_column(self) -> Any | None:
        return AttendanceRecord.location_code


def register() -> None:
    """Register the resolver for the `hrms` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, AttendanceScopeResolver)

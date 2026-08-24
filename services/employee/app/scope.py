"""How the `hrms` product turns a scope rung into a query filter.

The scope ladder (`OWN < TEAM < LOCATION < ORG`) is universal, but what each rung *means* is
per product — that indirection is ADR-0000 §6, and it exists so HR's reading of TEAM and
LOCATION does not become every product's. For HRMS the mapping is:

    OWN       -> Employee.id                    (the caller's own employee id is subject_ref)
    TEAM      -> Employee.reporting_manager_id  (a manager's reports carry their id here)
    LOCATION  -> Employee.location_code         (compared against the caller's location codes)

The token's `subject_ref` for an HRMS caller is their own employee id, which is why OWN and
TEAM both resolve against it: OWN matches the row whose id is the caller's, TEAM matches the
rows whose manager id is the caller's.
"""

from __future__ import annotations

from typing import Any

from app.models import Employee
from platform_core.auth import register_scope_resolver

PRODUCT = "hrms"


class EmployeeScopeResolver:
    """The `ScopeResolver` for HRMS employee rows."""

    def owner_column(self) -> Any:
        return Employee.id

    def team_column(self) -> Any | None:
        return Employee.reporting_manager_id

    def location_column(self) -> Any | None:
        return Employee.location_code


def register() -> None:
    """Register the resolver for the `hrms` product. Idempotent — safe to call at import."""
    register_scope_resolver(PRODUCT, EmployeeScopeResolver)

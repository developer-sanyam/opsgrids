"""The HRMS time-and-attendance permission catalogue and role templates.

This service is a second process in the same `hrms` product as employee-service, and it
declares its own slice of that product's vocabulary — the `hrms.time.*` permissions — rather
than importing employee-service's catalogue across a service boundary the platform exists to
keep clean. Registering the `hrms` product here is independent of, and consistent with,
employee-service registering it there: each process owns the catalogue for the permissions it
serves.

The structure is copied from employee-service, and for the same reason it is shaped the way it
is: in the system this platform replaces, the HR admin role was "every permission that exists",
so the moment another product registered its permissions the HR admin silently held them too.
Here that is impossible rather than merely discouraged — a `RoleTemplate` names exactly one
product, and `validate()` refuses at import time (asserted by a test) if a template holds a
foreign product's permission without declaring `cross_product`.
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "hrms"


# --------------------------------------------------------------------- hrms time catalogue

HRMS = PermissionCatalogue(
    PRODUCT,
    [
        # A punch has a meaningful "own" reading — everyone reads and writes their own
        # attendance — so both default to OWN and top out at ORG for an administrator who reads
        # or corrects the whole organization's.
        PermissionSpec("time", "attendance", "read", "See attendance records", "OWN", "ORG"),
        PermissionSpec(
            "time", "attendance", "write", "Punch, and correct, attendance records", "OWN", "ORG"
        ),
        # ADR-0006 §11: the self-service regularisation request. `max_scope` is OWN, not ORG —
        # there is no meaningful "request a correction on behalf of everyone". An administrator
        # who wants to change someone else's day uses the existing direct override
        # (`attendance.write` at `minimum=ORG`), which stays the audited escape hatch. Capping
        # the ceiling at OWN makes an ORG-wide request-for-others power unrepresentable rather
        # than merely ungranted.
        PermissionSpec(
            "time",
            "attendance",
            "request",
            "Ask for a correction to your own attendance",
            "OWN",
            "OWN",
        ),
        # A shift is an organization-wide definition, not a personal one: everyone reads them,
        # only an administrator writes them. So both default to ORG — there is no "own shift".
        PermissionSpec("time", "shift", "read", "See shift definitions", "ORG", "ORG"),
        PermissionSpec(
            "time", "shift", "write", "Define shifts and field geo-fences", "ORG", "ORG"
        ),
    ],
)


HRMS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="HR_ADMIN",
        name="HR administrator",
        product=PRODUCT,
        description=(
            "Administers time and attendance across the organization: reads and corrects every "
            "employee's attendance, and defines the organization's shifts and field geo-fences. "
            "Deliberately built from the HRMS catalogue alone."
        ),
        grants=HRMS.pick(
            ("hrms.time.attendance.read", "ORG"),
            ("hrms.time.attendance.write", "ORG"),
            # An HR administrator is also an employee with their own attendance (ADR-0006 §11),
            # so they hold the self-service request permission too — at OWN, its ceiling.
            ("hrms.time.attendance.request", "OWN"),
            ("hrms.time.shift.read", "ORG"),
            ("hrms.time.shift.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="EMPLOYEE",
        name="Employee",
        product=PRODUCT,
        description=(
            "Every person with an HRMS seat. Punches and reads their own attendance, asks for "
            "corrections to it, and sees the organization's shifts."
        ),
        is_default=True,
        grants=HRMS.pick(
            # A person punches and reads their own attendance — OWN, so it reaches nobody else's.
            ("hrms.time.attendance.read", "OWN"),
            ("hrms.time.attendance.write", "OWN"),
            # Everyone raises requests against their own record (ADR-0006 §11).
            ("hrms.time.attendance.request", "OWN"),
            # Shifts are org definitions everyone needs to see, so shift.read is granted at ORG.
            ("hrms.time.shift.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(HRMS_ROLE_TEMPLATES)


HRMS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=HRMS, role_templates=HRMS_ROLE_TEMPLATES
)

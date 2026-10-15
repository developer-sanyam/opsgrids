"""The HRMS permission catalogue and role templates, for the `hrms` product.

Mirrors account-service's catalogue, and for the same reason it is shaped the way it is: in
the system this platform replaces, the HR admin role was "every permission that exists", so
the moment another product registered its permissions the HR admin silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit.

The catalogue construct is copied here rather than imported from account-service on purpose:
a product declares its own vocabulary, and importing account's `catalogue` module would couple
this service to another service's code across a boundary the platform exists to keep clean.
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


# --------------------------------------------------------------------- hrms catalogue

HRMS = PermissionCatalogue(
    PRODUCT,
    [
        # Reading an employment record has a meaningful "own" reading — everyone can see their
        # own — so it defaults to OWN and may widen to ORG. Writing has no self-service reading
        # in this slice (people do not edit their own employment record), so the routes set an
        # ORG floor; the permission itself still tops out at ORG.
        PermissionSpec("people", "employee", "read", "See employee records", "OWN", "ORG"),
        PermissionSpec(
            "people",
            "employee",
            "write",
            "Create, edit and transition employee records",
            "OWN",
            "ORG",
        ),
        # Unmasking sensitive PII is a distinct permission, never folded into `read`: the wildcard
        # match keeps the dot, so `hrms.people.employee.read` can never satisfy
        # `...read_sensitive`. `is_dangerous` marks it as such; the route needs the permission
        # and nothing more (no step-up since 2026-10-15, ADR-0023 addendum). It defaults to OWN
        # so a person can see their own full details, and tops out at ORG for an HR administrator.
        PermissionSpec(
            "people",
            "employee",
            "read_sensitive",
            "Unmask an employee's sensitive PII (bank, government identifiers, contact)",
            "OWN",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


HRMS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="HR_ADMIN",
        name="HR administrator",
        product=PRODUCT,
        description=(
            "Administers people data across the organization: reads every employee and creates, "
            "edits and transitions their records. Deliberately built from the HRMS catalogue "
            "alone — administering HR is not the same job as owning the account."
        ),
        grants=HRMS.pick(
            ("hrms.people.employee.read", "ORG"),
            ("hrms.people.employee.write", "ORG"),
            # Administering people includes unmasking their details across the organization — but
            # only ever as a permission-gated, audited, budgeted reveal.
            ("hrms.people.employee.read_sensitive", "ORG"),
        ),
    ),
    RoleTemplate(
        code="EMPLOYEE",
        name="Employee",
        product=PRODUCT,
        description="Every person with an HRMS seat. Sees their own employment record.",
        is_default=True,
        grants=HRMS.pick(
            ("hrms.people.employee.read", "OWN"),
            # A person may unmask their *own* full details — at OWN, so it reaches nobody else's.
            ("hrms.people.employee.read_sensitive", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(HRMS_ROLE_TEMPLATES)


HRMS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=HRMS, role_templates=HRMS_ROLE_TEMPLATES
)

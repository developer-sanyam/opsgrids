"""The timesheet permission catalogue and role templates, for the `pulseboard` product.

Mirrors account-, employee- and projects-service's catalogue, and for the same reason it is
shaped the way it is: in the system this platform replaces, an admin role was "every permission
that exists", so the moment another product registered its permissions the admin silently held
them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's own
catalogue, never "all permissions" (CLAUDE.md).

PulseBoard spans three services (project, timesheet, standup); each declares the slice of the
`pulseboard` vocabulary it owns, exactly as the HRMS services each declare a slice of `hrms`.
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "pulseboard"


# --------------------------------------------------------------------- timesheet catalogue

PULSEBOARD_TIME = PermissionCatalogue(
    PRODUCT,
    [
        # The weekly grid. Reading has a meaningful OWN reading (my own weeks), so it defaults
        # to OWN and tops out at ORG — the whole-team grid view needs an ORG grant.
        PermissionSpec("time", "timesheet", "read", "See timesheets and the weekly grid", "OWN"),
        # Recording a week has an OWN reading too (I record my own time); an ORG grant records
        # on someone's behalf.
        PermissionSpec("time", "timesheet", "write", "Record and update timesheet weeks", "OWN"),
    ],
)


PULSEBOARD_TIME_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="PULSEBOARD_ADMIN",
        name="Delivery administrator",
        product=PRODUCT,
        description=(
            "Administers the team's time across the organization: sees the whole grid and can "
            "record or correct any week. Built from the pulseboard catalogue alone."
        ),
        grants=PULSEBOARD_TIME.all("ORG"),
    ),
    RoleTemplate(
        code="PULSEBOARD_MEMBER",
        name="Delivery-team member",
        product=PRODUCT,
        description=(
            "Every person with a PulseBoard seat. Sees the team's weekly grid and records "
            "their own time."
        ),
        is_default=True,
        grants=PULSEBOARD_TIME.pick(
            ("pulseboard.time.timesheet.read", "ORG"),
            ("pulseboard.time.timesheet.write", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(PULSEBOARD_TIME_ROLE_TEMPLATES)


PULSEBOARD_TIME_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=PULSEBOARD_TIME, role_templates=PULSEBOARD_TIME_ROLE_TEMPLATES
)

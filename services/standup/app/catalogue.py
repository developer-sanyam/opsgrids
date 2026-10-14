"""The standup permission catalogue and role templates, for the `pulseboard` product.

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


# --------------------------------------------------------------------- standup catalogue

PULSEBOARD_STANDUP = PermissionCatalogue(
    PRODUCT,
    [
        # The daily check-ins. Reading has a meaningful OWN reading (my own entries), so it
        # defaults to OWN and tops out at ORG — the team wall view needs an ORG grant.
        PermissionSpec("standup", "entry", "read", "See the team's daily check-ins", "OWN"),
        # Posting has an OWN reading (I post my own standup); an ORG grant posts on behalf.
        PermissionSpec("standup", "entry", "write", "Post and update daily check-ins", "OWN"),
    ],
)


PULSEBOARD_STANDUP_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="PULSEBOARD_ADMIN",
        name="Delivery administrator",
        product=PRODUCT,
        description=(
            "Administers the daily check-ins across the organization. Built from the "
            "pulseboard catalogue alone."
        ),
        grants=PULSEBOARD_STANDUP.all("ORG"),
    ),
    RoleTemplate(
        code="PULSEBOARD_MEMBER",
        name="Delivery-team member",
        product=PRODUCT,
        description=(
            "Every person with a PulseBoard seat. Reads the team's wall and posts their own "
            "check-in."
        ),
        is_default=True,
        grants=PULSEBOARD_STANDUP.pick(
            ("pulseboard.standup.entry.read", "ORG"),
            ("pulseboard.standup.entry.write", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(PULSEBOARD_STANDUP_ROLE_TEMPLATES)


PULSEBOARD_STANDUP_REGISTRATION = ProductRegistration(
    product=PRODUCT,
    catalogue=PULSEBOARD_STANDUP,
    role_templates=PULSEBOARD_STANDUP_ROLE_TEMPLATES,
)

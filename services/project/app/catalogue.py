"""The engagements-and-board permission catalogue and role templates, for the `pulseboard`
product.

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


# --------------------------------------------------------------------- pulseboard catalogue

PULSEBOARD = PermissionCatalogue(
    PRODUCT,
    [
        # The engagement portfolio. Reading has a meaningful OWN reading (the engagements I
        # own), so it defaults to OWN and tops out at ORG.
        PermissionSpec(
            "engagements", "project", "read", "See engagements and the portfolio", "OWN"
        ),
        PermissionSpec("engagements", "project", "write", "Create and edit engagements", "OWN"),
        # The board.
        PermissionSpec("board", "task", "read", "See the board", "OWN"),
        PermissionSpec("board", "task", "write", "Create and move board cards", "OWN"),
        # The delivery-team roster. A roster has no meaningful OWN slice — the whole team is
        # what the screens join names and capacities from — so reading floors at ORG.
        PermissionSpec("team", "person", "read", "See the delivery-team roster", "ORG"),
        # Same reasoning for writing it: adding or editing a roster member is an admin act,
        # not something the OWN rung has a meaning for.
        PermissionSpec("team", "person", "write", "Add and edit roster people", "ORG"),
    ],
)


PULSEBOARD_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="PULSEBOARD_ADMIN",
        name="Delivery administrator",
        product=PRODUCT,
        description=(
            "Administers engagements and the board across the organization. Built from the "
            "pulseboard catalogue alone — running delivery is not the same job as owning the "
            "account."
        ),
        grants=PULSEBOARD.all("ORG"),
    ),
    RoleTemplate(
        code="PULSEBOARD_MEMBER",
        name="Delivery-team member",
        product=PRODUCT,
        description=(
            "Every person with a PulseBoard seat. Reads the portfolio, the board and the "
            "roster, and works their own board cards."
        ),
        is_default=True,
        grants=PULSEBOARD.pick(
            ("pulseboard.engagements.project.read", "ORG"),
            ("pulseboard.board.task.read", "ORG"),
            ("pulseboard.board.task.write", "OWN"),
            ("pulseboard.team.person.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(PULSEBOARD_ROLE_TEMPLATES)


PULSEBOARD_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=PULSEBOARD, role_templates=PULSEBOARD_ROLE_TEMPLATES
)

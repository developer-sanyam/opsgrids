"""The projects permission catalogue and role templates, for the `projects` product.

Mirrors account-, employee- and itsm-service's catalogue, and for the same reason it is shaped
the way it is: in the system this platform replaces, an admin role was "every permission that
exists", so the moment another product registered its permissions the admin silently held them
too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's own
catalogue, never "all permissions" (CLAUDE.md).
"""

from __future__ import annotations

from platform_core.catalogue import (
    PermissionCatalogue,
    PermissionSpec,
    ProductRegistration,
    RoleTemplate,
    validate,
)

PRODUCT = "projects"


# --------------------------------------------------------------------- projects catalogue

PROJECTS = PermissionCatalogue(
    PRODUCT,
    [
        # The portfolio. Reading has a meaningful OWN reading (the projects I own), so it
        # defaults to OWN and tops out at ORG. Writing (create / edit a project) has an OWN
        # reading too, so the write route sets no floor and the object-level check does the rest.
        PermissionSpec("portfolio", "project", "read", "See projects and the portfolio", "OWN"),
        PermissionSpec("portfolio", "project", "write", "Create and edit projects", "OWN"),
        # Milestones — the delivery checkpoints on a project.
        PermissionSpec("delivery", "milestone", "read", "See project milestones", "OWN"),
        PermissionSpec("delivery", "milestone", "write", "Create and update milestones", "OWN"),
        # The workflow board.
        PermissionSpec("board", "task", "read", "See the workflow board", "OWN"),
        PermissionSpec("board", "task", "write", "Create and move board cards", "OWN"),
        # Members — the people every owner/assignee picker needs. No OWN reading: "the people"
        # is inherently an org-wide resource, not something a caller owns a slice of.
        PermissionSpec("team", "member", "read", "See delivery-org members", "ORG"),
        PermissionSpec("team", "member", "write", "Add and edit delivery-org members", "ORG"),
    ],
)


PROJECTS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="PROJECTS_ADMIN",
        name="Delivery administrator",
        product=PRODUCT,
        description=(
            "Administers the whole delivery portfolio across the organization. Built from the "
            "projects catalogue alone — running delivery is not the same job as owning the "
            "account."
        ),
        grants=PROJECTS.all("ORG"),
    ),
    RoleTemplate(
        code="PROJECTS_MANAGER",
        name="Delivery manager",
        product=PRODUCT,
        description=(
            "Runs projects, milestones and the board org-wide: reads and writes every portfolio "
            "resource, but does not administer the product."
        ),
        grants=PROJECTS.pick(
            ("projects.portfolio.project.read", "ORG"),
            ("projects.portfolio.project.write", "ORG"),
            ("projects.delivery.milestone.read", "ORG"),
            ("projects.delivery.milestone.write", "ORG"),
            ("projects.board.task.read", "ORG"),
            ("projects.board.task.write", "ORG"),
            ("projects.team.member.read", "ORG"),
            ("projects.team.member.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="PROJECTS_MEMBER",
        name="Project member",
        product=PRODUCT,
        description=(
            "Every person with a projects seat. Reads the portfolio, milestones and board, and "
            "works their own board cards."
        ),
        is_default=True,
        grants=PROJECTS.pick(
            ("projects.portfolio.project.read", "ORG"),
            ("projects.delivery.milestone.read", "ORG"),
            ("projects.board.task.read", "ORG"),
            ("projects.board.task.write", "OWN"),
            ("projects.team.member.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


validate(PROJECTS_ROLE_TEMPLATES)


PROJECTS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=PROJECTS, role_templates=PROJECTS_ROLE_TEMPLATES
)

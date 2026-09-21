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

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "projects"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    is_dangerous: bool = False

    def code(self, product: str) -> str:
        return f"{product}.{self.module}.{self.resource}.{self.action}"


@dataclass(frozen=True, slots=True)
class Grant:
    code: str
    scope: Scope


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    code: str
    name: str
    product: str
    grants: list[Grant]
    description: str = ""
    is_default: bool = False
    cross_product: bool = False


_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def min_scope(wanted: Scope, ceiling: Scope) -> Scope:
    """Never grant wider than the permission's own ceiling."""
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


class PermissionCatalogue:
    """One product's permissions. A role builder can only see its own."""

    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        """Every permission **in this catalogue**, at `scope` — the safe "give the admin
        everything", bound to one product by construction."""
        return [Grant(s.code(self.product), min_scope(scope, s.max_scope)) for s in self.specs]

    def pick(self, *pairs: tuple[str, Scope]) -> list[Grant]:
        """Named grants, validated against this catalogue so a typo fails loudly."""
        known = set(self.codes())
        out: list[Grant] = []
        for code, scope in pairs:
            full = code if code.startswith(f"{self.product}.") else f"{self.product}.{code}"
            if full not in known:
                raise ValueError(f"{full} is not in the {self.product} catalogue")
            out.append(Grant(full, scope))
        return out


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
        PermissionSpec("workspace", "member", "read", "See delivery-org members", "ORG"),
        PermissionSpec("workspace", "member", "write", "Add and edit delivery-org members", "ORG"),
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
            ("projects.workspace.member.read", "ORG"),
            ("projects.workspace.member.write", "ORG"),
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
            ("projects.workspace.member.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    customer's delivery administrator quietly holding every CRM permission.
    """
    for template in templates:
        if template.cross_product:
            continue
        for grant in template.grants:
            product = grant.code.split(".", 1)[0]
            if product != template.product:
                raise ValueError(
                    f"Role template {template.code!r} belongs to product "
                    f"{template.product!r} but grants {grant.code!r}. "
                    "Set cross_product=True deliberately, or remove the grant."
                )


validate(PROJECTS_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


PROJECTS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=PROJECTS, role_templates=PROJECTS_ROLE_TEMPLATES
)

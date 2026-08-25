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

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "pulseboard"


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


validate(PULSEBOARD_STANDUP_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


PULSEBOARD_STANDUP_REGISTRATION = ProductRegistration(
    product=PRODUCT,
    catalogue=PULSEBOARD_STANDUP,
    role_templates=PULSEBOARD_STANDUP_ROLE_TEMPLATES,
)

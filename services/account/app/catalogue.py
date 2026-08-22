"""Permission catalogues and role templates, registered per product.

The bug this module is shaped to prevent: in the system this platform replaces, the HR
admin role was defined as *every permission that exists*. Under a shared catalogue that
would silently grant CRM, finance and billing permissions the moment those products
registered theirs.

A comment saying "remember to filter" fixes it until someone forgets. So the structure
makes it impossible instead:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's
  permission without declaring `cross_product`.

The guard is tested. Adding a wrong-product grant is a failing build, not a silent
super-grant discovered during an audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: Requires a recent second factor. Payroll approval, PII unmask, product activation.
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
    #: Only a deliberately cross-product role (the organization owner) may hold another
    #: product's permissions, and saying so is explicit.
    cross_product: bool = False


class PermissionCatalogue:
    """One product's permissions. A role builder can only see its own."""

    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        """Every permission **in this catalogue**, at `scope`.

        This is the safe form of "give the admin everything": bound to one product by
        construction, so it cannot reach another's.
        """
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


_RANK = {"OWN": 0, "TEAM": 1, "LOCATION": 2, "ORG": 3}


def min_scope(wanted: Scope, ceiling: Scope) -> Scope:
    """Never grant wider than the permission's own ceiling."""
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


# --------------------------------------------------------------------- core catalogue

CORE = PermissionCatalogue(
    "core",
    [
        PermissionSpec("org", "organization", "read", "See the organization's profile", "ORG"),
        PermissionSpec(
            "org", "organization", "write", "Change name, address, tax identifiers", "ORG", "ORG"
        ),
        PermissionSpec("org", "unit", "read", "See departments and teams", "ORG"),
        PermissionSpec("org", "unit", "write", "Create and reorganize departments", "ORG", "ORG"),
        PermissionSpec("org", "location", "read", "See sites", "ORG"),
        PermissionSpec("org", "location", "write", "Create and edit sites", "ORG", "ORG"),
        PermissionSpec("identity", "membership", "read", "See who is in the organization", "TEAM"),
        PermissionSpec(
            "identity",
            "membership",
            "write",
            "Invite, deactivate and edit members",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec("identity", "role", "read", "See roles and what they grant", "ORG"),
        PermissionSpec(
            "identity",
            "role",
            "write",
            "Create roles and change grants",
            "ORG",
            "ORG",
            is_dangerous=True,
        ),
        PermissionSpec("identity", "audit", "read", "Read the audit trail", "ORG", "ORG"),
        # The marketplace and what the organization has turned on. These live in `core`
        # rather than in a `product` product, because browsing and activating is a platform
        # capability every customer has whatever they bought — the same reason
        # product-service marks its own `core` entry `is_platform`.
        PermissionSpec("product", "catalogue", "read", "Browse the marketplace", "ORG"),
        PermissionSpec("product", "activation", "read", "See which products are active", "ORG"),
        PermissionSpec(
            "product",
            "activation",
            "write",
            "Activate and deactivate products",
            "ORG",
            "ORG",
            # Activation creates a financial commitment. `ORG_OWNER` describes itself as
            # running billing and activation, and `ORG_ADMIN` as explicitly not billing —
            # so this is granted to the owner only, and the templates below say so.
            is_dangerous=True,
        ),
        PermissionSpec("profile", "self", "read", "See your own profile", "OWN", "OWN"),
        PermissionSpec("profile", "self", "write", "Edit your own profile", "OWN", "OWN"),
    ],
)


CORE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ORG_OWNER",
        name="Organization owner",
        product="core",
        description=(
            "Runs the account: billing, product activation, members and roles. Deliberately "
            "separate from a product administrator — owning the company is not the same job as "
            "administering its HR data."
        ),
        # Safe by construction: this is core's catalogue, so it cannot reach CRM or HRMS.
        grants=CORE.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product="core",
        description="Manages people and structure, but not billing.",
        grants=CORE.pick(
            ("org.organization.read", "ORG"),
            ("org.unit.read", "ORG"),
            ("org.unit.write", "ORG"),
            ("org.location.read", "ORG"),
            ("org.location.write", "ORG"),
            ("identity.membership.read", "ORG"),
            ("identity.membership.write", "ORG"),
            ("identity.role.read", "ORG"),
            # Can see the marketplace and what is active; cannot activate, because that
            # spends money and this role is defined as the one that does not.
            ("product.catalogue.read", "ORG"),
            ("product.activation.read", "ORG"),
            ("profile.self.read", "OWN"),
            ("profile.self.write", "OWN"),
        ),
    ),
    RoleTemplate(
        code="MEMBER",
        name="Member",
        product="core",
        description="Everyone. Sees the organization chart and edits their own profile.",
        is_default=True,
        grants=CORE.pick(
            ("org.organization.read", "ORG"),
            ("org.unit.read", "ORG"),
            ("org.location.read", "ORG"),
            ("identity.membership.read", "TEAM"),
            ("profile.self.read", "OWN"),
            ("profile.self.write", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather
    than a customer's HR administrator quietly holding every CRM permission.
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


validate(CORE_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What a product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CORE_REGISTRATION = ProductRegistration(
    product="core", catalogue=CORE, role_templates=CORE_ROLE_TEMPLATES
)

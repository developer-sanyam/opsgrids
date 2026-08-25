"""The Marketing permission catalogue and role templates, for the `marketing` product.

Same construct and the same guarantee as HRMS's catalogue: a `RoleTemplate` names exactly one
product, `PermissionCatalogue.all()` can only return this catalogue's own codes, and `validate()`
refuses at import time if a template grants a foreign product's permission without declaring
`cross_product`. The guard is exercised by a test, so a wrong-product grant is a red build rather
than a super-grant discovered during an audit.

The vocabulary is deliberately small — marketing analytics is read-dominated. Reads default to
ORG because the product's data is org-wide analytics, not per-person records: an analyst is meant
to see the whole picture. Writes carry an ORG ceiling too, and the routes set an ORG floor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "marketing"


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
        """Every permission **in this catalogue**, at `scope` — the safe form of "give the admin
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


# --------------------------------------------------------------------- marketing catalogue

MARKETING = PermissionCatalogue(
    PRODUCT,
    [
        # Reading marketing analytics is an org-wide act — an analyst sees every campaign,
        # audience and the roll-ups — so it defaults to ORG. There is no meaningful "own campaign"
        # reading in this product, which is exactly why the default is not OWN.
        PermissionSpec(
            "analytics",
            "campaign",
            "read",
            "See campaigns, audiences and marketing analytics",
            "ORG",
            "ORG",
        ),
        # Creating and editing campaigns and audiences. The routes also set an ORG floor, so a
        # narrower grant cannot reach the write endpoints.
        PermissionSpec(
            "analytics",
            "campaign",
            "write",
            "Create and edit campaigns and audiences",
            "ORG",
            "ORG",
        ),
    ],
)


MARKETING_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="MARKETING_ADMIN",
        name="Marketing administrator",
        product=PRODUCT,
        description=(
            "Runs the marketing function: reads every campaign and audience and creates and edits "
            "them. Built from the Marketing catalogue alone — administering marketing is not the "
            "same job as owning the account."
        ),
        grants=MARKETING.pick(
            ("marketing.analytics.campaign.read", "ORG"),
            ("marketing.analytics.campaign.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="MARKETING_ANALYST",
        name="Marketing analyst",
        product=PRODUCT,
        description="Every person with a Marketing seat. Reads the org's campaigns and analytics.",
        is_default=True,
        grants=MARKETING.pick(
            ("marketing.analytics.campaign.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product — a red build rather than a silent
    super-grant."""
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


validate(MARKETING_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


MARKETING_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=MARKETING, role_templates=MARKETING_ROLE_TEMPLATES
)

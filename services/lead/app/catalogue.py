"""The CRM lead-intake permission catalogue and role templates, for the `crm` product.

This service is the third process in the `crm` product — customer-service (the account book,
`crm.book.*`) and deal-service (the pipeline, `crm.pipeline.*`) are the other two — and it
declares its own slice of that product's vocabulary (`crm.lead.*`) rather than importing
either's catalogue across a service boundary the platform exists to keep clean. Registering
the `crm` product here is independent of, and consistent with, the other two registering it in
their own processes: each service owns the catalogue for the permissions it serves, exactly as
the HRMS services do.

ADR-0017 records why this is a third slice of `crm` rather than a new `salescrm` product: the
onboarding cards this service's first three cards were built from (`docs/onboarding/
cards-dev-a-lead.md`) originally specified a standalone `salescrm` product with its own
manifest, written before `customer` and `deal` existed on this branch. Building that would
have made `crm` and `salescrm` two entitlements for one CRM, and split the pipeline's already
years-shipped `crm.pipeline.*` from a lead's conversion into it. This catalogue is the
corrected version.

The structure mirrors customer- and deal-service's own catalogue, and for the same reason it
is shaped the way it is: in the system this platform replaces, an admin role was "every
permission that exists", so the moment another product registered its permissions the admin
silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's
  permission without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit. A product's admin role is built from that product's
own catalogue, never "all permissions" (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "crm"


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


# --------------------------------------------------------------------- crm lead catalogue

CRM_LEADS = PermissionCatalogue(
    PRODUCT,
    [
        # Leads. Reading has a meaningful OWN reading (the leads a rep is working), so it
        # defaults to OWN and tops out at ORG — the same shape `book.company.read` and
        # `pipeline.deal.read` already take.
        PermissionSpec("lead", "lead", "read", "See leads", "OWN"),
        PermissionSpec("lead", "lead", "write", "Create and update leads", "OWN"),
        # Converting a lead creates a company (+ contact) on customer-service and a deal on
        # deal-service — a bigger act than an ordinary field edit, so it is its own
        # permission rather than folded into `write`. OWN by default: a rep converts the
        # leads they are working.
        PermissionSpec(
            "lead", "lead", "convert", "Convert a qualified lead into a company and deal", "OWN"
        ),
        # Reading a lead's real phone number or email, as opposed to the masked hint every
        # other read returns. Classified dangerous and stepped up at the route
        # (`amr={"otp"}`), exactly as customer-service treats `crm.book.contact.read_sensitive`:
        # the ordinary lead permission must not be a route to an unconverted prospect's
        # personal details, and every reveal writes an `entity.sensitive_read` audit row naming
        # the field but never its value. OWN by default (a rep reveals the leads they are
        # working), ORG at most. As ADR-0000 §5's note says, `is_dangerous` is metadata and
        # not a control — what actually constrains a reveal is the step-up on the route, the
        # scope re-applied to the one lead, and the audit row.
        PermissionSpec(
            "lead",
            "lead",
            "read_sensitive",
            "Reveal a lead's real phone number or email, audited",
            "OWN",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


CRM_LEADS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="CRM_LEADS_ADMIN",
        name="CRM administrator (leads)",
        product=PRODUCT,
        description=(
            "Administers lead intake across the organization, including the assignment rule. "
            "Built from this service's own slice of the CRM catalogue alone — administering "
            "the CRM is not the same job as owning the account."
        ),
        grants=CRM_LEADS.all("ORG"),
    ),
    RoleTemplate(
        code="CRM_LEAD_REP",
        name="Sales representative",
        product=PRODUCT,
        description=(
            "Every person with a CRM seat. Reads the whole intake list — sales works from one "
            "shared book — works and converts the leads they own, and may reveal the real "
            "phone number or email of a lead they own (stepped up and audited)."
        ),
        is_default=True,
        grants=CRM_LEADS.pick(
            ("crm.lead.lead.read", "ORG"),
            ("crm.lead.lead.write", "OWN"),
            ("crm.lead.lead.convert", "OWN"),
            ("crm.lead.lead.read_sensitive", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather
    than a customer's CRM administrator quietly holding every HRMS permission.
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


validate(CRM_LEADS_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CRM_LEADS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=CRM_LEADS, role_templates=CRM_LEADS_ROLE_TEMPLATES
)

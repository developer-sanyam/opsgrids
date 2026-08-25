"""The finance permission catalogue and role templates, for the `finance` product.

Mirrors account- and employee-service's catalogue, and for the same reason: a product's admin
role is built from *that product's own catalogue*, never "every permission that exists". The
structure makes the wrong thing impossible rather than merely discouraged —

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import if a template holds a foreign product's permission without
  declaring `cross_product`.

The construct is copied here rather than imported from another service on purpose: a product
declares its own vocabulary, and importing another service's `catalogue` would couple this
service across the boundary the platform exists to keep clean.

Every finance read is an **organization-level** function: an AR clerk sees the whole org's
receivable book, not "their own" invoices. So every permission here defaults to ORG and tops out
at ORG, and the routes floor at ORG — there is no OWN reading of a company's books.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "finance"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "ORG"
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


# --------------------------------------------------------------------- finance catalogue

FINANCE = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec("billing", "invoice", "read", "See invoices and credit notes"),
        PermissionSpec("billing", "payment", "read", "See payments received"),
        PermissionSpec("billing", "customer", "read", "See customers and their receivables"),
        PermissionSpec("reports", "summary", "read", "See the finance overview / KPIs"),
        PermissionSpec("reports", "ledger", "read", "See the ledger / trial balance"),
        # Compliance. Reading a filing history is an ordinary finance read; *changing* the
        # organization's GST registration is not — it decides which identity every invoice is
        # filed under, so it is the one write in this catalogue and it is marked dangerous.
        PermissionSpec("compliance", "einvoice", "read", "See e-invoice filing status and history"),
        PermissionSpec(
            "compliance", "registration", "read", "See the organization's GST registration"
        ),
        PermissionSpec(
            "compliance",
            "registration",
            "write",
            "Set the organization's GST registration",
            is_dangerous=True,
        ),
    ],
)


FINANCE_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="FINANCE_ADMIN",
        name="Finance administrator",
        product=PRODUCT,
        description=(
            "Runs the receivable book for the organization: every invoice, payment, customer "
            "position and report, at ORG. Deliberately built from the finance catalogue alone."
        ),
        grants=FINANCE.all("ORG"),
    ),
    RoleTemplate(
        code="FINANCE_VIEWER",
        name="Finance viewer",
        product=PRODUCT,
        description="Read-only access to the finance book. The default finance seat.",
        is_default=True,
        grants=FINANCE.pick(
            ("finance.billing.invoice.read", "ORG"),
            ("finance.billing.payment.read", "ORG"),
            ("finance.billing.customer.read", "ORG"),
            ("finance.reports.summary.read", "ORG"),
            ("finance.reports.ledger.read", "ORG"),
            # A viewer sees whether a document is filed; they cannot change who it is filed as.
            ("finance.compliance.einvoice.read", "ORG"),
            ("finance.compliance.registration.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product. Asserted by a test, so the
    failure mode is a red build rather than a finance admin quietly holding CRM permissions."""
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


validate(FINANCE_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


FINANCE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=FINANCE, role_templates=FINANCE_ROLE_TEMPLATES
)

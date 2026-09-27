"""The HRMS permission catalogue and role templates, for the `hrms` product.

Mirrors account-service's catalogue, and for the same reason it is shaped the way it is: in
the system this platform replaces, the HR admin role was "every permission that exists", so
the moment another product registered its permissions the HR admin silently held them too.

The structure makes that impossible rather than merely discouraged:

* a `RoleTemplate` names exactly one product;
* `PermissionCatalogue.all()` can only return that catalogue's own codes;
* and `validate()` refuses at import time if a template holds a foreign product's permission
  without declaring `cross_product`.

The guard is exercised by a test, so a wrong-product grant is a red build rather than a
super-grant discovered during an audit.

The catalogue construct is copied here rather than imported from account-service on purpose:
a product declares its own vocabulary, and importing account's `catalogue` module would couple
this service to another service's code across a boundary the platform exists to keep clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "hrms"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: Metadata, not a control (ADR-0000 §5 note): nothing consults it at request time. What makes
    #: an action need a one-time code is the route's `require(..., amr={"otp"})`.
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
    #: Only a deliberately cross-product role may hold another product's permissions, and
    #: saying so is explicit.
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
        """Every permission **in this catalogue**, at `scope`.

        The safe form of "give the admin everything": bound to one product by construction, so
        it cannot reach another's.
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


# --------------------------------------------------------------------- hrms catalogue

HRMS = PermissionCatalogue(
    PRODUCT,
    [
        # Reading an employment record has a meaningful "own" reading — everyone can see their
        # own — so it defaults to OWN and may widen to ORG. Writing has no self-service reading
        # in this slice (people do not edit their own employment record), so the routes set an
        # ORG floor; the permission itself still tops out at ORG.
        PermissionSpec("people", "employee", "read", "See employee records", "OWN", "ORG"),
        PermissionSpec(
            "people",
            "employee",
            "write",
            "Create, edit and transition employee records",
            "OWN",
            "ORG",
        ),
        # Unmasking sensitive PII is a distinct permission, never folded into `read`: the wildcard
        # match keeps the dot, so `hrms.people.employee.read` can never satisfy
        # `...read_sensitive`. `is_dangerous` marks it as such; the route is what enforces the
        # step-up, by demanding a token whose `amr` includes `otp` (`amr={"otp"}` — a member
        # gets one from `POST /auth/mfa/step-up`, ADR-0023). It defaults to OWN so a person can see
        # their own full details, and tops out at ORG for an HR administrator.
        PermissionSpec(
            "people",
            "employee",
            "read_sensitive",
            "Unmask an employee's sensitive PII (bank, government identifiers, contact)",
            "OWN",
            "ORG",
            is_dangerous=True,
        ),
    ],
)


HRMS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="HR_ADMIN",
        name="HR administrator",
        product=PRODUCT,
        description=(
            "Administers people data across the organization: reads every employee and creates, "
            "edits and transitions their records. Deliberately built from the HRMS catalogue "
            "alone — administering HR is not the same job as owning the account."
        ),
        grants=HRMS.pick(
            ("hrms.people.employee.read", "ORG"),
            ("hrms.people.employee.write", "ORG"),
            # Administering people includes unmasking their details across the organization — but
            # only ever behind the `otp` step-up the route demands (`amr={"otp"}`).
            ("hrms.people.employee.read_sensitive", "ORG"),
        ),
    ),
    RoleTemplate(
        code="EMPLOYEE",
        name="Employee",
        product=PRODUCT,
        description="Every person with an HRMS seat. Sees their own employment record.",
        is_default=True,
        grants=HRMS.pick(
            ("hrms.people.employee.read", "OWN"),
            # A person may unmask their *own* full details — at OWN, so it reaches nobody else's.
            ("hrms.people.employee.read_sensitive", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    customer's HR administrator quietly holding every CRM permission.
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


validate(HRMS_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


HRMS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=HRMS, role_templates=HRMS_ROLE_TEMPLATES
)

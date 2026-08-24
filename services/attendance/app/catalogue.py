"""The HRMS time-and-attendance permission catalogue and role templates.

This service is a second process in the same `hrms` product as employee-service, and it
declares its own slice of that product's vocabulary — the `hrms.time.*` permissions — rather
than importing employee-service's catalogue across a service boundary the platform exists to
keep clean. Registering the `hrms` product here is independent of, and consistent with,
employee-service registering it there: each process owns the catalogue for the permissions it
serves.

The structure is copied from employee-service, and for the same reason it is shaped the way it
is: in the system this platform replaces, the HR admin role was "every permission that exists",
so the moment another product registered its permissions the HR admin silently held them too.
Here that is impossible rather than merely discouraged — a `RoleTemplate` names exactly one
product, and `validate()` refuses at import time (asserted by a test) if a template holds a
foreign product's permission without declaring `cross_product`.
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
    #: Requires a recent second factor. None here yet — a punch and a shift edit are routine.
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


# --------------------------------------------------------------------- hrms time catalogue

HRMS = PermissionCatalogue(
    PRODUCT,
    [
        # A punch has a meaningful "own" reading — everyone reads and writes their own
        # attendance — so both default to OWN and top out at ORG for an administrator who reads
        # or corrects the whole organization's.
        PermissionSpec("time", "attendance", "read", "See attendance records", "OWN", "ORG"),
        PermissionSpec(
            "time", "attendance", "write", "Punch, and correct, attendance records", "OWN", "ORG"
        ),
        # A shift is an organization-wide definition, not a personal one: everyone reads them,
        # only an administrator writes them. So both default to ORG — there is no "own shift".
        PermissionSpec("time", "shift", "read", "See shift definitions", "ORG", "ORG"),
        PermissionSpec(
            "time", "shift", "write", "Define shifts and field geo-fences", "ORG", "ORG"
        ),
    ],
)


HRMS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="HR_ADMIN",
        name="HR administrator",
        product=PRODUCT,
        description=(
            "Administers time and attendance across the organization: reads and corrects every "
            "employee's attendance, and defines the organization's shifts and field geo-fences. "
            "Deliberately built from the HRMS catalogue alone."
        ),
        grants=HRMS.pick(
            ("hrms.time.attendance.read", "ORG"),
            ("hrms.time.attendance.write", "ORG"),
            ("hrms.time.shift.read", "ORG"),
            ("hrms.time.shift.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="EMPLOYEE",
        name="Employee",
        product=PRODUCT,
        description=(
            "Every person with an HRMS seat. Punches and reads their own attendance, and sees "
            "the organization's shifts."
        ),
        is_default=True,
        grants=HRMS.pick(
            # A person punches and reads their own attendance — OWN, so it reaches nobody else's.
            ("hrms.time.attendance.read", "OWN"),
            ("hrms.time.attendance.write", "OWN"),
            # Shifts are org definitions everyone needs to see, so shift.read is granted at ORG.
            ("hrms.time.shift.read", "ORG"),
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

"""The ITSM permission catalogue and role templates, for the `itsm` product.

Mirrors account- and employee-service's catalogue, and for the same reason it is shaped the way
it is: in the system this platform replaces, an admin role was "every permission that exists",
so the moment another product registered its permissions the admin silently held them too.

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

PRODUCT = "itsm"


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


# --------------------------------------------------------------------- itsm catalogue

ITSM = PermissionCatalogue(
    PRODUCT,
    [
        # Incidents. Reading has a meaningful OWN reading (the incidents I raised), so it
        # defaults to OWN and tops out at ORG. Writing (raise, update, add a worklog) has an
        # OWN reading too — a user can raise and comment on their own ticket — so the write
        # routes set no floor and the object-level check does the rest.
        PermissionSpec("service", "incident", "read", "See incidents and their worklog", "OWN"),
        PermissionSpec(
            "service", "incident", "write", "Raise incidents, update them and add worklog", "OWN"
        ),
        # Service requests and the catalog they draw from. Catalog reads ride on request.read
        # because the catalog is the request surface's reference data.
        PermissionSpec("service", "request", "read", "See service requests and the catalog", "OWN"),
        PermissionSpec("service", "request", "write", "Raise and fulfil service requests", "OWN"),
        # The catalog itself is the organization's reference data, not anyone's own row: an
        # offering has no OWN reading, so writing it floors at ORG (the route carries the
        # matching `minimum=Scope.ORG`) while reading it still rides on request.read.
        PermissionSpec(
            "service", "catalog", "write", "Add and change catalog offerings", "ORG", "ORG"
        ),
        # CMDB. Assets are org infrastructure, so the natural reading is ORG for an agent; the
        # permission still tops out at ORG and OWN reads the assets a person owns.
        PermissionSpec("cmdb", "asset", "read", "See CMDB assets", "OWN"),
        PermissionSpec("cmdb", "asset", "write", "Create and update CMDB assets", "OWN"),
    ],
)


ITSM_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="ITSM_ADMIN",
        name="Service desk administrator",
        product=PRODUCT,
        description=(
            "Administers the whole service desk and CMDB across the organization. Built from the "
            "ITSM catalogue alone — administering IT is not the same job as owning the account."
        ),
        grants=ITSM.all("ORG"),
    ),
    RoleTemplate(
        code="ITSM_AGENT",
        name="Service desk agent",
        product=PRODUCT,
        description=(
            "Works the incident and request queues org-wide and reads the CMDB, but does not "
            "administer assets or the service catalog."
        ),
        grants=ITSM.pick(
            ("itsm.service.incident.read", "ORG"),
            ("itsm.service.incident.write", "ORG"),
            ("itsm.service.request.read", "ORG"),
            ("itsm.service.request.write", "ORG"),
            ("itsm.cmdb.asset.read", "ORG"),
        ),
    ),
    RoleTemplate(
        code="ITSM_USER",
        name="ITSM user",
        product=PRODUCT,
        description="Every person with an ITSM seat. Sees and raises their own tickets.",
        is_default=True,
        grants=ITSM.pick(
            ("itsm.service.incident.read", "OWN"),
            ("itsm.service.incident.write", "OWN"),
            ("itsm.service.request.read", "OWN"),
            ("itsm.service.request.write", "OWN"),
            ("itsm.cmdb.asset.read", "OWN"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    customer's service-desk administrator quietly holding every CRM permission.
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


validate(ITSM_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


ITSM_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=ITSM, role_templates=ITSM_ROLE_TEMPLATES
)

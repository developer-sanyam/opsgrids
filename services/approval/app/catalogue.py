"""Approval's slice of the `core` permission catalogue (ADR-0006 §11).

Approval is a platform surface, not a product (ADR-0006 §1) — its permissions therefore belong
to the **`core`** catalogue account-service already declares, not to a catalogue of their own.
Two services contributing to one product's vocabulary is the normal shape (`hrms.json` already
merges `attendance` and `employee`; ADR-0006 §11 cites it): each service declares its own slice
as data, `tools/export_permissions.py` unions grants per role-template code across every
service's manifest, and account never imports this module — the manifest is the seam.

This module is **not run** by this change. Declaring the catalogue is this slice's job; wiring
it into `libs/contracts/permissions/core.json` and rolling it out to live tenants
(ADR-0006 §11, ADR-0007) is the orchestrator's, once every service's slice exists.

The structure — and the reason it looks exactly like `account/app/catalogue.py` and
`attendance/app/catalogue.py` rather than importing either — is the same one those two modules
already state: in the system this platform replaces, the HR admin role was "every permission
that exists", so the moment a second product registered its permissions the HR admin silently
held them too. Here that is impossible rather than merely discouraged — a `RoleTemplate` names
exactly one product, and `validate()` refuses at import time (asserted by a test) if a template
holds a foreign product's permission without declaring `cross_product`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "core"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    module: str
    resource: str
    action: str
    description: str
    default_scope: Scope = "OWN"
    max_scope: Scope = "ORG"
    #: Requires a recent second factor. None here — deciding a request is routine, not a payroll
    #: approval or a PII unmask.
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
    #: saying so is explicit. None of approval's templates need it — every grant here is
    #: `core.approval.*`, which is this module's own product.
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


# --------------------------------------------------------------------- approval's core slice

APPROVAL = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "approval",
            "request",
            "read",
            "See approval requests: OWN reaches requests raised or eligible on, and the "
            "caller's own chain; ORG reaches every request and any employee's chain",
            "OWN",
            "ORG",
        ),
        PermissionSpec(
            "approval",
            "request",
            "decide",
            "Decide a pending request: OWN where the routing chain makes the caller eligible; "
            "ORG additionally decides unassigned steps",
            "OWN",
            "ORG",
        ),
        PermissionSpec(
            "approval",
            "delegation",
            "write",
            "Create and revoke delegations: OWN only where the caller is the delegator; ORG "
            "for any delegator",
            "OWN",
            "ORG",
        ),
        # No "own" reading: SLA policy is organization-wide configuration, never one person's.
        PermissionSpec(
            "approval", "policy", "write", "Edit the SLA policy for a request type", "ORG", "ORG"
        ),
    ],
)


APPROVAL_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="MEMBER",
        name="Member",
        product=PRODUCT,
        description=(
            "Everyone raises requests, decides what the routing chain sends them, and may hand "
            "over while away. Holding decide:OWN is not authority by itself — ADR-0006 §6's "
            "eligibility check is what actually decides who may answer."
        ),
        is_default=True,
        grants=APPROVAL.pick(
            ("approval.request.read", "OWN"),
            ("approval.request.decide", "OWN"),
            ("approval.delegation.write", "OWN"),
        ),
    ),
    RoleTemplate(
        code="ORG_ADMIN",
        name="Administrator",
        product=PRODUCT,
        description=(
            "The organization's administrator. SLA policy is organization configuration, not "
            "billing, so it is in scope for the role defined as people and structure."
        ),
        grants=APPROVAL.all("ORG"),
    ),
    RoleTemplate(
        code="ORG_OWNER",
        name="Organization owner",
        product=PRODUCT,
        description=(
            "Account's template already grants every `core` permission at ORG over its own "
            "specs; this is approval's contribution to that union. It is what guarantees every "
            "organization ends ADR-0007's reconcile with at least one holder of decide:ORG, so "
            "the unassigned queue always has an owner."
        ),
        grants=APPROVAL.all("ORG"),
    ),
    RoleTemplate(
        code="APPROVAL_ADMIN",
        name="Approvals administrator",
        product=PRODUCT,
        description=(
            "All four approval permissions at ORG, nothing else. HRMS's HR_ADMIN cannot be "
            "granted core.* directly (ADR-0000 §5's corollary — a product's admin role is built "
            "from that product's own catalogue), so an HR administrator who needs the unassigned "
            "queue is given this role alongside HR_ADMIN. Deliberately not the product's "
            "admin-most template: `ORG_OWNER` already holds every `core` grant, so marking this "
            "one admin-most too would be redundant and it is not auto-granted on activation."
        ),
        grants=APPROVAL.all("ORG"),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product.

    Called at import and asserted by a test, so the failure mode is a red build rather than a
    role quietly holding a foreign product's permission.
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


validate(APPROVAL_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this service hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


CORE_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=APPROVAL, role_templates=APPROVAL_ROLE_TEMPLATES
)

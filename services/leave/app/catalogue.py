"""The leave permission catalogue and role templates.

A third `hrms` process, alongside employee- and attendance-service, declaring its own slice of
that product's vocabulary — `hrms.time.leave.*` — rather than importing either sibling's
catalogue across a service boundary the platform exists to keep clean
(`tools/export_permissions.py` merges all three at export time; see its own docstring).

Decisions are never gated by a permission of their own here — the same reasoning
`attendance.catalogue` gives for `hrms.time.attendance.request`'s missing "decide" counterpart:
approving or rejecting a leave request happens on approval's own surface, guarded by
`core.approval.request.decide` (ADR-0006 §11). Creating a second, product-specific "decide leave"
permission would let an installer grant a decision power this service has no route to check —
there is no `POST /leave/requests/{id}/decision` here to guard.
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
    return wanted if _RANK[wanted] <= _RANK[ceiling] else ceiling


class PermissionCatalogue:
    def __init__(self, product: str, specs: list[PermissionSpec]) -> None:
        self.product = product
        self.specs = specs

    def codes(self) -> list[str]:
        return [s.code(self.product) for s in self.specs]

    def all(self, scope: Scope = "ORG") -> list[Grant]:
        return [Grant(s.code(self.product), min_scope(scope, s.max_scope)) for s in self.specs]

    def pick(self, *pairs: tuple[str, Scope]) -> list[Grant]:
        known = set(self.codes())
        out: list[Grant] = []
        for code, scope in pairs:
            full = code if code.startswith(f"{self.product}.") else f"{self.product}.{code}"
            if full not in known:
                raise ValueError(f"{full} is not in the {self.product} catalogue")
            out.append(Grant(full, scope))
        return out


# --------------------------------------------------------------------- hrms leave catalogue

HRMS = PermissionCatalogue(
    PRODUCT,
    [
        # Leave types are organization-wide reference data, the same shape
        # `attendance.catalogue`'s shift permissions take: everyone reads them, only an
        # administrator writes them.
        PermissionSpec(
            "time", "leave_type", "read", "See leave types and their policy", "ORG", "ORG"
        ),
        PermissionSpec(
            "time", "leave_type", "write", "Define leave types and their policy", "ORG", "ORG"
        ),
        # A balance has a meaningful "own" reading — everyone sees their own — so it defaults to
        # OWN and tops out at ORG for HR. There is no TEAM arm in this slice (see
        # `selectors.py`'s balance read for why); ORG is still a legal grant for the ceiling, a
        # caller granted TEAM here is simply refused (the standard `ForbiddenProblem`), the same
        # "ungranted by any default template, but not structurally impossible" shape
        # `hrms.people.employee.read`'s LOCATION rung has in employee-service today.
        PermissionSpec("time", "leave_balance", "read", "See leave balances", "OWN", "ORG"),
        # Posting a grant or accrual is an administrative act on someone else's ledger — never a
        # meaningful "own" write — so both default and ceiling are ORG, the same shape
        # `hrms.time.shift.write` uses for org-wide definitional data.
        PermissionSpec(
            "time", "leave_balance", "write", "Grant or adjust a leave balance", "ORG", "ORG"
        ),
        # Applying for leave, and withdrawing your own request, is self-service by construction —
        # the ceiling is OWN, so "apply on someone else's behalf" is ungrantable through the
        # normal install path, the same reasoning `hrms.time.attendance.request`'s ceiling gives.
        PermissionSpec(
            "time",
            "leave_request",
            "write",
            "Apply for leave and cancel your own request",
            "OWN",
            "OWN",
        ),
        PermissionSpec("time", "leave_request", "read", "See leave requests", "OWN", "ORG"),
        # The documented escape hatch (ADR-0006 §6's "the product's own override... stays
        # audited in its own domain") — HR force-cancels a stuck or mistaken request directly,
        # without going through approval. Mirrors `hrms.time.attendance.write`'s ORG-only
        # administrative reading.
        PermissionSpec(
            "time",
            "leave_request",
            "manage",
            "Cancel or adjust any employee's leave request directly",
            "ORG",
            "ORG",
        ),
    ],
)


HRMS_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="HR_ADMIN",
        name="HR administrator",
        product=PRODUCT,
        description=(
            "Administers leave across the organization: defines leave types, grants and adjusts "
            "balances, and reads or force-cancels any employee's request. Deliberately built "
            "from the HRMS catalogue alone."
        ),
        grants=HRMS.pick(
            ("hrms.time.leave_type.read", "ORG"),
            ("hrms.time.leave_type.write", "ORG"),
            ("hrms.time.leave_balance.read", "ORG"),
            ("hrms.time.leave_balance.write", "ORG"),
            # An HR administrator is also an employee with their own leave, so they hold the
            # self-service permission too — at OWN, its ceiling.
            ("hrms.time.leave_request.write", "OWN"),
            ("hrms.time.leave_request.read", "ORG"),
            ("hrms.time.leave_request.manage", "ORG"),
        ),
    ),
    RoleTemplate(
        code="EMPLOYEE",
        name="Employee",
        product=PRODUCT,
        description=(
            "Every person with an HRMS seat. Sees the organization's leave types, applies for "
            "and withdraws their own leave, and reads their own balance and request history."
        ),
        is_default=True,
        grants=HRMS.pick(
            ("hrms.time.leave_type.read", "ORG"),
            ("hrms.time.leave_balance.read", "OWN"),
            ("hrms.time.leave_request.write", "OWN"),
            ("hrms.time.leave_request.read", "OWN"),
        ),
    ),
]


def validate(templates: list[RoleTemplate]) -> None:
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
    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


HRMS_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=HRMS, role_templates=HRMS_ROLE_TEMPLATES
)

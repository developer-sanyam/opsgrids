"""The permission catalogue and role templates for the `support` product.

Copied in shape from employee- and account-service, and for the same load-bearing reason: a
product's admin role is built from *that product's own* catalogue, never "every permission that
exists". The structure makes the wrong-product grant impossible rather than merely discouraged —
`PermissionCatalogue.all()` can only return this catalogue's own codes, and `validate()` refuses
at import time if a template holds a foreign product's permission without declaring
`cross_product`. A test exercises the guard, so a mistake is a red build, not an audit finding.

The construct is copied rather than imported from another service on purpose: a product declares
its own vocabulary, and importing account's module would couple this service across the very
boundary the platform exists to keep clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Scope = str  # "OWN" | "TEAM" | "LOCATION" | "ORG"

PRODUCT = "support"


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
        """Every permission in this catalogue, at `scope` — the safe form of "give the admin
        everything", bound to one product by construction so it cannot reach another's."""
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


# --------------------------------------------------------------------- support catalogue
#
# The support queue is a shared surface: an agent works the whole queue, not only their own
# tickets. So both permissions top out at ORG and the routes floor reads and writes at ORG —
# there is no self-service "own tickets only" reading to protect, and a permission guarded by
# name alone would let any holder act. Per-assignee narrowing is a query filter (`?assignee=`),
# never a security boundary.
#
# The desk's *reference data* — the agent roster and the SLA policy — is a second axis, and it
# gets its own write permissions rather than riding on `ticket.write`. Everyone who works the
# queue replies and assigns; rewriting the roster or moving the SLA targets the desk is measured
# against is the administrator's job. Reading them stays under `ticket.read`, which is how the
# queue has always read them.

SUPPORT = PermissionCatalogue(
    PRODUCT,
    [
        PermissionSpec(
            "desk",
            "ticket",
            "read",
            "See the support queue: tickets, threads, agents, SLA policy and the overview",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "ticket",
            "write",
            "Open tickets, reply to them and assign them",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "agent",
            "write",
            "Add or update the agents who work the queue",
            "ORG",
            "ORG",
        ),
        PermissionSpec(
            "desk",
            "sla_policy",
            "write",
            "Set the SLA policy the queue is measured against",
            "ORG",
            "ORG",
        ),
    ],
)


SUPPORT_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="SUPPORT_ADMIN",
        name="Support administrator",
        product=PRODUCT,
        description=(
            "Runs the support desk: reads the whole queue and every thread, opens and works "
            "tickets, and maintains the desk's reference data — the agent roster and the SLA "
            "policy. Built from the support catalogue alone — running the desk is not the same "
            "job as owning the account."
        ),
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
            ("support.desk.ticket.write", "ORG"),
            ("support.desk.agent.write", "ORG"),
            ("support.desk.sla_policy.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="SUPPORT_AGENT",
        name="Support agent",
        product=PRODUCT,
        description=(
            "Works the shared queue: reads every ticket, opens them, replies and assigns. Not "
            "the roster or the SLA policy — those are the administrator's."
        ),
        is_default=True,
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
            ("support.desk.ticket.write", "ORG"),
        ),
    ),
    RoleTemplate(
        code="SUPPORT_VIEWER",
        name="Support viewer",
        product=PRODUCT,
        description="Read-only visibility into the queue and overview — no replies, no assigns.",
        grants=SUPPORT.pick(
            ("support.desk.ticket.read", "ORG"),
        ),
    ),
]


# --------------------------------------------------------------------- the guard


def validate(templates: list[RoleTemplate]) -> None:
    """Refuse a template that reaches outside its own product. Called at import and asserted by a
    test, so the failure mode is a red build rather than a silent super-grant."""
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


validate(SUPPORT_ROLE_TEMPLATES)


@dataclass(frozen=True, slots=True)
class ProductRegistration:
    """What this product hands the platform at deploy: its vocabulary and its default roles."""

    product: str
    catalogue: PermissionCatalogue
    role_templates: list[RoleTemplate] = field(default_factory=list)

    def __post_init__(self) -> None:
        validate(self.role_templates)


SUPPORT_REGISTRATION = ProductRegistration(
    product=PRODUCT, catalogue=SUPPORT, role_templates=SUPPORT_ROLE_TEMPLATES
)
